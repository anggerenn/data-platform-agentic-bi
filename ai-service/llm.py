from openai import OpenAI
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from schema_context import load_schema_context
from typing import Optional


client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
)

SQL_SYSTEM_PROMPT = """You are a data analyst assistant. You have access to the following database schema:

{schema}

Your job is to convert the user's question into a single valid DuckDB SQL query.
Rules:
- Return ONLY the SQL query, no explanation, no markdown, no backticks
- Always use fully qualified table names (schema.table) exactly as shown in the schema — do NOT include [CANONICAL] or [STAGING] labels in the SQL
- Only use tables and columns listed in the schema above
- Prefer [CANONICAL] tables for all business questions — but strip the label when writing SQL
- Only use [STAGING] tables if the user explicitly asks about raw or unaggregated data
- Do not use INSERT, UPDATE, DELETE, or DROP statements
- Always cast VARCHAR date columns before using date functions: TRY_CAST(column AS DATE)
- For date formatting use DuckDB syntax: strftime('%Y-%m', TRY_CAST(column AS DATE))
- For year/month filtering use: YEAR(TRY_CAST(column AS DATE)) or MONTH(TRY_CAST(column AS DATE))
- String filters must be case-insensitive: always use ILIKE instead of = or LIKE for string comparisons, e.g. WHERE city ILIKE 'new york' not WHERE city = 'New York'
- For partial string matches use ILIKE with wildcards: WHERE city ILIKE '%york%'
- Window functions: every LAG(), LEAD(), SUM() OVER(), etc. must have balanced parentheses. Count opening and closing parentheses before returning the query — they must match exactly
- When using LAG() or LEAD() inside a larger expression, wrap each window function call in its own parentheses before combining: (LAG(SUM(col)) OVER (ORDER BY x)) not LAG(SUM(col) OVER (ORDER BY x))
- If the question cannot be answered from the schema, return exactly: SELECT 'I could not find relevant data for that question.' AS message
- If the user says they do not want to see certain columns in the chart (e.g. "no need to visualize month", "don't show city in the chart"), append a comment on the very last line of the SQL in this exact format: -- chart_exclude: col1, col2
  The comment must be on its own line after the semicolon (or after the last SQL line if no semicolon). Example:
  SELECT city, month, revenue FROM ...;
  -- chart_exclude: month
- Use conversation history to understand follow-up questions and references like 'that', 'same', 'those cities', 'now filter by', etc.
"""

INTENT_SYSTEM_PROMPT = (
    "You are an intent classifier. "
    "Classify the user's message as either 'dashboard' or 'explore'. "
    "Return 'dashboard' only if the user explicitly wants to save, create, or persist a dashboard. "
    "Return 'explore' if they just want to see or explore data. "
    "Reply with a single word: dashboard or explore."
)


def _chat_completion(system: str, user: str) -> str:
    response = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0,
    )
    return response.choices[0].message.content.strip()


def clean_sql(sql: str) -> str:
    sql = sql.strip()
    if sql.startswith("```"):
        sql = sql.split("\n", 1)[-1]
        sql = sql.rsplit("```", 1)[0]
    return sql.strip()


def generate_sql(question: str, history: Optional[list[dict]] = None) -> str:
    schema = load_schema_context()
    messages = [{"role": "system", "content": SQL_SYSTEM_PROMPT.format(schema=schema)}]

    # Inject last 10 turns of conversation history for follow-up understanding
    if history:
        for turn in history[-10:]:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": question})

    response = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=messages,
        temperature=0,
    )
    return clean_sql(response.choices[0].message.content.strip())


def classify_intent(message: str) -> str:
    result = _chat_completion(INTENT_SYSTEM_PROMPT, message)
    return "dashboard" if "dashboard" in result.lower() else "explore"


if __name__ == "__main__":
    print("--- generate_sql ---")
    print(generate_sql("What are the top 5 cities by total revenue?"))

    print("\n--- classify_intent ---")
    print(classify_intent("Create a dashboard for top cities by revenue"))
    print(classify_intent("What are the top 5 cities by total revenue?"))