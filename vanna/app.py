import asyncio
import dataclasses
import json
import os
import queue
import re
import threading
import uuid

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, stream_with_context

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from agents.planner import run_dpm, PRD
from agents.builder import run_data_modeler
from agents.lightdash import create_dashboard
from agents.housekeeper import check as housekeeper_check
from agents.instructor import generate_guide

from agents.router import AgentDeps, agent
from agents.designer import get_chart_spec
from vn import get_vanna

flask_app = Flask(__name__)
vn = get_vanna()

# Warm up ChromaDB/ONNX in background so the first user query is fast.
import threading
def _warmup():
    try:
        vn.get_similar_question_sql("total revenue")
    except Exception:
        pass
threading.Thread(target=_warmup, daemon=True).start()

_STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')
_LIGHTDASH_URL = os.environ.get('LIGHTDASH_PUBLIC_URL', 'http://localhost:8080')
_DBT_PATH = os.path.join(os.path.dirname(__file__), '..', 'dbt')

MAX_HISTORY = 20  # sliding window: keep last N messages per session

# Server-side session store: session_id → list[ModelMessage]
# Resets on container restart — acceptable for short-lived chat sessions
sessions: dict[str, list[ModelMessage]] = {}

# DPM session store: dpm_session_id → {summary, history}
dpm_sessions: dict[str, dict] = {}

# SQL cache: normalized question → sql string
# Skips generate_sql() LLM call on repeated questions
_sql_cache: dict[str, str] = {}


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
        html = f.read()
    return html, 200, {'Content-Type': 'text/html'}


@flask_app.route('/chat/stream', methods=['POST'])
def chat_stream():
    body = request.get_json()
    question = (body.get('message') or '').strip()
    if not question:
        return jsonify({"error": "message required"}), 400

    session_id = body.get('session_id') or str(uuid.uuid4())
    history = _get_session(session_id)

    def generate():
        q = queue.Queue()

        async def _run():
            try:
                deps = AgentDeps(vanna=vn, sql_cache=_sql_cache)
                result = await agent.run(question, deps=deps, message_history=history)
                output = result.output
                new_msgs = _strip_explore_rows(result.new_messages())

                rows = deps.result_rows
                columns = deps.result_columns
                chart_spec = None
                if output.intent == 'explore' and columns and rows:
                    spec = await get_chart_spec(columns, rows, question=question)
                    chart_spec = spec.model_dump()

                result_data = {
                    **output.model_dump(),
                    'data': rows,
                    'columns': columns,
                    'row_count': deps.result_total_count,
                    'chart_spec': chart_spec,
                    'session_id': session_id,
                }
                q.put(('output', result_data, new_msgs))
            except Exception as e:
                sessions.setdefault(session_id, [])
                q.put(('error', str(e)))

        threading.Thread(target=lambda: asyncio.run(_run()), daemon=True).start()

        thinking_event = json.dumps({'type': 'status', 'message': 'Thinking\u2026'})
        yield f"data: {thinking_event}\n\n"

        while True:
            item = q.get()
            if item[0] == 'text':
                yield f"data: {json.dumps({'type': 'text', 'content': item[1]})}\n\n"
            elif item[0] == 'output':
                _, result_data, new_msgs = item
                sessions[session_id] = sessions.get(session_id, []) + new_msgs
                yield f"data: {json.dumps({'type': 'result', **result_data})}\n\n"
                break
            elif item[0] == 'error':
                yield f"data: {json.dumps({'type': 'error', 'message': item[1], 'session_id': session_id})}\n\n"
                break

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'X-Accel-Buffering': 'no', 'Cache-Control': 'no-cache'},
    )


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
        output['row_count'] = deps.result_total_count

        # Enrich explore results with a server-side chart spec
        if result.output.intent == 'explore' and columns and rows:
            spec = asyncio.run(get_chart_spec(columns, rows, question=question))
            output['chart_spec'] = spec.model_dump()
        else:
            output['chart_spec'] = None

        return jsonify({**output, "session_id": session_id})
    except Exception as e:
        sessions.setdefault(session_id, [])  # register session even on failure
        return jsonify({
            "intent": "explore",
            "text": f"Something went wrong: {e}",
            "sql": None, "data": None, "columns": None, "row_count": None,
            "session_id": session_id,
        })


def extract_exploration_summary(messages: list[ModelMessage]) -> str:
    parts = []
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart):
                    parts.append(f"User asked: {part.content}")
                elif (
                    isinstance(part, ToolReturnPart)
                    and part.tool_name == 'explore_data'
                    and isinstance(part.content, dict)
                ):
                    c = part.content
                    if c.get('sql'):
                        parts.append(f"SQL: {c['sql']}")
                    if c.get('columns'):
                        parts.append(f"Columns: {', '.join(c['columns'])}")
                    if c.get('row_count') is not None:
                        parts.append(f"Rows returned: {c['row_count']}")
    return '\n'.join(parts) if parts else "No exploration data."


@flask_app.route('/dashboard/start', methods=['POST'])
def dashboard_start():
    body = request.get_json()
    session_id = (body.get('session_id') or '').strip()
    if not session_id or session_id not in sessions:
        return jsonify({"error": "valid session_id required"}), 400

    summary = extract_exploration_summary(sessions[session_id])
    dpm_session_id = str(uuid.uuid4())
    dpm_sessions[dpm_session_id] = {"summary": summary, "history": []}

    try:
        response, new_msgs = asyncio.run(run_dpm("Start", summary, []))
        dpm_sessions[dpm_session_id]["history"] = new_msgs
        if response.status == 'complete' and response.prd:
            dpm_sessions[dpm_session_id]["prd"] = response.prd.model_dump()
        return jsonify({
            "dpm_session_id": dpm_session_id,
            "status": response.status,
            "message": response.message,
            "prd": response.prd.model_dump() if response.prd else None,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@flask_app.route('/dashboard/chat', methods=['POST'])
def dashboard_chat():
    body = request.get_json()
    dpm_session_id = (body.get('dpm_session_id') or '').strip()
    user_message = (body.get('message') or '').strip()
    if not dpm_session_id or not user_message:
        return jsonify({"error": "dpm_session_id and message required"}), 400
    if dpm_session_id not in dpm_sessions:
        return jsonify({"error": "DPM session not found"}), 404

    sess = dpm_sessions[dpm_session_id]
    try:
        response, new_msgs = asyncio.run(
            run_dpm(user_message, sess["summary"], sess["history"])
        )
        sess["history"] = sess["history"] + new_msgs
        if response.status == 'complete' and response.prd:
            sess["prd"] = response.prd.model_dump()
        return jsonify({
            "dpm_session_id": dpm_session_id,
            "status": response.status,
            "message": response.message,
            "prd": response.prd.model_dump() if response.prd else None,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@flask_app.route('/dashboard/build', methods=['POST'])
def dashboard_build():
    body = request.get_json()
    dpm_session_id = (body.get('dpm_session_id') or '').strip()
    if not dpm_session_id or dpm_session_id not in dpm_sessions:
        return jsonify({"error": "valid dpm_session_id required"}), 400

    sess = dpm_sessions[dpm_session_id]
    prd_data = sess.get('prd')
    if not prd_data:
        return jsonify({"error": "No completed PRD in session"}), 400

    try:
        prd = PRD(**prd_data)

        # Housekeeper: advisory only — never blocks, user is the decision maker
        verdict = housekeeper_check(prd, vn)
        if verdict.verdict != 'none':
            housekeeper_info = {
                'housekeeper': verdict.verdict,
                'existing_name': verdict.matched_dashboard_name,
                'existing_url': verdict.matched_dashboard_url,
                'suggestion': verdict.reason,
            }
        else:
            housekeeper_info = {}

        model_result = asyncio.run(run_data_modeler(prd, _DBT_PATH))

        if model_result.needs_new_model:
            return jsonify({"needs_new_model": True, "error": "No existing model covers these metrics."})

        # Instructor: generate guide before build so it can be embedded as a markdown tile
        try:
            guide = generate_guide(prd)
            guide_info = guide.model_dump()
        except Exception:
            guide = None
            guide_info = {}

        dashboard_result = create_dashboard(prd, model_result, guide=guide)

        # Persist PRD to dbt/lightdash/prd/<slug>.json for audit + future reuse
        if not dashboard_result.get('error'):
            try:
                import re as _re
                slug = _re.sub(r'[^a-z0-9]+', '_', prd.title.lower()).strip('_')
                prd_path = os.path.join(_DBT_PATH, 'lightdash', 'prd', f'{slug}.json')
                os.makedirs(os.path.dirname(prd_path), exist_ok=True)
                with open(prd_path, 'w') as f:
                    json.dump({
                        **prd.model_dump(),
                        'built_at': __import__('datetime').datetime.utcnow().isoformat() + 'Z',
                        'dashboard_url': dashboard_result.get('url', ''),
                        'model': model_result.model_name,
                    }, f, indent=2)
            except Exception:
                pass

        return jsonify({**model_result.model_dump(), **dashboard_result, **housekeeper_info, 'guide': guide_info})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@flask_app.route('/retrain/schema', methods=['POST'])
def retrain_schema():
    """Re-parse dbt schema.yml and add new training pairs to ChromaDB.
    Called by the Prefect pipeline after dbt run.
    """
    try:
        from train_from_schema import retrain
        stats = retrain(vn)
        return jsonify({"status": "ok", **stats})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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


@flask_app.route('/export', methods=['POST'])
def export_csv():
    """Re-execute SQL and stream the full result as a CSV download."""
    body = request.get_json(silent=True) or {}
    sql = (body.get('sql') or request.form.get('sql') or '').strip()
    if not sql:
        return jsonify({"error": "sql required"}), 400

    # Strip trailing LIMIT — DeepSeek may add one for display purposes
    sql = re.sub(r'\s+LIMIT\s+\d+\s*;?\s*$', '', sql, flags=re.IGNORECASE).strip()

    try:
        df = vn.run_sql(sql)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    def generate():
        yield ','.join(df.columns) + '\n'
        for _, row in df.iterrows():
            def _fmt(v):
                if v is None:
                    return ''
                s = str(v)
                return f'"{s.replace(chr(34), chr(34)*2)}"' if (',' in s or '"' in s or '\n' in s) else s
            yield ','.join(_fmt(v) for v in row) + '\n'

    return Response(
        stream_with_context(generate()),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename="export.csv"'},
    )


@flask_app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})


if __name__ == '__main__':
    flask_app.run(host='0.0.0.0', port=8084)
