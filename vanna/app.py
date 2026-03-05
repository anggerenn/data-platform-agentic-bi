import asyncio
import dataclasses
import json
import os

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from agent import AgentDeps, agent
from vn import get_vanna

flask_app = Flask(__name__)
vn = get_vanna()

_STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')
_LIGHTDASH_URL = os.environ.get('LIGHTDASH_PUBLIC_URL', 'http://localhost:8080')

MAX_HISTORY = 20  # sliding window: keep last N messages


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


def _process_history(raw_history: list) -> list[ModelMessage]:
    """Deserialize, apply sliding window, strip rows/data, and ensure clean start."""
    if not raw_history:
        return []
    messages = list(ModelMessagesTypeAdapter.validate_python(raw_history))
    windowed = _strip_explore_rows(messages[-MAX_HISTORY:])
    return _trim_to_user_turn(windowed)


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

    history = _process_history(body.get('history', []))

    try:
        result = asyncio.run(
            agent.run(question, deps=AgentDeps(vanna=vn), message_history=history)
        )
        new_msgs = _strip_explore_rows(result.new_messages())
        return jsonify({
            **result.output.model_dump(),
            "new_messages": json.loads(ModelMessagesTypeAdapter.dump_json(new_msgs)),
        })
    except Exception as e:
        return jsonify({
            "intent": "explore",
            "text": f"Something went wrong: {e}",
            "sql": None, "data": None, "columns": None, "row_count": None,
            "new_messages": [],
        })


@flask_app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})


if __name__ == '__main__':
    flask_app.run(host='0.0.0.0', port=8084)
