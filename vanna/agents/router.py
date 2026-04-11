from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from agents._model import make_model
from vn import VannaAI


class ChatResponse(BaseModel):
    intent: Literal["explore", "semantic", "clarify"]
    text: str
    sql: Optional[str] = None
    # data/columns/row_count are NOT produced by the LLM — they come from
    # AgentDeps.result_* after the run and are injected in app.py


@dataclass
class AgentDeps:
    vanna: VannaAI
    sql_cache: dict = field(default_factory=dict)
    # Populated by explore_data tool — rows bypass the LLM entirely
    result_rows: list = field(default_factory=list)
    result_columns: list = field(default_factory=list)
    result_total_count: int = 0


agent = Agent(
    model=make_model(),
    model_settings={"max_tokens": 8192},
    deps_type=AgentDeps,
    output_type=ChatResponse,
    instructions="""You are a data exploration assistant. Route every question to exactly one tool:

- explore_data  : any question asking for data, numbers, trends, comparisons, rankings, breakdowns
- answer_semantic: definitional or conceptual questions (what does X mean, explain Y, how is Z calculated) AND narrative summaries of previous results (key takeaways, summary, insights, what does this mean, conclusion)
- clarify       : ambiguous input, single words, gibberish, reactions ("wow", "ok", "test", "8")

Always call exactly one tool and return a ChatResponse with the correct intent.

IMPORTANT for explore_data: You only receive column names and row count — NOT the actual data values.
Write a 1-sentence summary describing what the query shows (e.g. "Here is the monthly revenue breakdown by category and city.").
Do NOT invent or guess specific numbers — the actual data is shown directly to the user.
""",
)


@agent.tool
async def explore_data(ctx: RunContext[AgentDeps], question: str) -> dict:
    """Run a data question: generate SQL, execute it, return metadata only."""
    cache_key = question.lower().strip()
    if cache_key in ctx.deps.sql_cache:
        sql = ctx.deps.sql_cache[cache_key]
    else:
        sql = ctx.deps.vanna.generate_sql_with_retry(question)
        ctx.deps.sql_cache[cache_key] = sql
    df = ctx.deps.vanna.run_sql(sql)
    rows = df.head(20).to_dict(orient='records')
    # Store rows in deps — they never enter LLM context
    ctx.deps.result_rows = rows
    ctx.deps.result_columns = list(df.columns)
    ctx.deps.result_total_count = len(df)
    return {"sql": sql, "row_count": len(df), "columns": list(df.columns)}


@agent.tool
async def answer_semantic(ctx: RunContext[AgentDeps], question: str) -> str:
    """Answer a definitional or conceptual question using schema documentation."""
    docs = ctx.deps.vanna.get_related_documentation(question)
    if docs:
        return "Schema context:\n" + "\n".join(docs[:5])
    return "No schema documentation found. Answer from general knowledge."


@agent.tool
async def clarify(ctx: RunContext[AgentDeps], clarifying_question: str) -> str:
    """Ask for clarification on an ambiguous question."""
    return clarifying_question
