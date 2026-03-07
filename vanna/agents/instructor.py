"""Instructor — generates a short dashboard guide after build.

Produces an overview, concrete use-case questions the dashboard answers,
and tips for using filters/dimensions. Triggered after every dashboard build
and also when the housekeeper finds an overlapping existing dashboard
(so the user understands what's already there).
"""
import asyncio

from pydantic import BaseModel
from pydantic_ai import Agent

from agents._model import make_model


class DashboardGuide(BaseModel):
    overview: str           # 1-2 sentences: what this dashboard shows and why it matters
    use_cases: list[str]    # 3-5 concrete questions this dashboard answers
    tips: list[str]         # 1-2 tips: useful filters, dimensions, or comparisons to try


_agent = Agent(
    model=make_model(),
    output_type=DashboardGuide,
    instructions="""You write short, practical dashboard guides for business users.

Given a dashboard PRD, write:
- overview: 1-2 sentences explaining what the dashboard shows and its business value
- use_cases: 3-5 concrete questions a user can answer with this dashboard (e.g. "Which city drove the most revenue last month?")
- tips: 1-2 actionable tips about filters or dimensions worth exploring

Be specific and grounded in the actual metrics and audience. No fluff.""",
)


async def _run(prd) -> DashboardGuide:
    prompt = (
        f"Dashboard title: {prd.title}\n"
        f"Objective: {prd.objective}\n"
        f"Audience: {prd.audience}\n"
        f"Metrics: {', '.join(prd.metrics)}\n"
        f"Action items: {', '.join(prd.action_items) if prd.action_items else 'none'}"
    )
    result = await _agent.run(prompt)
    return result.output


def generate_guide(prd) -> DashboardGuide:
    return asyncio.run(_run(prd))
