from typing import Literal, Optional

from pydantic import BaseModel, model_validator
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage

from agents._model import make_model


class PRD(BaseModel):
    title: str
    problem_statement: str
    objective: str
    audience: str
    metrics: list[str]              # what you measure: aggregations (revenue, count, %)
    dimensions: list[str] = []      # how you slice: grouping fields (city, category, date)
    metric_definitions: dict[str, str] = {}  # metric name → business definition (for ambiguous terms)
    action_items: list[str]


class DPMResponse(BaseModel):
    status: Literal["clarifying", "complete"]
    message: str

    @model_validator(mode='after')
    def prd_required_when_complete(self):
        if self.status == 'complete' and self.prd is None:
            raise ValueError("prd must be populated when status is 'complete'")
        return self
    prd: Optional[PRD] = None


def _make_agent(exploration_summary: str) -> Agent:
    return Agent(
        model=make_model(),
        model_settings={"max_tokens": 4096},
        output_type=DPMResponse,
        instructions=f"""You are a Data Product Manager assistant helping design a Lightdash dashboard.

The user has just explored this data:
{exploration_summary}

Ask ONE short question at a time, in this exact order. Do NOT skip any:
1. Problem statement — what pain point does this solve?
2. Business objective — what decision/outcome does it enable? (sanity-check vs problem statement)
3. Target audience — who will use it?
4. Key metrics AND dimensions — what numbers matter most (metrics = aggregations: revenue, count, %)
   AND how should they be sliced (dimensions = grouping fields: by city, by category, by date)?
5. Metric definitions — for any metric that contains ambiguous terms (active, inactive, churn, at risk,
   retention, leaderboard, top, high-value, engaged, dormant), ask the user to define each one.
   Example: "You mentioned 'active customer' and 'churn' — how do you define these?
   e.g. active = ordered in last 30 days, churned = no order in 90 days"
   If ALL metrics from Q4 are unambiguous (e.g. just "total revenue", "order count"), skip this question.
6. Desired actions — what should viewers DO after seeing the dashboard?

When generating the PRD:
- Copy the user's metric names VERBATIM from their Q4 answer — do not paraphrase, merge, or rename them.
- Copy the user's dimension names VERBATIM from their Q4 answer.
- Put aggregations in metrics (e.g. "Total Revenue", "Order Count").
- Put grouping fields in dimensions (e.g. "City", "Category", "Order Date").
- Do NOT mix them — customer_id is a dimension, not a metric.
- Populate metric_definitions with the user's exact definitions from Q5 (metric name → definition string).
  Only include metrics that the user explicitly defined. Leave empty if Q5 was skipped.
- The title must directly reflect the dashboard's primary purpose in ≤6 words.

You MUST receive an answer to all required questions before generating the PRD.
Only respond with status="complete" after the last required question is answered.
Start by acknowledging the explored data and asking question 1.
Keep messages short and conversational.""",
    )


async def run_dpm(
    user_message: str,
    exploration_summary: str,
    history: list[ModelMessage],
) -> tuple[DPMResponse, list[ModelMessage]]:
    ag = _make_agent(exploration_summary)
    result = await ag.run(user_message, message_history=history)
    return result.output, result.new_messages()
