from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from llm import generate_sql, classify_intent
from sql_engine import execute_query
from superset_client import get_session, get_database_id, get_or_create_dataset, create_chart, create_dashboard

router = APIRouter()

DASHBOARD_KEYWORDS = {"dashboard", "save", "create", "persist", "keep", "store"}

class ChatRequest(BaseModel):
    message: str
    table_name: str
    schema: str
    db_name: str = "DuckDB"

@router.post("/chat")
def chat(body: ChatRequest):
    try:
        message_lower = body.message.lower()
        has_keywords = any(kw in message_lower for kw in DASHBOARD_KEYWORDS)

        if has_keywords:
            intent = classify_intent(body.message)
        else:
            intent = "explore"

        sql = generate_sql(body.message)
        results = execute_query(sql)

        if intent == "dashboard":
            session = get_session()
            database_id = get_database_id(session, body.db_name)
            dataset_id = get_or_create_dataset(session, body.table_name, body.schema, database_id, sql=sql)
            chart_id = create_chart(session, body.message[:50], dataset_id)
            dashboard = create_dashboard(session, body.message[:50], [chart_id], chart_names=[body.message[:50]])
            return {
                "intent": "dashboard",
                "message": body.message,
                "sql": sql,
                "results": results,
                **dashboard,
            }

        return {
            "intent": "explore",
            "message": body.message,
            "sql": sql,
            "results": results,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))