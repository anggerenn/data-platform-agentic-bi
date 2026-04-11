import os
from decimal import Decimal

import pandas as pd
import psycopg2
from openai import OpenAI as OpenAIClient
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
- Use ROUND((expr)::NUMERIC, 2) to round — cast the ENTIRE expression to NUMERIC before ROUND; PostgreSQL ROUND(double precision, int) is not supported
- GROUP BY must use column expressions, not aliases and NEVER aggregate functions
- For "how many customers meet a condition" use COUNT(*) FROM (SELECT customer_id FROM ... GROUP BY customer_id HAVING ...) sub — never GROUP BY an aggregate
- Schemas: raw (source), transformed_staging (views), transformed_marts (tables)
- The ONLY tables that exist are: transformed_marts.daily_sales and transformed_staging.stg_orders
- NEVER invent table names — do NOT use transformed_marts.orders, transformed_staging.orders, or any other table not listed above
- For date filters use ISO string literals directly: WHERE order_date >= '2026-03-01'
- NEVER use ClickHouse functions: toDate(), today(), toStartOfMonth(), toYYYYMM(), toMonth(), toDayOfMonth()
- Return ONLY the SQL query — no explanation, no markdown code fences"""


class VannaAI(ChromaDB_VectorStore, OpenAI_Chat):
    def __init__(self, client=None, config=None):
        ChromaDB_VectorStore.__init__(self, config=config)
        OpenAI_Chat.__init__(self, client=client, config=config)
        self._conn = None
        self._conn_kwargs = {}

    def get_sql_prompt(self, initial_prompt=None, question=None, **kwargs):
        return super().get_sql_prompt(
            initial_prompt=_SYSTEM_PROMPT,
            question=question,
            **kwargs,
        )

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

    def validate_sql(self, sql: str) -> tuple[bool, str]:
        """Run EXPLAIN to check SQL validity without fetching data."""
        try:
            with self._get_conn().cursor() as cur:
                cur.execute(f"EXPLAIN {sql}")
            return True, ""
        except Exception as e:
            return False, str(e)

    def generate_sql_with_retry(self, question: str, max_attempts: int = 3) -> str:
        """Generate SQL, validate with EXPLAIN, retry with error context on failure."""
        prompt = question
        last_error = ""
        for attempt in range(max_attempts):
            sql = self.generate_sql(prompt)
            ok, error = self.validate_sql(sql)
            if ok:
                return sql
            last_error = error
            print(f"[vanna] SQL attempt {attempt + 1} failed: {error}")
            if attempt < max_attempts - 1:
                prompt = (
                    f"{question}\n\n"
                    f"Previous SQL attempt failed with error: {error}\n"
                    f"SQL was:\n{sql}\n"
                    f"Generate a corrected SQL query."
                )
        raise ValueError(f"SQL generation failed after {max_attempts} attempts. Last error: {last_error}")


def get_vanna() -> VannaAI:
    client = OpenAIClient(
        api_key=os.environ['DEEPSEEK_API_KEY'],
        base_url='https://api.deepseek.com',
    )
    vn = VannaAI(
        client=client,
        config={
            'model': os.environ.get('VANNA_MODEL', 'deepseek-chat'),
            'path': os.path.expanduser(os.environ.get('CHROMA_PATH', '~/data/vanna-chroma')),
        },
    )
    vn.connect_to_postgres(
        host=os.environ['ANALYTICS_DB_HOST'],
        port=int(os.environ.get('ANALYTICS_DB_PORT', '5432')),
        user=os.environ.get('ANALYTICS_DB_USER', 'bi_readonly'),
        password=os.environ['ANALYTICS_DB_PASSWORD'],
        dbname=os.environ.get('ANALYTICS_DB_NAME', 'analytics'),
    )
    return vn
