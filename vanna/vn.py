import os
from decimal import Decimal

import pandas as pd
import psycopg2
from openai import OpenAI as OpenAIClient

from vec import BM25Store

_SYSTEM_PROMPT = """You are a PostgreSQL SQL expert. Given context about the database schema, \
documentation, and similar examples, generate a valid PostgreSQL query for the user's question.

Rules:
- Use standard window functions: LAG(), LEAD(), etc. — PostgreSQL supports them natively
- Use DATE_TRUNC('month', col) instead of any toStartOfMonth() equivalent
- Use EXTRACT(day FROM col) for day-of-month, EXTRACT(year FROM col) for year
- Use CURRENT_DATE for today's date
- Use INTERVAL '1 month' syntax (with quotes) for date arithmetic
- Use NULLIF(expr, 0) for safe division
- GROUP BY must use column expressions, not aliases
- Schemas: raw (source), transformed_staging (views), transformed_marts (tables)
- For date filters use ISO string literals directly: WHERE order_date >= '2026-03-01'
- NEVER use ClickHouse functions: toDate(), today(), toStartOfMonth(), toYYYYMM(), toMonth(), toDayOfMonth()
- Return ONLY the SQL query — no explanation, no markdown code fences"""


class VannaLite:
    def __init__(self, client: OpenAIClient, model: str, store: BM25Store):
        self._client = client
        self._model = model
        self._store = store
        self._conn = None
        self._conn_kwargs = {}

    def connect_to_postgres(self, host, port, user, password, dbname):
        self._conn_kwargs = dict(host=host, port=port, user=user, password=password, dbname=dbname)
        self._conn = None

    def _get_conn(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(**self._conn_kwargs)
            self._conn.autocommit = True
        return self._conn

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
        with self._get_conn().cursor() as cur:
            cur.execute(sql)
            columns = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
        # Convert Decimal → float (psycopg2 maps PostgreSQL NUMERIC/SUM(BIGINT) to Decimal,
        # which is not JSON-serializable)
        clean_rows = [
            tuple(float(v) if isinstance(v, Decimal) else v for v in row)
            for row in rows
        ]
        return pd.DataFrame(clean_rows, columns=columns)

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
    vn.connect_to_postgres(
        host=os.environ.get('ANALYTICS_DB_HOST', 'localhost'),
        port=int(os.environ.get('ANALYTICS_DB_PORT', '5432')),
        user=os.environ.get('ANALYTICS_DB_USER', 'bi_readonly'),
        password=os.environ['ANALYTICS_DB_PASSWORD'],
        dbname=os.environ.get('ANALYTICS_DB_NAME', 'analytics'),
    )
    return vn
