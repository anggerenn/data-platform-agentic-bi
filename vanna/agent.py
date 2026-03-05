import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider

from vn import MyVanna


def build_schema_context() -> str:
    """
    Reads all schema.yml files under dbt/models/ and builds a plain-text
    schema context for the agent's system prompt.

    Directory → ClickHouse database mapping uses the DBT_SCHEMA_PREFIX env var
    (default: "transformed_") combined with the subdirectory name.
    e.g. dbt/models/marts/ → transformed_marts
    """
    # Locate the dbt/models directory relative to this file
    here = Path(__file__).parent
    dbt_models_dir = here.parent / "dbt" / "models"
    schema_prefix = os.environ.get("DBT_SCHEMA_PREFIX", "transformed_")

    schema_files = sorted(dbt_models_dir.glob("**/schema.yml"))
    if not schema_files:
        return "No dbt schema files found."

    sections: list[str] = []
    for schema_file in schema_files:
        # Derive ClickHouse db name from the subdirectory (e.g. marts → transformed_marts)
        subdir = schema_file.parent.name
        db_name = f"{schema_prefix}{subdir}"

        with open(schema_file) as f:
            content = yaml.safe_load(f)

        models = (content or {}).get("models", [])
        for model in models:
            model_name = model.get("name", "")
            model_desc = model.get("description", "")
            columns = model.get("columns", [])

            col_lines = []
            for col in columns:
                col_name = col.get("name", "")
                col_desc = col.get("description", "")
                col_lines.append(f"   {col_name}" + (f" — {col_desc}" if col_desc else ""))

            section = f"Table: {db_name}.{model_name}"
            if model_desc:
                section += f"\n  Description: {model_desc}"
            if col_lines:
                section += "\n  Columns:\n" + "\n".join(col_lines)
            sections.append(section)

    return "\n\n".join(sections)


class ChatResponse(BaseModel):
    intent: Literal["explore", "semantic", "clarify"]
    text: str
    sql: Optional[str] = None
    data: Optional[list[dict]] = None
    columns: Optional[list[str]] = None
    row_count: Optional[int] = None


@dataclass
class AgentDeps:
    vanna: MyVanna


_schema_context = build_schema_context()

agent = Agent(
    model=OpenAIModel(
        'deepseek-chat',
        provider=OpenAIProvider(
            base_url='https://api.deepseek.com',
            api_key=os.environ.get('DEEPSEEK_API_KEY', ''),
        ),
    ),
    deps_type=AgentDeps,
    output_type=ChatResponse,
    instructions=f"""You are a data exploration assistant.

Available tables:
{_schema_context}

Route questions as follows:
- Data questions (counts, sums, trends, comparisons) → use explore_data tool
- Definition or semantic questions (what does X mean, explain Y) → use answer_semantic tool
- Ambiguous or unclear questions → use clarify tool

Always call exactly one tool and return a ChatResponse with the correct intent.
""",
)


@agent.tool
async def explore_data(ctx: RunContext[AgentDeps], question: str) -> dict:
    """Generate SQL for a data question and execute it against ClickHouse."""
    sql = ctx.deps.vanna.generate_sql(question)
    df = ctx.deps.vanna.run_sql(sql)
    return {
        "sql": sql,
        "columns": list(df.columns),
        "rows": df.head(500).to_dict(orient='records'),
        "row_count": len(df),
    }


@agent.tool
async def answer_semantic(ctx: RunContext[AgentDeps], question: str) -> str:
    """Answer a definitional or semantic question using schema context."""
    return question


@agent.tool
async def clarify(ctx: RunContext[AgentDeps], clarifying_question: str) -> str:
    """Ask for clarification when the user's question is ambiguous."""
    return clarifying_question
