import os

import clickhouse_connect
import pandas as pd
from openai import OpenAI as OpenAIClient

from vec import BM25Store

_SYSTEM_PROMPT = """You are a ClickHouse SQL expert. Given context about the database schema, \
documentation, and similar examples, generate a valid ClickHouse SQL query for the user's question.

Rules:
- Use lagInFrame() / leadInFrame() instead of LAG() / LEAD() — ClickHouse 24.3 does not support standard window functions
- lagInFrame requires a frame spec: OVER (ORDER BY col ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
- Window functions cannot be mixed with GROUP BY in the same SELECT — use a subquery
- Use nullIf(prev, 0) for safe division in growth calculations
- GROUP BY must use column expressions, not aliases
- Return ONLY the SQL query — no explanation, no markdown code fences"""


class VannaLite:
    def __init__(self, client: OpenAIClient, model: str, store: BM25Store):
        self._client = client
        self._model = model
        self._store = store
        self._ch = None

    def connect_to_clickhouse(self, host, port, user, password, dbname, settings=None):
        self._ch = clickhouse_connect.get_client(
            host=host,
            port=port,
            username=user,
            password=password,
            database=dbname,
            settings=settings or {},
        )

    def train(self, ddl=None, documentation=None, question=None, sql=None):
        if ddl:
            self._store.add_ddl(ddl)
        if documentation:
            self._store.add_documentation(documentation)
        if question and sql:
            self._store.add_question_sql(question, sql)

    def generate_sql(self, question: str) -> str:
        ddl = self._store.get_related_ddl(question)
        docs = self._store.get_related_documentation(question)
        examples = self._store.get_similar_question_sql(question)

        parts = []
        if ddl:
            parts.append("## Schema\n" + "\n\n".join(ddl))
        if docs:
            parts.append("## Documentation\n" + "\n\n".join(docs))
        if examples:
            ex = "\n\n".join(f"Q: {e['question']}\nSQL: {e['sql']}" for e in examples)
            parts.append("## Similar Examples\n" + ex)

        user_msg = "\n\n".join(parts) + f"\n\n## Question\n{question}"

        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        )
        return resp.choices[0].message.content.strip()

    def run_sql(self, sql: str) -> pd.DataFrame:
        result = self._ch.query(sql)
        return pd.DataFrame(result.result_rows, columns=result.column_names)

    def get_related_documentation(self, query: str) -> list:
        return self._store.get_related_documentation(query)

    def get_similar_question_sql(self, query: str) -> list:
        return self._store.get_similar_question_sql(query)


def get_vanna() -> VannaLite:
    client = OpenAIClient(
        api_key=os.environ['DEEPSEEK_API_KEY'],
        base_url='https://api.deepseek.com',
    )
    store = BM25Store(
        path=os.path.expanduser(os.environ.get('BM25_PATH', '~/data/vanna-bm25')),
    )
    vn = VannaLite(
        client=client,
        model=os.environ.get('VANNA_MODEL', 'deepseek-chat'),
        store=store,
    )
    vn.connect_to_clickhouse(
        host=os.environ.get('CLICKHOUSE_HOST', 'localhost'),
        port=int(os.environ.get('CLICKHOUSE_PORT', '8123')),
        user=os.environ.get('CLICKHOUSE_USER', 'default'),
        password=os.environ['CLICKHOUSE_PASSWORD'],
        dbname='transformed_marts',
        settings={'readonly': '1'},
    )
    return vn
