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
    # Computed summary stats — passed to answer_semantic, never raw rows
    result_summary: str = ""


def _summarise_rows(rows: list[dict], columns: list[str]) -> str:
    """Compute lightweight summary stats for semantic reasoning.

    Returns column-level stats (numeric: min/max/avg; categorical: distinct values).
    Raw rows never leave this function — only aggregates are returned.
    """
    if not rows or not columns:
        return ""

    total = len(rows)
    lines = [f"Result: {total} row(s), columns: {', '.join(columns)}"]

    for col in columns:
        values = [r[col] for r in rows if r.get(col) is not None]
        if not values:
            continue
        if all(isinstance(v, (int, float)) for v in values):
            mn, mx, avg = min(values), max(values), sum(values) / len(values)
            lines.append(f"  {col}: min={mn:.4g}, max={mx:.4g}, avg={avg:.4g}")
        else:
            distinct = list(dict.fromkeys(str(v) for v in values))
            if len(distinct) <= 8:
                lines.append(f"  {col}: {', '.join(distinct)}")
            else:
                lines.append(
                    f"  {col}: {len(distinct)} distinct values"
                    f" (sample: {', '.join(distinct[:3])}…)"
                )

    return "\n".join(lines)


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
    ctx.deps.result_summary = _summarise_rows(rows, list(df.columns))
    return {"sql": sql, "row_count": len(df), "columns": list(df.columns)}


@agent.tool
async def answer_semantic(ctx: RunContext[AgentDeps], question: str) -> str:
    """Answer a conceptual question or summarise previous results using schema docs + data stats."""
    parts = []

    docs = ctx.deps.vanna.get_related_documentation(question)
    if docs:
        parts.append("Schema context:\n" + "\n".join(docs[:5]))

    if ctx.deps.result_summary:
        parts.append("Previous query result statistics:\n" + ctx.deps.result_summary)

    if not parts:
        return "No schema documentation or result data available. Answer from general knowledge."
    return "\n\n".join(parts)


@agent.tool
async def clarify(ctx: RunContext[AgentDeps], clarifying_question: str) -> str:
    """Ask for clarification on an ambiguous question."""
    return clarifying_question
