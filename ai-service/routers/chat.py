from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import re
from llm import generate_sql, classify_intent
from sql_engine import execute_query
from superset_client import get_session, get_database_id, get_or_create_dataset, create_chart, create_dashboard

router = APIRouter()

DASHBOARD_KEYWORDS = {"dashboard", "save", "create", "persist", "keep", "store"}

class ChatRequest(BaseModel):
    query: str
    db_name: str = "DuckDB"

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
        query_lower = body.query.lower()
        has_keywords = any(kw in query_lower for kw in DASHBOARD_KEYWORDS)

        if has_keywords:
            intent = classify_intent(body.query)
        else:
            intent = "explore"

        sql = generate_sql(body.query)
        results = execute_query(sql)
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
                **dashboard,
            }

        return {
            "intent": "explore",
            "query": body.query,
            "sql": sql,
            "results": results,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))