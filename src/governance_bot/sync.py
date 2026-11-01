"""Atomic incremental source indexing, sharing the RAG chunk representation."""
from contextlib import closing
from dataclasses import asdict
import hashlib
import json
import math
import sqlite3
import time

from .ingestion import chunks

CHUNKER_VERSION = 'heading-characters-1800-200-v1'


def _listing(source):
    result = {}
    for metadata in source.list_documents():
        if metadata.source_id != source.source_id or metadata.document_id in result:
            raise ValueError('Invalid or duplicate source document identity')
        result[metadata.document_id] = asdict(metadata)
    return result


def sync_source(rag, source):
    """All-or-nothing sync. A SQLite write reservation prevents concurrent writers.

    Keep network calls inside the reservation; readers still see the last commit.
    Use a separate database from the legacy full-snapshot local ingest command.
    """
    started = time.perf_counter()
    rag.path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(rag.path, timeout=0)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS chunks (payload TEXT, vector TEXT)')
        db.execute('''CREATE TABLE IF NOT EXISTS documents (
            source_id TEXT NOT NULL, document_id TEXT NOT NULL,
            metadata TEXT NOT NULL, content_hash TEXT NOT NULL, indexed_at TEXT NOT NULL,
            PRIMARY KEY (source_id, document_id))''')
        metadata = dict(db.execute('SELECT key,value FROM metadata'))
        count = db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
        if count and metadata.get('index_kind') != 'incremental':
            raise ValueError('Local snapshot index detected; use --db data/drive.sqlite for sync')
        if metadata.get('embedding_model', rag.embedding_model) != rag.embedding_model:
            raise ValueError('Embedding model changed; sync into a new database to rebuild')
        if metadata.get('chunker_version', CHUNKER_VERSION) != CHUNKER_VERSION:
            raise ValueError('Chunking configuration changed; sync into a new database to rebuild')
        old = {doc_id: (json.loads(meta), digest, stamp) for doc_id, meta, digest, stamp in db.execute(
            'SELECT document_id,metadata,content_hash,indexed_at FROM documents WHERE source_id=?',
            (source.source_id,))}
        listed = _listing(source)  # Raises on incomplete discovery; no deletion can commit.
        cache = {}
        dimension = None
        for payload, vector in db.execute('SELECT payload,vector FROM chunks'):
            row, values = json.loads(payload), json.loads(vector)
            if dimension is None:
                dimension = len(values)
            cache[row['content_hash']] = values
        stats = dict(source_id=source.source_id, added=0, updated=0, unchanged=0,
                     removed=len(old.keys() - listed.keys()), fetched=0,
                     embedded_chunks=0, embedding_calls=0, reused_chunks=0)
        prepared, pending = {}, {}
        now = str(time.time())
        for doc_id, meta in listed.items():
            previous = old.get(doc_id)
            # Without a reliable version marker, fetch and hash on every sync.
            has_marker = any(meta.get(k) for k in ('source_version', 'last_updated', 'source_checksum'))
            if previous and previous[0] == meta and has_marker:
                stats['unchanged'] += 1
                continue
            document = source.fetch_document(doc_id)
            stats['fetched'] += 1
            if asdict(document.metadata) != meta:
                raise ValueError('Source metadata changed after discovery; retry sync')
            digest = hashlib.sha256(document.text.encode('utf-8')).hexdigest()
            if not document.text.strip() or digest != document.content_hash:
                raise ValueError('Invalid source document text or content hash')
            stats['added' if previous is None else 'updated'] += 1
            rows = []
            for section, text in chunks(document.text):
                chunk_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
                row = dict(meta, source=meta['source_url'], section=section, text=text,
                           content_hash=chunk_hash, indexed_at=now)
                rows.append(row)
                if chunk_hash in cache:
                    stats['reused_chunks'] += 1
                else:
                    pending[chunk_hash] = text
            if not rows:
                raise ValueError('Document produced no chunks; previous index preserved')
            prepared[doc_id] = (meta, digest, rows)
        pending_items = list(pending.items())
        for start in range(0, len(pending_items), 32):
            batch = pending_items[start:start + 32]
            vectors = rag.backend.embed([text for _, text in batch], rag.embedding_model)
            if len(vectors) != len(batch):
                raise ValueError('Invalid embedding response count')
            for (digest, _), vector in zip(batch, vectors):
                if not vector or (dimension is not None and len(vector) != dimension) or not all(
                        isinstance(x, (int, float)) and math.isfinite(x) for x in vector):
                    raise ValueError('Invalid embedding dimensions or values')
                dimension = len(vector)
                cache[digest] = vector
            stats['embedded_chunks'] += len(batch)
            stats['embedding_calls'] += 1
        # Reconcile only a stable pair of complete listings. Drive is not snapshot-isolated.
        if _listing(source) != listed:
            raise ValueError('Source listing changed during sync; retry without altering the index')
        for doc_id in (old.keys() - listed.keys()) | prepared.keys():
            db.execute("DELETE FROM chunks WHERE json_extract(payload,'$.source_id')=? "
                       "AND json_extract(payload,'$.document_id')=?", (source.source_id, doc_id))
            db.execute('DELETE FROM documents WHERE source_id=? AND document_id=?', (source.source_id, doc_id))
        for doc_id, (meta, digest, rows) in prepared.items():
            db.execute('INSERT INTO documents VALUES (?,?,?,?,?)',
                       (source.source_id, doc_id, json.dumps(meta), digest, now))
            db.executemany('INSERT INTO chunks(payload,vector) VALUES (?,?)',
                           [(json.dumps(row), json.dumps(cache[row['content_hash']])) for row in rows])
        db.executemany('INSERT OR REPLACE INTO metadata VALUES (?,?)',
                       [('embedding_model', rag.embedding_model), ('indexed_at', now),
                        ('chunker_version', CHUNKER_VERSION), ('index_kind', 'incremental'),
                        ('last_sync:' + source.source_id, now)])
        stats['documents'] = len(listed)
        stats['chunks'] = db.execute("SELECT COUNT(*) FROM chunks WHERE json_extract(payload,'$.source_id')=?",
                                    (source.source_id,)).fetchone()[0]
        stats['total_ms'] = round((time.perf_counter() - started) * 1000)
    return stats
