import os
from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider

from vn import MyVanna


class ChatResponse(BaseModel):
    intent: Literal["explore", "semantic", "clarify"]
    text: str
    sql: Optional[str] = None
    # data/columns/row_count are NOT produced by the LLM — they come from
    # AgentDeps.result_* after the run and are injected in app.py


@dataclass
class AgentDeps:
    vanna: MyVanna
    # Populated by explore_data tool — rows bypass the LLM entirely
    result_rows: list = field(default_factory=list)
    result_columns: list = field(default_factory=list)


agent = Agent(
    model=OpenAIModel(
        'deepseek-chat',
        provider=OpenAIProvider(
            base_url='https://api.deepseek.com',
            api_key=os.environ.get('DEEPSEEK_API_KEY', ''),
        ),
    ),
    model_settings={"max_tokens": 8192},
    deps_type=AgentDeps,
    output_type=ChatResponse,
    instructions="""You are a data exploration assistant. Route every question to exactly one tool:

- explore_data  : any question asking for data, numbers, trends, comparisons, rankings, breakdowns
- answer_semantic: definitional or conceptual questions (what does X mean, explain Y, how is Z calculated)
- clarify       : ambiguous input, single words, gibberish, reactions ("wow", "ok", "test", "8")

Always call exactly one tool and return a ChatResponse with the correct intent.
""",
)


@agent.tool
async def explore_data(ctx: RunContext[AgentDeps], question: str) -> dict:
    """Run a data question: generate SQL, execute it, return metadata only."""
    sql = ctx.deps.vanna.generate_sql(question)
    df = ctx.deps.vanna.run_sql(sql)
    rows = df.head(500).to_dict(orient='records')
    # Store rows in deps — they never enter LLM context
    ctx.deps.result_rows = rows
    ctx.deps.result_columns = list(df.columns)
    return {"sql": sql, "row_count": len(df), "columns": list(df.columns)}


@agent.tool
async def answer_semantic(ctx: RunContext[AgentDeps], question: str) -> str:
    """Answer a definitional or conceptual question."""
    return question


@agent.tool
async def clarify(ctx: RunContext[AgentDeps], clarifying_question: str) -> str:
    """Ask for clarification on an ambiguous question."""
    return clarifying_question
