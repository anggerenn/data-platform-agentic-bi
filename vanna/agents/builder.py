import glob
import os
import re
from typing import Optional

import yaml
from pydantic import BaseModel


class DataModelResult(BaseModel):
    model_name: str
    db_schema: str
    columns: list[str]
    is_new: bool
    needs_new_model: bool = False  # True when no existing model covers the PRD


_FILLER = {
    'by', 'per', 'and', 'or', 'the', 'a', 'of', 'to', 'in', 'for', 'with',
    'rate', 'trend', 'growth', 'daily', 'monthly', 'total', 'breakdown',
    'over', 'time', 'last', 'current', 'previous', 'vs', 'each', 'all',
}

# Phrases that signal the PRD requires individual customer-level grain
_CUSTOMER_GRAIN = {
    'customer_id', 'customer id', 'per customer', 'by customer',
    'individual customer', 'leaderboard', 'customer rank', 'customer level',
    'top customer', 'each customer',
}


def _needs_customer_grain(metrics: list[str]) -> bool:
    """Return True if any metric text implies individual customer-level grain."""
    combined = ' '.join(metrics).lower()
    return any(kw in combined for kw in _CUSTOMER_GRAIN)


def _scan_models(dbt_path: str) -> list[dict]:
    results = []
    schema_prefix = os.environ.get('DBT_SCHEMA_PREFIX', 'transformed_')
    for schema_file in glob.glob(
        os.path.join(dbt_path, 'models', '**', 'schema.yml'), recursive=True
    ):
        try:
            with open(schema_file) as f:
                data = yaml.safe_load(f)
        except Exception:
            continue
        if not data or 'models' not in data:
            continue
        dir_name = os.path.basename(os.path.dirname(schema_file))
        db_schema = f"{schema_prefix}{dir_name}"
        for m in data.get('models', []):
            results.append({
                'name': m['name'],
                'db_schema': db_schema,
                'columns': [c['name'] for c in m.get('columns', [])],
                'description': m.get('description', ''),
                'canonical': bool(m.get('meta', {}).get('canonical')),
            })
    return results


def _coverage_score(model: dict, metrics: list[str]) -> float:
    keywords = set()
    for m in metrics:
        keywords.update(re.findall(r'\w+', m.lower()))
    keywords -= _FILLER
    if not keywords:
        return 1.0
    col_set = {c.lower() for c in model['columns']}
    desc_words = set(re.findall(r'\w+', model['description'].lower()))
    searchable = col_set | desc_words
    matched = sum(1 for kw in keywords if any(kw in s or s in kw for s in searchable))
    return matched / len(keywords)


def find_best_model(dbt_path: str, metrics: list[str]) -> Optional[dict]:
    models = _scan_models(dbt_path)

    # Customer-grain override: if PRD needs customer_id, restrict to models that have it.
    # This prevents the canonical daily_sales (no customer_id) from being selected when
    # the PRD asks for individual customer breakdowns or leaderboards.
    if _needs_customer_grain(metrics):
        customer_models = [m for m in models if 'customer_id' in m['columns']]
        if customer_models:
            models = customer_models

    canonical = [m for m in models if m['canonical']]

    # Score all models; canonical ones get a tie-breaking boost
    scored = sorted(
        [(m, _coverage_score(m, metrics) + (0.1 if m['canonical'] else 0)) for m in models],
        key=lambda x: x[1],
        reverse=True,
    )

    if scored and scored[0][1] >= 0.4:
        return scored[0][0]

    # Fallback: if there is exactly one canonical model it covers general analysis by definition
    if len(canonical) == 1:
        return canonical[0]

    return None


async def run_data_modeler(prd, dbt_path: str) -> DataModelResult:
    """
    Function-first: check if an existing dbt model covers the PRD metrics.
    If yes, return it directly — no LLM, no SQL generation.
    If no, flag needs_new_model=True (dbt model creation is a future step).
    """
    best = find_best_model(dbt_path, prd.metrics)
    if best:
        return DataModelResult(
            model_name=best['name'],
            db_schema=best['db_schema'],
            columns=best['columns'],
            is_new=False,
        )
    return DataModelResult(
        model_name='',
        db_schema='',
        columns=[],
        is_new=False,
        needs_new_model=True,
    )
