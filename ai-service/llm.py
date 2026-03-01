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

Window function rules:
- Window functions like LAG() and LEAD() must ALWAYS operate on pre-aggregated data, never on raw rows
- When computing month-over-month or period-over-period metrics, always aggregate first (GROUP BY period), then apply the window function in an outer query or CTE
- Correct pattern:
    WITH monthly AS (
        SELECT strftime('%Y-%m', TRY_CAST(date_col AS DATE)) AS month,
               SUM(value_col) AS total
        FROM schema.table
        GROUP BY 1
    )
    SELECT month, total,
           LAG(total) OVER (ORDER BY month) AS prev,
           (total - LAG(total) OVER (ORDER BY month)) / LAG(total) OVER (ORDER BY month) * 100 AS growth_pct
    FROM monthly
    ORDER BY month
- Every LAG(), LEAD(), SUM() OVER() etc. must have balanced parentheses — count opening and closing parens before returning
- Wrap each window function call in its own parentheses when combining: (LAG(col) OVER (ORDER BY x)) not LAG(col OVER (ORDER BY x))

Chart exclude rule (IMPORTANT — follow exactly):
- If the user says they do NOT want to see a column in the chart (e.g. "don't visualize month", "hide city from chart", "no need to show month in chart"), you MUST append this comment as the very last line of your output, after the SQL and after any semicolon:
  -- chart_exclude: col1, col2
- This comment is required — do not omit it when the user asks to hide chart columns
- Example of correct output when user says "show city, month, revenue but hide month from chart":
  SELECT city, strftime('%Y-%m', TRY_CAST(order_date AS DATE)) AS month, SUM(revenue) AS revenue
  FROM schema.table
  GROUP BY city, month
  ORDER BY city, month;
  -- chart_exclude: month
- The comment must appear AFTER the semicolon on its own line, nowhere else

- If the question cannot be answered from the schema, return exactly: SELECT 'I could not find relevant data for that question.' AS message

Context and clarification rules:
- Use conversation history to understand follow-up questions and references like 'that', 'same', 'those cities', 'now filter by', etc.
- ONLY carry forward filters, groupings, or table choices from a previous query if the user explicitly references the previous result using words like: 'that', 'same', 'those', 'filter that', 'also', 'now add', 'break that down', 'for the same'
- If the new question appears to be about a DIFFERENT topic, entity, or metric than the previous query AND does NOT use any of the above reference words, ask the user for clarification instead of guessing
- To ask for clarification, return exactly this format: SELECT 'CLARIFY: <your question here>' AS message
- Your clarification question should be short, conversational, and specific — e.g. "Are you still looking at New York, or do you want this for all cities?" or "Should I carry forward the electronics filter from before?"
- If the user's reply answers the clarification (e.g. "all cities", "yes keep it", "no start fresh"), use that answer to generate the correct SQL
"""

INTENT_SYSTEM_PROMPT = (
    "You are an intent classifier. "
    "Classify the user's message as either 'dashboard' or 'explore'. "
    "Return 'dashboard' only if the user explicitly wants to save, create, or persist a dashboard. "
    "Return 'explore' if they just want to see or explore data. "
    "Reply with a single word: dashboard or explore."
)

RELEVANCE_SYSTEM_PROMPT = (
    "You are a query relevance classifier for a data analytics assistant. "
    "Determine if the user's message is a genuine data or analytics question that can be answered with SQL. "
    "Return 'relevant' if the message is asking about data, metrics, trends, filters, or follow-up analytical questions. "
    "Return 'irrelevant' if the message is: random characters, nonsense, profanity, off-topic conversation, "
    "or anything that cannot reasonably be answered with a database query. "
    "When in doubt, return 'relevant'. "
    "Reply with a single word: relevant or irrelevant."
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


def is_relevant_query(message: str) -> bool:
    """
    Uses DeepSeek to determine if the message is a genuine analytics question.
    Returns False for random characters, nonsense, off-topic content.
    Called only when the fast heuristic guard is uncertain.
    """
    result = _chat_completion(RELEVANCE_SYSTEM_PROMPT, message)
    return "irrelevant" not in result.lower()


if __name__ == "__main__":
    print("--- generate_sql ---")
    print(generate_sql("What are the top 5 cities by total revenue?"))

    print("\n--- classify_intent ---")
    print(classify_intent("Create a dashboard for top cities by revenue"))
    print(classify_intent("What are the top 5 cities by total revenue?"))

    print("\n--- is_relevant_query ---")
    print(is_relevant_query("asdfgh"))
    print(is_relevant_query("top cities revenue"))