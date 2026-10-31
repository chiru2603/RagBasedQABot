import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from .ingestion import chunks, read_document
from .providers import ABSTAIN


def cosine(a, b):
    if len(a) != len(b):
        raise ValueError('Embedding dimension mismatch; rebuild index')
    norm = math.sqrt(sum(x*x for x in a) * sum(y*y for y in b))
    return sum(x*y for x, y in zip(a, b)) / norm if norm else 0.0


class RAG:
    def __init__(self, db_path, backend, embedding_model='text-embedding-3-small'):
        self.path = Path(db_path)
        self.backend = backend
        self.embedding_model = embedding_model

    def ingest(self, directory):
        directory = Path(directory).resolve()
        if not directory.is_dir():
            raise ValueError('Document directory does not exist')
        rows = []
        files = sorted(p for p in directory.rglob('*') if p.is_file() and p.suffix.lower() in {'.md', '.txt', '.pdf'})
        if not files:
            raise ValueError('No supported files; existing index was preserved')
        for path in files:
            text = read_document(path)
            for section, content in chunks(text):
                source = path.relative_to(directory).as_posix()
                rows.append({'source': source, 'section': section, 'text': content,
                             'content_hash': hashlib.sha256(content.encode()).hexdigest()})
        if not rows:
            raise ValueError('No document text; existing index was preserved')
        vectors = []
        for start in range(0, len(rows), 32):
            vectors.extend(self.backend.embed([r['text'] for r in rows[start:start+32]], self.embedding_model))
        if len(vectors) != len(rows) or not vectors or not vectors[0]:
            raise ValueError('Invalid embedding response')
        dimensions = len(vectors[0])
        if any(len(v) != dimensions or not all(math.isfinite(x) for x in v) for v in vectors):
            raise ValueError('Invalid embedding dimensions or values')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS chunks (payload TEXT, vector TEXT)')
            # Replace the complete snapshot in one transaction, only after all API work succeeds.
            db.execute('DELETE FROM chunks')
            db.execute('DELETE FROM metadata')
            db.executemany('INSERT INTO chunks VALUES (?, ?)', [(json.dumps(r), json.dumps(v)) for r, v in zip(rows, vectors)])
            db.executemany('INSERT INTO metadata VALUES (?, ?)', [('embedding_model', self.embedding_model), ('indexed_at', str(time.time()))])
        return {'documents': len(files), 'chunks': len(rows)}

    def retrieve(self, question, k=4, min_score=-1.0):
        if not question.strip() or len(question) > 4000:
            raise ValueError('Question must contain 1–4000 characters')
        if not 1 <= k <= 10 or not -1 <= min_score <= 1:
            raise ValueError('top-k must be 1–10 and min-score must be -1–1')
        if not self.path.is_file():
            raise ValueError('Index missing; run ingest first')
        with sqlite3.connect(self.path) as db:
            metadata = dict(db.execute('SELECT key, value FROM metadata'))
            if metadata.get('embedding_model') != self.embedding_model:
                raise ValueError('Embedding model changed; rebuild index with ingest')
            stored = db.execute('SELECT payload, vector FROM chunks').fetchall()
        query = self.backend.embed([question], self.embedding_model)[0]
        ranked = sorted([(cosine(query, json.loads(v)), json.loads(p)) for p, v in stored], key=lambda x: x[0], reverse=True)
        return [dict(row, id=f'S{i}', score=score, indexed_at=metadata['indexed_at'])
                for i, (score, row) in enumerate([r for r in ranked if r[0] >= min_score][:k], 1)]

    def ask(self, question, k=4, min_score=-1.0):
        started = time.perf_counter()
        evidence = self.retrieve(question, k, min_score)
        retrieval_ms = round((time.perf_counter() - started) * 1000)
        if not evidence:
            return {'answer': ABSTAIN, 'abstained': True, 'sources': [], 'reason': 'no_evidence'}
        result, usage = self.backend.generate(question, evidence)
        answer = result.get('answer', '')
        abstained = result.get('abstained')
        if not isinstance(answer, str) or not answer.strip() or type(abstained) is not bool:
            raise ValueError('Model returned invalid answer structure')
        cited = set(re.findall(r'\[(S\d+)\]', answer))
        valid = {r['id'] for r in evidence}
        # This validates citation membership, not whether the source supports a claim.
        if cited - valid or (not abstained and not cited):
            return {'answer': ABSTAIN, 'abstained': True, 'sources': [], 'reason': 'invalid_citations'}
        return {'answer': answer, 'abstained': abstained,
                'sources': [r for r in evidence if r['id'] in cited],
                'retrieved': evidence, 'usage': usage, 'retrieval_ms': retrieval_ms,
                'total_ms': round((time.perf_counter()-started)*1000)}
