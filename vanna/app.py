import asyncio
import dataclasses
import json
import os
import uuid

from dotenv import load_dotenv
from flask import Flask, jsonify, request

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from agents.routing import AgentDeps, agent
from agents.data_visualizer import get_chart_spec
from vn import get_vanna

flask_app = Flask(__name__)
vn = get_vanna()

# Warm up ChromaDB embedding model in background so the first user query is fast.
# get_similar_question_sql() triggers ONNX model load without calling any external API.
import threading
def _warmup():
    try:
        vn.get_similar_question_sql("total revenue")
    except Exception:
        pass
threading.Thread(target=_warmup, daemon=True).start()

_STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')
_LIGHTDASH_URL = os.environ.get('LIGHTDASH_PUBLIC_URL', 'http://localhost:8080')

MAX_HISTORY = 20  # sliding window: keep last N messages per session

# Server-side session store: session_id → list[ModelMessage]
# Resets on container restart — acceptable for short-lived chat sessions
sessions: dict[str, list[ModelMessage]] = {}


def _strip_explore_rows(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Remove rows from explore_data tool returns and data from final_result args."""
    cleaned = []
    for msg in messages:
        if isinstance(msg, ModelRequest):
            new_parts = []
            for part in msg.parts:
                if (
                    isinstance(part, ToolReturnPart)
                    and part.tool_name == 'explore_data'
                    and isinstance(part.content, dict)
                    and 'rows' in part.content
                ):
                    part = dataclasses.replace(
                        part,
                        content={k: v for k, v in part.content.items() if k != 'rows'},
                    )
                new_parts.append(part)
            cleaned.append(dataclasses.replace(msg, parts=new_parts))

        elif isinstance(msg, ModelResponse):
            # Strip `data` rows from final_result ToolCallPart args to keep history lean
            new_parts = []
            for part in msg.parts:
                if isinstance(part, ToolCallPart) and part.tool_name == 'final_result':
                    try:
                        args = json.loads(part.args) if isinstance(part.args, str) else dict(part.args)
                        if 'data' in args:
                            args['data'] = None
                        part = dataclasses.replace(part, args=json.dumps(args))
                    except Exception:
                        pass
                new_parts.append(part)
            cleaned.append(dataclasses.replace(msg, parts=new_parts))

        else:
            cleaned.append(msg)

    return cleaned


def _trim_to_user_turn(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Ensure history starts at a clean user-prompt turn.

    After applying the sliding window, the slice may begin with an orphaned
    ToolReturnPart (no preceding tool_calls), which DeepSeek rejects with 400.
    Scan forward until the first ModelRequest that contains a UserPromptPart.
    """
    for i, msg in enumerate(messages):
        if isinstance(msg, ModelRequest) and any(
            isinstance(p, UserPromptPart) for p in msg.parts
        ):
            return messages[i:]
    return []


def _get_session(session_id: str) -> list[ModelMessage]:
    """Return the trimmed history for a session."""
    msgs = sessions.get(session_id, [])
    if not msgs:
        return []
    return _trim_to_user_turn(_strip_explore_rows(msgs[-MAX_HISTORY:]))


@flask_app.route('/', methods=['GET'])
def index():
    with open(os.path.join(_STATIC_DIR, 'index.html')) as f:
        html = f.read().replace('{{LIGHTDASH_URL}}', _LIGHTDASH_URL)
    return html, 200, {'Content-Type': 'text/html'}


@flask_app.route('/chat', methods=['POST'])
def chat():
    body = request.get_json()
    question = (body.get('message') or '').strip()
    if not question:
        return jsonify({"error": "message required"}), 400

    session_id = body.get('session_id') or str(uuid.uuid4())
    history = _get_session(session_id)

    try:
        deps = AgentDeps(vanna=vn)
        result = asyncio.run(
            agent.run(question, deps=deps, message_history=history)
        )
        new_msgs = _strip_explore_rows(result.new_messages())
        sessions[session_id] = sessions.get(session_id, []) + new_msgs

        output = result.output.model_dump()

        # Inject query results from deps — rows never passed through the LLM
        rows = deps.result_rows
        columns = deps.result_columns
        output['data'] = rows
        output['columns'] = columns
        output['row_count'] = len(rows)

        # Enrich explore results with a server-side chart spec
        if result.output.intent == 'explore' and columns and rows:
            spec = asyncio.run(get_chart_spec(columns, rows, question=question))
            output['chart_spec'] = spec.model_dump()
        else:
            output['chart_spec'] = None

        return jsonify({**output, "session_id": session_id})
    except Exception as e:
        return jsonify({
            "intent": "explore",
            "text": f"Something went wrong: {e}",
            "sql": None, "data": None, "columns": None, "row_count": None,
            "session_id": session_id,
        })


@flask_app.route('/feedback', methods=['POST'])
def feedback():
    body = request.get_json()
    question = (body.get('question') or '').strip()
    sql = (body.get('sql') or '').strip()
    rating = body.get('rating')  # 'up' or 'down'

    if not question or not sql or rating not in ('up', 'down'):
        return jsonify({"error": "question, sql, and rating required"}), 400

    if rating == 'up':
        try:
            vn.train(question=question, sql=sql)
            return jsonify({"status": "trained"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    else:
        from datetime import datetime, timezone
        entry = {"question": question, "sql": sql, "timestamp": datetime.now(timezone.utc).isoformat()}
        feedback_path = os.environ.get('FEEDBACK_PATH', '/data/vanna-feedback.jsonl')
        with open(feedback_path, 'a') as f:
            f.write(json.dumps(entry) + '\n')
        return jsonify({"status": "recorded"})


@flask_app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})


if __name__ == '__main__':
    flask_app.run(host='0.0.0.0', port=8084)
