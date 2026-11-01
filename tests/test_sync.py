from contextlib import closing
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from governance_bot.retrieval import RAG
from governance_bot.sources import DocumentMetadata, SourceDocument, SourceError
from governance_bot.sync import sync_source


class MemorySource:
    source_id = 'gdrive:test'

    def __init__(self):
        self.docs = {}
        self.fetched = []

    def put(self, doc_id, text, version='1', title=None):
        metadata = DocumentMetadata(self.source_id, doc_id, title or doc_id, 'text/markdown',
                                    f'https://example.test/{doc_id}', None, version, None)
        self.docs[doc_id] = SourceDocument(metadata, text, hashlib.sha256(text.encode()).hexdigest())

    def list_documents(self):
        return [d.metadata for d in self.docs.values()]

    def fetch_document(self, doc_id):
        self.fetched.append(doc_id)
        return self.docs[doc_id]


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backend = Mock()
        self.backend.embed.side_effect = lambda texts, model: [[1., 0.] for _ in texts]
        self.rag = RAG(Path(self.temp.name) / 'drive.sqlite', self.backend)
        self.source = MemorySource()
        self.source.put('access', '# Access\n14 days\n# Review\n90 days')
        sync_source(self.rag, self.source)
        self.backend.reset_mock()
        self.source.fetched.clear()

    def snapshot(self):
        with closing(sqlite3.connect(self.rag.path)) as db:
            return {table: db.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall()
                    for table in ('chunks', 'documents', 'metadata')}

    def test_unchanged_has_no_fetch_or_embedding_calls(self):
        before = self.snapshot()['chunks']
        stats = sync_source(self.rag, self.source)
        self.assertEqual(stats['unchanged'], 1)
        self.assertEqual(stats['embedded_chunks'], 0)
        self.assertEqual(self.source.fetched, [])
        self.backend.embed.assert_not_called()
        self.assertEqual(self.snapshot()['chunks'], before)

    def test_update_reuses_unchanged_chunks(self):
        self.source.put('access', '# Access\n7 days\n# Review\n90 days', '2')
        stats = sync_source(self.rag, self.source)
        self.assertEqual((stats['updated'], stats['embedded_chunks'], stats['reused_chunks']), (1, 1, 1))
        stored = str(self.snapshot()['chunks'])
        self.assertNotIn('14 days', stored)
        self.assertIn('7 days', stored)

    def test_retrieval_preserves_original_chunk_indexing_time(self):
        first = self.rag.retrieve('access')[0]
        sync_source(self.rag, self.source)
        second = self.rag.retrieve('access')[0]
        self.assertEqual(first['indexed_at'], second['indexed_at'])
        self.assertEqual(second['source'], 'https://example.test/access')
        self.assertEqual(second['document_id'], 'access')

    def test_rename_updates_citation_metadata_without_embedding(self):
        self.source.put('access', self.source.docs['access'].text, '2', 'Renamed policy')
        stats = sync_source(self.rag, self.source)
        self.assertEqual(stats['embedded_chunks'], 0)
        self.assertIn('Renamed policy', str(self.snapshot()['chunks']))

    def test_add_then_remove_and_empty_folder(self):
        self.source.put('new', '# Policy\nNew content')
        self.assertEqual(sync_source(self.rag, self.source)['added'], 1)
        del self.source.docs['new']
        self.assertEqual(sync_source(self.rag, self.source)['removed'], 1)
        self.source.docs.clear()
        self.assertEqual(sync_source(self.rag, self.source)['removed'], 1)
        self.assertEqual(self.snapshot()['chunks'], [])
        self.assertEqual(self.snapshot()['documents'], [])

    def test_embedding_failure_preserves_removals_and_old_snapshot(self):
        before = self.snapshot()
        self.source.docs.clear()
        self.source.put('new', 'New content')
        self.backend.embed.side_effect = RuntimeError('API down')
        with self.assertRaises(RuntimeError):
            sync_source(self.rag, self.source)
        self.assertEqual(self.snapshot(), before)

    def test_listing_or_fetch_failure_rolls_back(self):
        before = self.snapshot()
        self.source.list_documents = Mock(side_effect=SourceError('Incomplete listing'))
        with self.assertRaises(SourceError):
            sync_source(self.rag, self.source)
        self.assertEqual(self.snapshot(), before)
        self.source.list_documents = lambda: [replace(self.source.docs['access'].metadata, source_version='2')]
        self.source.fetch_document = Mock(side_effect=SourceError('Download failed'))
        with self.assertRaises(SourceError):
            sync_source(self.rag, self.source)
        self.assertEqual(self.snapshot(), before)

    def test_changed_final_listing_rejects_deletions(self):
        before = self.snapshot()
        original = self.source.list_documents()
        self.source.list_documents = Mock(side_effect=[[], original])
        with self.assertRaisesRegex(ValueError, 'listing changed'):
            sync_source(self.rag, self.source)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_vectors_roll_back(self):
        before = self.snapshot()
        self.source.put('new', 'New text')
        for vector in ([], [float('nan'), 1.], [1.]):
            self.backend.embed.side_effect = lambda texts, model: [vector]
            with self.assertRaises(ValueError):
                sync_source(self.rag, self.source)
            self.assertEqual(self.snapshot(), before)

    def test_other_sources_are_preserved(self):
        other = MemorySource()
        other.source_id = 'gdrive:other'
        other.put('access', 'Other source policy')
        sync_source(self.rag, other)
        self.source.docs.clear()
        sync_source(self.rag, self.source)
        payloads = [json.loads(row[0]) for row in self.snapshot()['chunks']]
        self.assertEqual([p['source_id'] for p in payloads], ['gdrive:other'])

    def test_model_mismatch_and_concurrent_writer(self):
        self.rag.embedding_model = 'different'
        with self.assertRaisesRegex(ValueError, 'model changed'):
            sync_source(self.rag, self.source)
        self.rag.embedding_model = 'text-embedding-3-small'
        with closing(sqlite3.connect(self.rag.path)) as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaises(sqlite3.OperationalError):
                sync_source(self.rag, self.source)

    def test_legacy_index_and_local_ingest_cannot_mix(self):
        docs = Path(self.temp.name) / 'docs'
        docs.mkdir()
        (docs / 'policy.md').write_text('Local policy')
        with self.assertRaisesRegex(ValueError, 'Incremental index'):
            self.rag.ingest(docs)
        other = RAG(Path(self.temp.name) / 'legacy.sqlite', self.backend)
        other.ingest(docs)
        with self.assertRaisesRegex(ValueError, 'Local snapshot'):
            sync_source(other, self.source)


if __name__ == '__main__':
    unittest.main()
