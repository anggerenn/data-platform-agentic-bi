"""
BM25-based vector store — replaces ChromaDB/ONNX.
No embedding model required; ~60MB target vs ~335MB with ChromaDB.

Storage layout (at BM25_PATH):
  documents.json  — all training data (DDL, docs, Q&A pairs)
"""
import json
import os

from rank_bm25 import BM25Okapi


class BM25Store:
    def __init__(self, path: str):
        self._path = path
        os.makedirs(path, exist_ok=True)
        self._docs_file = os.path.join(path, 'documents.json')
        self._docs = self._load()

    def _load(self) -> dict:
        if os.path.exists(self._docs_file):
            with open(self._docs_file) as f:
                return json.load(f)
        return {'ddl': [], 'documentation': [], 'sql': []}

    def _save(self):
        with open(self._docs_file, 'w') as f:
            json.dump(self._docs, f, indent=2)

    def _search(self, corpus: list, query: str, top_k: int = 5) -> list:
        if not corpus:
            return []
        tokenized = [doc.lower().split() for doc in corpus]
        index = BM25Okapi(tokenized)
        scores = index.get_scores(query.lower().split())
        ranked = sorted(zip(scores, corpus), reverse=True)
        return [doc for score, doc in ranked[:top_k] if score > 0]

    # --- write ---

    def add_ddl(self, ddl: str):
        self._docs['ddl'].append(ddl)
        self._save()

    def add_documentation(self, doc: str):
        self._docs['documentation'].append(doc)
        self._save()

    def add_question_sql(self, question: str, sql: str):
        self._docs['sql'].append({'question': question, 'sql': sql})
        self._save()

    # --- read ---

    def get_related_ddl(self, query: str, top_k: int = 5) -> list:
        return self._search(self._docs['ddl'], query, top_k)

    def get_related_documentation(self, query: str, top_k: int = 5) -> list:
        return self._search(self._docs['documentation'], query, top_k)

    def get_similar_question_sql(self, query: str, top_k: int = 5) -> list:
        entries = self._docs['sql']
        if not entries:
            return []
        questions = [e['question'] for e in entries]
        tokenized = [q.lower().split() for q in questions]
        index = BM25Okapi(tokenized)
        scores = index.get_scores(query.lower().split())
        ranked = sorted(zip(scores, range(len(entries)), entries), reverse=True)
        return [entry for score, _, entry in ranked[:top_k] if score > 0]
