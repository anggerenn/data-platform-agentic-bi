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
    ToolReturnPart,
)

from agent import AgentDeps, agent
from vn import get_vanna

flask_app = Flask(__name__)
vn = get_vanna()

_STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')
_LIGHTDASH_URL = os.environ.get('LIGHTDASH_PUBLIC_URL', 'http://localhost:8080')

MAX_HISTORY = 20  # sliding window: keep last N messages


def _strip_explore_rows(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Remove the rows payload from explore_data tool returns, keeping sql/columns/row_count."""
    cleaned = []
    for msg in messages:
        if not isinstance(msg, ModelRequest):
            cleaned.append(msg)
            continue
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
    return cleaned


def _process_history(raw_history: list) -> list[ModelMessage]:
    """Deserialize, apply sliding window, and strip row data from incoming history."""
    if not raw_history:
        return []
    messages = list(ModelMessagesTypeAdapter.validate_python(raw_history))
    return _strip_explore_rows(messages[-MAX_HISTORY:])


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

    result = asyncio.run(
        agent.run(question, deps=AgentDeps(vanna=vn), message_history=history)
    )

    new_msgs = _strip_explore_rows(result.new_messages())
    return jsonify({
        **result.output.model_dump(),
        "new_messages": json.loads(ModelMessagesTypeAdapter.dump_json(new_msgs)),
    })


@flask_app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})


if __name__ == '__main__':
    flask_app.run(host='0.0.0.0', port=8084)
