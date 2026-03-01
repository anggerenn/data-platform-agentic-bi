import re
from pydantic import BaseModel
from sql_engine import execute_query, UnsafeSQLError
from fastapi import APIRouter, HTTPException
from llm import generate_sql, classify_intent
from superset_client import get_session, get_database_id, get_or_create_dataset, create_chart, create_dashboard
from typing import Optional


router = APIRouter()

DASHBOARD_KEYWORDS = {"dashboard", "save", "create", "persist", "keep", "store"}

# ── Junk query detection ───────────────────────────────────────────────────────
# Blocklist: profanity and common nonsense patterns
_JUNK_BLOCKLIST = re.compile(
    r'(fuck|shit|bitch|ass|damn|crap|bastard|dick|piss|cock|cunt|motherfuck\w*)',
    re.IGNORECASE
)

def _is_junk_query(query: str) -> bool:
    """
    Returns True if the query should be rejected before reaching the LLM.
    Catches:
      - Profanity / nonsense blocklist matches
      - Queries with no word of ≥4 alphabetic characters (e.g. "asd", "ppp", "lo")
    Allows short but valid queries like "top 5 cities" (has "cities" ≥4 chars).
    """
    if _JUNK_BLOCKLIST.search(query):
        return True
    # Extract all purely-alphabetic tokens and check if any is ≥4 chars
    alpha_words = re.findall(r'[a-zA-Z]{4,}', query)
    return len(alpha_words) == 0


# ── chart_exclude parser ───────────────────────────────────────────────────────
_CHART_EXCLUDE_RE = re.compile(r'--\s*chart_exclude:\s*(.+)$', re.IGNORECASE | re.MULTILINE)

def extract_chart_exclude(sql: str) -> tuple[str, list[str]]:
    """
    Parses the optional -- chart_exclude: col1, col2 comment from generated SQL.
    Returns (clean_sql, excluded_columns).
    clean_sql has the comment line stripped so DuckDB never sees it.
    """
    match = _CHART_EXCLUDE_RE.search(sql)
    if not match:
        return sql, []
    excluded = [c.strip() for c in match.group(1).split(',') if c.strip()]
    clean_sql = _CHART_EXCLUDE_RE.sub('', sql).strip().rstrip(';').strip() + ';'
    # Normalise: remove any double semicolons that might result
    clean_sql = re.sub(r';+', ';', clean_sql)
    return clean_sql, excluded

# Patterns that indicate a poisoned or junk history entry — reject these
# before passing history to the LLM so they can't influence SQL generation.
# Includes any content that starts with a word that could be mistaken for SQL
# by DeepSeek (e.g. "Queried...", "SQL executed:", "Returned...") — these were
# previously used as assistant history prefixes and caused "QUERIED is not SELECT" errors.
_HISTORY_POISON_PATTERNS = re.compile(
    r'(SQL executed:|could not find relevant|cannot answer|syntax error|server error|'
    r'parser error|unrecognised sql|disallowed sql|^queried|^returned \d|'
    r'^\s*[a-z]{1,3}\s*$)',
    re.IGNORECASE
)

class ChatRequest(BaseModel):
    query: str
    db_name: str = "DuckDB"
    history: Optional[list[dict]] = None


def sanitize_history(history: Optional[list[dict]]) -> list[dict]:
    """
    Clean incoming conversation history before passing to the LLM.
    - Only allow role: user or assistant
    - Strip entries with poisoned/fallback/error content
    - Strip entries with suspiciously short content (single chars, flooding)
    - Cap at 20 entries (last 20 turns)
    """
    if not history:
        return []

    cleaned = []
    for entry in history:
        role = entry.get("role", "")
        content = str(entry.get("content", "")).strip()

        # Only valid roles
        if role not in ("user", "assistant"):
            continue

        # Skip empty
        if not content:
            continue

        # Skip very short user messages — likely flooding (single chars)
        if role == "user" and len(content) <= 2:
            continue

        # Skip poisoned assistant entries
        if role == "assistant" and _HISTORY_POISON_PATTERNS.search(content):
            continue

        # Cap individual entry length — no one needs a 10k char history entry
        content = content[:500]

        cleaned.append({"role": role, "content": content})

    # Keep only the last 20 turns
    return cleaned[-20:]


def extract_table_info(sql: str) -> tuple[str, str]:
    """
    Extract table_name and schema from generated SQL.
    Falls back to 'main' schema and 'unknown' table if not found.
    """
    # Match: FROM schema.table or JOIN schema.table
    match = re.search(r'(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)', sql, re.IGNORECASE)
    if match:
        return match.group(2), match.group(1)  # table_name, schema

    # Match: FROM table (no schema prefix)
    match = re.search(r'(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)', sql, re.IGNORECASE)
    if match:
        return match.group(1), "main"

    return "unknown", "main"


@router.post("/chat")
def chat(body: ChatRequest):
    try:
        query_lower = body.query.strip().lower()

        # ── Junk query guard ──────────────────────────────────────────────
        # Reject before hitting the LLM if:
        #   (a) matches profanity / nonsense blocklist, OR
        #   (b) contains no word with ≥4 alphabetic characters
        # This preserves short but valid queries like "top 5 cities" (has "cities")
        # while blocking "asd", "motherfucker", "ppp", etc.
        if _is_junk_query(query_lower):
            return {
                "intent": "explore",
                "query": body.query,
                "sql": None,
                "results": [{"message": "Could not find relevant data for that question."}],
                "columns": ["message"],
            }

        has_keywords = any(kw in query_lower for kw in DASHBOARD_KEYWORDS)

        if has_keywords:
            intent = classify_intent(body.query)
        else:
            intent = "explore"

        # Sanitize history server-side — never trust raw client history
        clean_history = sanitize_history(body.history)

        raw_sql = generate_sql(body.query, history=clean_history)

        # Parse and strip the optional -- chart_exclude: comment before execution.
        # The comment is injected by DeepSeek when the user asks to hide columns
        # from the chart — DuckDB must never see it.
        sql, chart_exclude_columns = extract_chart_exclude(raw_sql)

        # UnsafeSQLError is raised by validate_sql() inside execute_query()
        # for non-SELECT statements. Return 400 with a clear message.
        try:
            results = execute_query(sql)
        except UnsafeSQLError as e:
            raise HTTPException(status_code=400, detail=str(e))
        # duckdb.Error is now caught inside execute_query() and returned as a
        # fallback row — it will NOT raise here. Only truly unexpected errors reach
        # the outer except below.

        table_name, schema = extract_table_info(sql)

        if intent == "dashboard":
            session = get_session()
            database_id = get_database_id(session, body.db_name)
            dataset_id = get_or_create_dataset(session, table_name, schema, database_id, sql=sql)
            chart_id = create_chart(session, body.query[:50], dataset_id)
            dashboard = create_dashboard(session, body.query[:50], [chart_id], chart_names=[body.query[:50]])
            return {
                "intent": "dashboard",
                "query": body.query,
                "sql": sql,
                "results": results,
                "chart_exclude_columns": chart_exclude_columns,
                **dashboard,
            }

        return {
            "intent": "explore",
            "query": body.query,
            "sql": sql,
            "results": results,
            "chart_exclude_columns": chart_exclude_columns,
        }

    except HTTPException:
        raise  # re-raise explicit HTTP exceptions unchanged
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))