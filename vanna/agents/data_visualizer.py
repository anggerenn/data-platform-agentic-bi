"""
Data Visualizer Agent.

Two-layer design:
  1. analyze_result() + match_catalog() — deterministic, no LLM, builds a shortlist
  2. Agent — picks the best chart from the shortlist and assigns columns

Widget mode  : get_chart_spec(columns, rows, question)
Dashboard mode: get_chart_spec(columns, rows, question, dashboard="lightdash")
               restricts catalog to charts the target BI tool can render natively.
"""
import os
from typing import Optional

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider

from chart_catalog import CHART_CATALOG, analyze_result, match_catalog


class ChartSpec(BaseModel):
    type: Optional[str] = None   # key in CHART_CATALOG, or None
    x: Optional[str] = None
    y: Optional[str] = None
    y_cols: Optional[list] = None
    group: Optional[str] = None
    title: Optional[str] = None


def _build_model() -> OpenAIModel:
    return OpenAIModel(
        'deepseek-chat',
        provider=OpenAIProvider(
            base_url='https://api.deepseek.com',
            api_key=os.environ.get('DEEPSEEK_API_KEY', ''),
        ),
    )


def _build_instructions(options: list) -> str:
    menu = "\n".join(
        f"  - {name}: {CHART_CATALOG[name]['description']}"
        for name in options
    )
    return f"""You choose the best chart for a query result.

Available charts (already filtered to what the data can support):
{menu}

Rules:
- Pick the chart that best communicates the answer to the user's question.
- Assign columns to x, y, group using only the column names from the metadata — do not invent names.
- line / area  : x=date col, y=primary numeric col, y_cols=all numeric cols.
- grouped_bar  : x=first cat col, y=numeric col, group=second cat col.
- scatter      : x=first numeric col, y=second numeric col.
- pie          : x=cat col (labels), y=numeric col (values).
- big_number   : y=the numeric col (x and group unused).
- Set type=null only if no chart from the list would add value.
- Write a concise title (5 words max).
"""


async def get_chart_spec(
    columns: list,
    rows: list,
    question: str = "",
    dashboard: Optional[str] = None,
) -> ChartSpec:
    """
    Return a ChartSpec for the given query result.

    Pass dashboard="lightdash" to restrict to dashboard-compatible charts only.
    Never raises — returns no-chart on error.
    """
    try:
        meta = analyze_result(columns, rows)
        options = match_catalog(meta, dashboard=dashboard)

        if not options:
            return ChartSpec()

        if len(options) == 1:
            # Only one viable chart — assign columns deterministically, skip LLM
            return _auto_assign(options[0], meta)

        agent = Agent(
            model=_build_model(),
            output_type=ChartSpec,
            instructions=_build_instructions(options),
        )

        prompt = (
            f"Question: {question}\n"
            f"Row count: {meta['row_count']}\n"
            f"Numeric cols: {meta['num_cols']}\n"
            f"Date cols: {meta['date_cols']}\n"
            f"Categorical cols: {meta['cat_cols']}\n"
            f"Compatible charts: {options}"
        )
        result = await agent.run(prompt)
        return result.output

    except Exception:
        return ChartSpec()


def _auto_assign(chart_type: str, meta: dict) -> ChartSpec:
    """Assign columns deterministically when only one chart type is viable."""
    num = meta["num_cols"]
    date = meta["date_cols"]
    cat = meta["cat_cols"]

    if chart_type == "big_number":
        return ChartSpec(type="big_number", y=num[0] if num else None)
    if chart_type in ("line", "area"):
        return ChartSpec(type=chart_type, x=date[0], y=num[0], y_cols=num)
    if chart_type == "grouped_line":
        return ChartSpec(type="grouped_line", x=date[0], y=num[0], group=cat[0])
    if chart_type == "bar":
        return ChartSpec(type="bar", x=cat[0], y=num[0])
    if chart_type == "grouped_bar":
        return ChartSpec(type="grouped_bar", x=cat[0], y=num[0], group=cat[1])
    if chart_type == "scatter":
        return ChartSpec(type="scatter", x=num[0], y=num[1])
    if chart_type == "pie":
        return ChartSpec(type="pie", x=cat[0], y=num[0])
    if chart_type == "heatmap":
        return ChartSpec(type="heatmap", x=cat[0], y=cat[1], group=num[0])
    return ChartSpec()
