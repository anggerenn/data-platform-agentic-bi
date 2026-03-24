"""Housekeeper — prevents duplicate dashboards by comparing metric fingerprints.

Flow:
  1. Function reads dbt/lightdash/charts/*.yml → extracts metric field IDs per chart
  2. Function reads dbt/lightdash/dashboards/*.yml → maps dashboard → metric keyword set
  3. Function normalises metric field IDs and PRD metric text into keyword sets
  4. Function computes Jaccard similarity between new PRD and each existing dashboard
  5. Verdict:
       full             (score >= 0.8) → redirect to existing dashboard
       partial_covered  (score >= 0.4, prd ⊆ existing) → existing covers PRD → redirect
       partial_uncovered(score >= 0.4, prd ⊄ existing) → PRD has new metrics → polish existing
       none             (score  < 0.4) → no overlap, build freely
  6. LLM only called for the ambiguous zone (0.5–0.7) to check narrative context
"""
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


# ── Lightdash API: fetch all dashboard fingerprints ───────────────────────────

def _fetch_api_fingerprints() -> list:
    """Pull fingerprints for ALL dashboards (UI + YAML) from the Lightdash API.

    For each dashboard → tiles → savedChartUuid → metricQuery fields.
    This covers dashboards created directly in the Lightdash UI that never
    touch the dbt YAML files.
    """
    internal = os.environ.get('LIGHTDASH_INTERNAL_URL', 'http://lightdash:8080')
    public   = os.environ['LIGHTDASH_PUBLIC_URL']
    headers  = {'Authorization': f"ApiKey {os.environ.get('LIGHTDASH_API_KEY', '')}"}

    try:
        project_uuid = requests.get(
            f"{internal}/api/v1/org/projects", headers=headers, timeout=8
        ).json()['results'][0]['projectUuid']
    except Exception:
        return []

    try:
        dashboards = requests.get(
            f"{internal}/api/v1/projects/{project_uuid}/dashboards",
            headers=headers, timeout=8,
        ).json().get('results', [])
    except Exception:
        return []

    fingerprints = []
    for d in dashboards:
        name = d['name']
        url  = f"{public}/projects/{project_uuid}/dashboards/{d['uuid']}/view"
        try:
            tiles = requests.get(
                f"{internal}/api/v1/dashboards/{d['uuid']}",
                headers=headers, timeout=8,
            ).json()['results']['tiles']
        except Exception:
            tiles = []

        all_kws: set = set()
        for tile in tiles:
            chart_uuid = tile.get('properties', {}).get('savedChartUuid')
            if not chart_uuid:
                continue
            try:
                mq = requests.get(
                    f"{internal}/api/v1/saved/{chart_uuid}",
                    headers=headers, timeout=8,
                ).json()['results']['metricQuery']
                fields = mq.get('metrics', []) + mq.get('dimensions', [])
                for fid in fields:
                    all_kws |= _keywords(_normalise_field(fid))
            except Exception:
                continue

        fingerprints.append({'name': name, 'url': url, 'keywords': all_kws})

    return fingerprints


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


def _slugify(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')


def _has_prd(dbt_path: str, name: str) -> bool:
    path = os.path.join(dbt_path, 'lightdash', 'prd', f'{_slugify(name)}.json')
    return os.path.exists(path)


def _chart_field_keywords(dbt_path: str, dashboard_slug: str) -> set:
    """Load field-level keyword signal from chart YAMLs linked to a dashboard."""
    dashboard_path = os.path.join(dbt_path, 'lightdash', 'dashboards', f'{dashboard_slug}.yml')
    charts_dir = os.path.join(dbt_path, 'lightdash', 'charts')
    kws: set = set()
    if not os.path.exists(dashboard_path):
        return kws
    try:
        with open(dashboard_path) as f:
            doc = yaml.safe_load(f) or {}
        for tile in doc.get('tiles', []):
            chart_slug = tile.get('properties', {}).get('chartSlug', '')
            if not chart_slug:
                continue
            chart_path = os.path.join(charts_dir, f'{chart_slug}.yml')
            if not os.path.exists(chart_path):
                continue
            with open(chart_path) as f:
                chart_doc = yaml.safe_load(f) or {}
            mq = chart_doc.get('metricQuery', {})
            for fid in mq.get('metrics', []) + mq.get('dimensions', []):
                kws |= _keywords(_normalise_field(fid))
    except Exception:
        pass
    return kws


def _build_fingerprints(dbt_path: str) -> list:
    """Build fingerprints from PRD JSON files (authoritative metric vocabulary).

    PRD files use the same human-readable metric text as new PRDs, giving
    meaningful Jaccard similarity. Chart field IDs are merged in as a
    structural signal (field-level overlap). API is only used for URLs.

    Governance rules:
      - [WIP]-prefixed dashboard titles are excluded
      - Only dashboards with a PRD file are included
    """
    import json as _json

    # Fetch URLs from API for matching by dashboard name
    api_fps = _fetch_api_fingerprints()
    url_by_name = {fp['name']: fp['url'] for fp in api_fps}

    # Primary: read PRD JSON files — same vocabulary as new PRD metrics
    prd_dir = os.path.join(dbt_path, 'lightdash', 'prd')
    fingerprints = []
    if os.path.exists(prd_dir):
        for fn in os.listdir(prd_dir):
            if not fn.endswith('.json'):
                continue
            with open(os.path.join(prd_dir, fn)) as f:
                prd_data = _json.load(f)
            name = prd_data.get('title', fn[:-5])
            if name.startswith('[WIP]'):
                continue
            metrics = prd_data.get('metrics', [])
            objective = prd_data.get('objective', '')
            model = prd_data.get('model', '')
            # Narrative keywords + field-level keywords from chart YAMLs
            dashboard_slug = _slugify(name)
            kws = _keywords(' '.join(metrics) + ' ' + objective)
            kws |= _chart_field_keywords(dbt_path, dashboard_slug)
            fingerprints.append({
                'name': name,
                'url': url_by_name.get(name, ''),
                'keywords': kws,
                'model': model,
            })

    prd_names = {fp['name'] for fp in fingerprints}

    # Fallback: YAML-only dashboards not yet in any PRD file (pre-deploy)
    dashboards_dir = os.path.join(dbt_path, 'lightdash', 'dashboards')
    charts_dir     = os.path.join(dbt_path, 'lightdash', 'charts')

    chart_kws: dict = {}
    if os.path.exists(charts_dir):
        for fn in os.listdir(charts_dir):
            if not fn.endswith('.yml'):
                continue
            with open(os.path.join(charts_dir, fn)) as f:
                doc = yaml.safe_load(f) or {}
            slug = doc.get('slug', fn[:-4])
            mq   = doc.get('metricQuery', {})
            fields = mq.get('metrics', []) + mq.get('dimensions', [])
            kws: set = set()
            for fid in fields:
                kws |= _keywords(_normalise_field(fid))
            chart_kws[slug] = kws

    yaml_fps = []
    if os.path.exists(dashboards_dir):
        for fn in os.listdir(dashboards_dir):
            if not fn.endswith('.yml'):
                continue
            with open(os.path.join(dashboards_dir, fn)) as f:
                doc = yaml.safe_load(f) or {}
            name = doc.get('name', fn[:-4])
            if name in prd_names:
                continue  # already covered by API
            if name.startswith('[WIP]') or not _has_prd(dbt_path, name):
                continue  # no PRD → implicitly WIP
            all_kws: set = set()
            for tile in doc.get('tiles', []):
                slug = tile.get('properties', {}).get('chartSlug', '')
                all_kws |= chart_kws.get(slug, set())
            yaml_fps.append({'name': name, 'url': '', 'keywords': all_kws, 'model': ''})

    return fingerprints + yaml_fps


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ── ChromaDB: resolve ambiguous zone ──────────────────────────────────────────

_PRD_DOC_PREFIX = "Dashboard: '"


def _extract_dashboard_name(doc: str) -> Optional[str]:
    """Extract dashboard name from a PRD documentation string."""
    m = re.match(r"Dashboard: '([^']+)'", doc)
    return m.group(1) if m else None


def _chromadb_disambiguate(prd, best: dict, score: float, vn) -> Optional[HousekeeperVerdict]:
    """Use ChromaDB semantic similarity to resolve the ambiguous Jaccard zone.

    Queries the documentation collection for PRD docs similar to the new PRD.
    If a stored PRD doc for the best-matching dashboard is returned, the
    semantic overlap is confirmed and we return a verdict without calling the LLM.
    """
    try:
        prd_summary = (
            f"Dashboard objective: {prd.objective}. "
            f"Audience: {prd.audience}. "
            f"Metrics: {', '.join(prd.metrics)}."
        )
        related = vn.get_related_documentation(prd_summary)
        for doc in related[:5]:
            name = _extract_dashboard_name(doc)
            if name and name.lower() == best['name'].lower():
                new_metrics = _keywords(' '.join(prd.metrics)) - best['keywords']
                sub = 'partial_covered' if not new_metrics else 'partial_uncovered'
                if sub == 'partial_covered':
                    reason = (
                        f"Semantic match confirmed with '{best['name']}' ({score:.0%} metric overlap). "
                        f"Your metrics are already covered — view it instead of creating a new one."
                    )
                else:
                    reason = (
                        f"Semantic match confirmed with '{best['name']}' ({score:.0%} metric overlap). "
                        f"New metrics {sorted(new_metrics)} not yet covered — consider polishing that dashboard."
                    )
                return HousekeeperVerdict(
                    verdict=sub,
                    matched_dashboard_name=best['name'],
                    matched_dashboard_url=best['url'],
                    reason=reason,
                )
    except Exception:
        pass
    return None


# ── LLM: fallback for ambiguous zone ──────────────────────────────────────────

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


def _llm_disambiguate(prd, existing: dict, score: float) -> _LLMVerdict:
    prompt = (
        f"New PRD — objective: {prd.objective} | audience: {prd.audience} | metrics: {prd.metrics}\n"
        f"Existing dashboard — name: {existing['name']} | metric keywords: {sorted(existing['keywords'])}\n"
        f"Jaccard similarity: {score:.2f}"
    )
    result = _agent.run_sync(prompt)
    return result.output


# ── Public entry point (sync) ──────────────────────────────────────────────────

def check(prd, vn=None, model_name: Optional[str] = None) -> HousekeeperVerdict:
    fingerprints = _build_fingerprints(_DBT_PATH)
    if not fingerprints:
        return HousekeeperVerdict(verdict='none', reason='No existing dashboards to compare.')

    prd_kws = _keywords(' '.join(prd.metrics) + ' ' + prd.objective)

    scored = sorted(
        [(fp, _jaccard(prd_kws, fp['keywords'])) for fp in fingerprints],
        key=lambda x: x[1], reverse=True,
    )
    best, score = scored[0]

    # Model-level signal: same dbt model → treat as at least partial overlap
    # even when narrative Jaccard is low (catches same-data, different-framing)
    if model_name and score < _PARTIAL_THRESHOLD:
        for fp, s in scored:
            if fp.get('model') == model_name:
                score = _PARTIAL_THRESHOLD
                best = fp
                break

    if score >= _FULL_THRESHOLD:
        return HousekeeperVerdict(
            verdict='full',
            matched_dashboard_name=best['name'],
            matched_dashboard_url=best['url'],
            reason=f"Metric overlap {score:.0%} — this dashboard already covers the same story.",
        )

    if score >= _PARTIAL_THRESHOLD:
        new_metrics = prd_kws - best['keywords']
        sub_verdict = 'partial_covered' if not new_metrics else 'partial_uncovered'

        # Ambiguous zone: try ChromaDB first, fall back to LLM
        if _AMBIGUOUS_LOW <= score <= _AMBIGUOUS_HIGH:
            if vn is not None:
                verdict = _chromadb_disambiguate(prd, best, score, vn)
                if verdict:
                    return verdict
            try:
                llm = _llm_disambiguate(prd, best, score)
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
