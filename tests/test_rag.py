import tempfile
from pathlib import Path
import unittest
from governance_bot.retrieval import RAG, cosine
from governance_bot.ingestion import chunks


class FakeBackend:
    fail = False
    result = {'answer': 'Restricted access needs owner approval [S1].', 'abstained': False}
    def embed(self, texts, model):
        if self.fail:
            raise RuntimeError('simulated API failure')
        return [[1., 0.] if 'restricted' in t.lower() else [0., 1.] for t in texts]
    def generate(self, question, evidence):
        return self.result, {}


class RAGTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.docs = self.root / 'docs'
        self.docs.mkdir()
        self.doc = self.docs / 'policy.md'
        self.doc.write_text('# Access\nRestricted needs owner approval.\n# Other\nPublic data.')
        self.backend = FakeBackend()
        self.rag = RAG(self.root / 'index.sqlite', self.backend)
        self.rag.ingest(self.docs)

    def test_ranking_and_citations(self):
        result = self.rag.ask('restricted')
        self.assertFalse(result['abstained'])
        self.assertEqual(result['sources'][0]['section'], 'Access')
        self.assertEqual(result['sources'][0]['source'], 'policy.md')

    def test_invalid_and_missing_citations_fail_closed(self):
        for answer in ('Unsupported [S99].', 'Uncited answer.'):
            self.backend.result = {'answer': answer, 'abstained': False}
            self.assertEqual(self.rag.ask('restricted')['reason'], 'invalid_citations')

    def test_abstention(self):
        self.backend.result = {'answer': 'No sufficient evidence.', 'abstained': True}
        self.assertTrue(self.rag.ask('unknown')['abstained'])

    def test_rebuild_removes_old_text(self):
        self.doc.write_text('# Replacement\nPublic only.')
        self.rag.ingest(self.docs)
        rows = self.rag.retrieve('restricted')
        self.assertEqual(len(rows), 1)
        self.assertNotIn('Restricted', rows[0]['text'])

    def test_failed_embedding_preserves_snapshot(self):
        self.doc.write_text('Replacement')
        self.backend.fail = True
        with self.assertRaises(RuntimeError):
            self.rag.ingest(self.docs)
        self.backend.fail = False
        self.assertEqual(len(self.rag.retrieve('restricted')), 2)

    def test_empty_source_preserves_index(self):
        self.doc.unlink()
        with self.assertRaises(ValueError):
            self.rag.ingest(self.docs)
        self.assertEqual(len(self.rag.retrieve('restricted')), 2)

    def test_model_mismatch(self):
        self.rag.embedding_model = 'different-model'
        with self.assertRaisesRegex(ValueError, 'rebuild'):
            self.rag.retrieve('restricted')

    def test_bounds_and_dimensions(self):
        self.assertEqual(cosine([1, 0], [1, 0]), 1)
        with self.assertRaises(ValueError):
            cosine([1], [1, 2])
        self.assertTrue(all(len(body) <= 1800 for _, body in chunks('# H\n' + 'a'*9000)))
        with self.assertRaises(ValueError):
            self.rag.retrieve('')


if __name__ == '__main__':
    unittest.main()
