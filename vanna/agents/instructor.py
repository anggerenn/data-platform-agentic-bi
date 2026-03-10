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
    dimensions = getattr(prd, 'dimensions', [])
    prompt = (
        f"Dashboard title: {prd.title}\n"
        f"Objective: {prd.objective}\n"
        f"Audience: {prd.audience}\n"
        f"Metrics: {', '.join(prd.metrics)}\n"
        f"Dimensions: {', '.join(dimensions) if dimensions else 'none'}\n"
        f"Action items: {', '.join(prd.action_items) if prd.action_items else 'none'}"
    )
    result = await _agent.run(prompt)
    return result.output


def generate_guide(prd) -> DashboardGuide:
    return asyncio.run(_run(prd))


async def _merge(existing_prd_data: dict, new_prd) -> DashboardGuide:
    prompt = (
        f"EXISTING dashboard:\n"
        f"  Title: {existing_prd_data.get('title', '')}\n"
        f"  Objective: {existing_prd_data.get('objective', '')}\n"
        f"  Audience: {existing_prd_data.get('audience', '')}\n"
        f"  Metrics: {', '.join(existing_prd_data.get('metrics', []))}\n\n"
        f"NEW content being added to this dashboard:\n"
        f"  Objective: {new_prd.objective}\n"
        f"  Audience: {new_prd.audience}\n"
        f"  Metrics: {', '.join(new_prd.metrics)}\n\n"
        f"Write a combined guide that covers both use cases. The overview should explain "
        f"that this dashboard serves multiple audiences or objectives."
    )
    result = await _agent.run(prompt)
    return result.output


def merge_guides(existing_prd_data: dict, new_prd) -> DashboardGuide:
    """Generate a merged README guide from an existing PRD dict and a new PRD."""
    return asyncio.run(_merge(existing_prd_data, new_prd))
