from openai import OpenAI
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from schema_context import load_schema_context

client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
)

SYSTEM_PROMPT = """You are a data analyst assistant. You have access to the following database schema:

{schema}

Your job is to convert the user's question into a single valid DuckDB SQL query.
Rules:
- Return ONLY the SQL query, no explanation, no markdown, no backticks
- Always use fully qualified table names (schema.table)
- Only use tables and columns listed in the schema above
- Prefer [CANONICAL] tables for all business questions
- Only use [STAGING] tables if the user explicitly asks about raw or unaggregated data
- Do not use INSERT, UPDATE, DELETE, or DROP statements
"""

def clean_sql(sql: str) -> str:
    sql = sql.strip()
    if sql.startswith("```"):
        sql = sql.split("\n", 1)[-1]  # remove first line (```sql)
        sql = sql.rsplit("```", 1)[0]  # remove closing ```
    return sql.strip()

def generate_sql(question: str) -> str:
    schema = load_schema_context()
    response = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT.format(schema=schema)},
            {"role": "user", "content": question},
        ],
        temperature=0,
    )
    raw = response.choices[0].message.content.strip()
    return clean_sql(raw)


if __name__ == "__main__":
    question = "What are the top 5 cities by total revenue?"
    sql = generate_sql(question)
    print(sql)