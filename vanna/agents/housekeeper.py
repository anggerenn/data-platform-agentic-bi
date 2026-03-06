"""Housekeeper — prevents duplicate dashboards by comparing metric fingerprints.

Flow:
  1. Function reads dbt/dashboards/charts/*.yml → extracts metric field IDs per chart
  2. Function reads dbt/dashboards/dashboards/*.yml → maps dashboard → metric keyword set
  3. Function normalises metric field IDs and PRD metric text into keyword sets
  4. Function computes Jaccard similarity between new PRD and each existing dashboard
  5. Verdict:
       full             (score >= 0.8) → redirect to existing dashboard
       partial_covered  (score >= 0.4, prd ⊆ existing) → existing covers PRD → redirect
       partial_uncovered(score >= 0.4, prd ⊄ existing) → PRD has new metrics → polish existing
       none             (score  < 0.4) → no overlap, build freely
  6. LLM only called for the ambiguous zone (0.5–0.7) to check narrative context
"""
import asyncio
import os
import re
from typing import Literal, Optional

import requests
import yaml
from pydantic import BaseModel
from pydantic_ai import Agent

from agents._model import make_model

_DBT_PATH = '/dbt'
_FULL_THRESHOLD = 0.8
_PARTIAL_THRESHOLD = 0.4
_AMBIGUOUS_LOW = 0.5
_AMBIGUOUS_HIGH = 0.7


# ── Result type ────────────────────────────────────────────────────────────────

class HousekeeperVerdict(BaseModel):
    verdict: Literal['full', 'partial_covered', 'partial_uncovered', 'none']
    matched_dashboard_name: Optional[str] = None
    matched_dashboard_url: Optional[str] = None
    reason: str


# ── Lightdash API: fetch dashboard URLs ───────────────────────────────────────

def _fetch_dashboard_urls() -> dict:
    internal = os.environ.get('LIGHTDASH_INTERNAL_URL', 'http://lightdash:8080')
    public = os.environ.get('LIGHTDASH_PUBLIC_URL', 'http://localhost:8080')
    headers = {'Authorization': f"ApiKey {os.environ.get('LIGHTDASH_API_KEY', '')}"}
    try:
        r = requests.get(f"{internal}/api/v1/org/projects", headers=headers, timeout=8)
        projects = r.json().get('results', [])
        if not projects:
            return {}
        project_uuid = projects[0]['projectUuid']
        r = requests.get(
            f"{internal}/api/v1/projects/{project_uuid}/dashboards",
            headers=headers, timeout=8,
        )
        return {
            d['name']: f"{public}/projects/{project_uuid}/dashboards/{d['uuid']}/view"
            for d in r.json().get('results', [])
        }
    except Exception:
        return {}


# ── Function: build metric fingerprints from YAML ─────────────────────────────

def _normalise_field(field_id: str) -> str:
    """'daily_sales_total_revenue_sum' → 'total revenue'"""
    # strip 2-part model prefix (e.g. daily_sales_)
    s = re.sub(r'^[a-z]+_[a-z]+_', '', field_id)
    # strip aggregation suffix
    s = re.sub(r'_(sum|avg|count|min|max)$', '', s)
    return s.replace('_', ' ')


_STOPWORDS = {
    'per', 'by', 'and', 'or', 'the', 'a', 'of', 'in', 'for', 'with',
    'show', 'me', 'total', 'daily', 'monthly', 'weekly', 'its', 'this',
}


def _keywords(text: str) -> set:
    words = re.findall(r'\b[a-z]+\b', text.lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


def _build_fingerprints(dbt_path: str) -> list:
    charts_dir = os.path.join(dbt_path, 'dashboards', 'charts')
    dashboards_dir = os.path.join(dbt_path, 'dashboards', 'dashboards')

    # slug → keyword set from metrics + dimensions
    chart_kws: dict = {}
    if os.path.exists(charts_dir):
        for fn in os.listdir(charts_dir):
            if not fn.endswith('.yml'):
                continue
            with open(os.path.join(charts_dir, fn)) as f:
                doc = yaml.safe_load(f) or {}
            slug = doc.get('slug', fn[:-4])
            mq = doc.get('metricQuery', {})
            fields = mq.get('metrics', []) + mq.get('dimensions', [])
            kws: set = set()
            for fid in fields:
                kws |= _keywords(_normalise_field(fid))
            chart_kws[slug] = kws

    url_map = _fetch_dashboard_urls()
    fingerprints = []

    if os.path.exists(dashboards_dir):
        for fn in os.listdir(dashboards_dir):
            if not fn.endswith('.yml'):
                continue
            with open(os.path.join(dashboards_dir, fn)) as f:
                doc = yaml.safe_load(f) or {}
            name = doc.get('name', fn[:-4])
            all_kws: set = set()
            for tile in doc.get('tiles', []):
                slug = tile.get('properties', {}).get('chartSlug', '')
                all_kws |= chart_kws.get(slug, set())
            fingerprints.append({
                'name': name,
                'url': url_map.get(name, ''),
                'keywords': all_kws,
            })

    return fingerprints


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ── LLM: resolve ambiguous zone ───────────────────────────────────────────────

class _LLMVerdict(BaseModel):
    verdict: Literal['full', 'partial_covered', 'partial_uncovered', 'none']
    reason: str


_agent = Agent(
    model=make_model(),
    output_type=_LLMVerdict,
    instructions="""You are a dashboard governance assistant.

Metric similarity and subset analysis have already been computed.
Your job is to check whether the narratives are truly the same.

  full              — same objective + same audience + same metric cut → duplicate, redirect
  partial_covered   — PRD metrics are covered by existing dashboard; existing already serves this need → redirect
  partial_uncovered — PRD has additional metrics not in existing; build new but suggest polishing existing
  none              — metric overlap is coincidental, narratives are unrelated

Be conservative with "full". Different audience or different dimensional cut
(e.g. one is city-level, other is category-level) is "partial_uncovered" not "full".""",
)


async def _llm_disambiguate(prd, existing: dict, score: float) -> _LLMVerdict:
    prompt = (
        f"New PRD — objective: {prd.objective} | audience: {prd.audience} | metrics: {prd.metrics}\n"
        f"Existing dashboard — name: {existing['name']} | metric keywords: {sorted(existing['keywords'])}\n"
        f"Jaccard similarity: {score:.2f}"
    )
    result = await _agent.run(prompt)
    return result.output


# ── Public entry point (sync) ──────────────────────────────────────────────────

def check(prd) -> HousekeeperVerdict:
    fingerprints = _build_fingerprints(_DBT_PATH)
    if not fingerprints:
        return HousekeeperVerdict(verdict='none', reason='No existing dashboards to compare.')

    prd_kws = _keywords(' '.join(prd.metrics) + ' ' + prd.objective)

    scored = sorted(
        [(fp, _jaccard(prd_kws, fp['keywords'])) for fp in fingerprints],
        key=lambda x: x[1], reverse=True,
    )
    best, score = scored[0]

    if score >= _FULL_THRESHOLD:
        return HousekeeperVerdict(
            verdict='full',
            matched_dashboard_name=best['name'],
            matched_dashboard_url=best['url'],
            reason=f"Metric overlap {score:.0%} — this dashboard already covers the same story.",
        )

    if score >= _PARTIAL_THRESHOLD:
        # Determine sub-verdict: covered (PRD ⊆ existing) vs uncovered (PRD has new metrics)
        new_metrics = prd_kws - best['keywords']
        sub_verdict = 'partial_covered' if not new_metrics else 'partial_uncovered'

        # Ambiguous zone: use LLM to verify narrative context
        if _AMBIGUOUS_LOW <= score <= _AMBIGUOUS_HIGH:
            try:
                llm = asyncio.run(_llm_disambiguate(prd, best, score))
                return HousekeeperVerdict(
                    verdict=llm.verdict,
                    matched_dashboard_name=best['name'] if llm.verdict != 'none' else None,
                    matched_dashboard_url=best['url'] if llm.verdict != 'none' else None,
                    reason=llm.reason,
                )
            except Exception:
                pass

        if sub_verdict == 'partial_covered':
            return HousekeeperVerdict(
                verdict='partial_covered',
                matched_dashboard_name=best['name'],
                matched_dashboard_url=best['url'],
                reason=f"Your metrics are already covered ({score:.0%} overlap) by '{best['name']}' — view it instead of creating a new one.",
            )
        return HousekeeperVerdict(
            verdict='partial_uncovered',
            matched_dashboard_name=best['name'],
            matched_dashboard_url=best['url'],
            reason=f"Metric overlap {score:.0%} with '{best['name']}'. New metrics {sorted(new_metrics)} not yet covered — consider polishing that dashboard instead of starting fresh.",
        )

    return HousekeeperVerdict(
        verdict='none',
        reason=f"No significant overlap (best match {score:.0%}). New dashboard is justified.",
    )
