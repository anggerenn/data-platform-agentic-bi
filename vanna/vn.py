import os
from decimal import Decimal

import pandas as pd
import psycopg2
from openai import OpenAI as OpenAIClient, APIError
from vanna.legacy.chromadb import ChromaDB_VectorStore
from vanna.legacy.openai import OpenAI_Chat

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


class VannaAI(ChromaDB_VectorStore, OpenAI_Chat):
    def __init__(self, client=None, config=None, fallback_client=None, fallback_model=None):
        ChromaDB_VectorStore.__init__(self, config=config)
        OpenAI_Chat.__init__(self, client=client, config=config)
        self._fallback_client = fallback_client  # DeepSeek, used if primary (Gemini) fails
        self._fallback_model = fallback_model
        self._conn = None
        self._conn_kwargs = {}

    def get_sql_prompt(self, initial_prompt=None, question=None, **kwargs):
        return super().get_sql_prompt(
            initial_prompt=_SYSTEM_PROMPT,
            question=question,
            **kwargs,
        )

    def submit_prompt(self, prompt, **kwargs):
        """Try primary client (Gemini); fall back to DeepSeek on any API error."""
        try:
            return super().submit_prompt(prompt, **kwargs)
        except Exception:
            if self._fallback_client is None:
                raise
            # swap client + model name, then retry
            primary_client = self.client
            primary_model = self.config.get('model')
            self.client = self._fallback_client
            if self._fallback_model:
                self.config['model'] = self._fallback_model
            try:
                return super().submit_prompt(prompt, **kwargs)
            finally:
                self.client = primary_client
                self.config['model'] = primary_model

    def connect_to_postgres(self, host, port, user, password, dbname, **kwargs):
        self._conn_kwargs = dict(host=host, port=int(port), user=user, password=password, dbname=dbname)
        self._conn = None

    def _get_conn(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(**self._conn_kwargs)
            self._conn.autocommit = True
        return self._conn

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


def get_vanna() -> VannaAI:
    deepseek = OpenAIClient(
        api_key=os.environ['DEEPSEEK_API_KEY'],
        base_url='https://api.deepseek.com',
    )
    gemini_key = os.environ.get('GEMINI_API_KEY', '')
    if gemini_key:
        primary = OpenAIClient(
            api_key=gemini_key,
            base_url='https://generativelanguage.googleapis.com/v1beta/openai/',
        )
        fallback = deepseek
        model = 'gemini-2.0-flash'  # VANNA_MODEL is for DeepSeek path only
    else:
        primary = deepseek
        fallback = None
        model = os.environ.get('VANNA_MODEL', 'deepseek-chat')

    vn = VannaAI(
        client=primary,
        fallback_client=fallback,
        fallback_model='deepseek-chat' if fallback else None,
        config={
            'model': model,
            'path': os.path.expanduser(os.environ.get('CHROMA_PATH', '~/data/vanna-chroma')),
        },
    )
    vn.connect_to_postgres(
        host=os.environ.get('ANALYTICS_DB_HOST', 'localhost'),
        port=int(os.environ.get('ANALYTICS_DB_PORT', '5432')),
        user=os.environ.get('ANALYTICS_DB_USER', 'bi_readonly'),
        password=os.environ['ANALYTICS_DB_PASSWORD'],
        dbname=os.environ.get('ANALYTICS_DB_NAME', 'analytics'),
    )
    return vn
