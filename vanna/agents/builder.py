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


_scan_cache: dict[str, list[dict]] = {}


def _scan_models(dbt_path: str) -> list[dict]:
    if dbt_path in _scan_cache:
        return _scan_cache[dbt_path]
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
                'grain': m.get('meta', {}).get('grain', []),
            })
    _scan_cache[dbt_path] = results
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


def find_best_model(
    dbt_path: str,
    dimensions: list[str],
    metrics: list[str],
) -> Optional[dict]:
    models = _scan_models(dbt_path)

    # Normalise required dimension names for grain comparison
    required_dims = {d.lower().strip() for d in dimensions if d.strip()}

    def grain_covers(model: dict) -> bool:
        """True if the model's declared grain is a superset of the required dimensions."""
        if not required_dims:
            return True
        grain = {g.lower() for g in model.get('grain', [])}
        return required_dims.issubset(grain)

    covering = [m for m in models if grain_covers(m)]

    if covering:
        # Among models whose grain covers the required dims, prefer canonical;
        # use coverage score as tiebreaker.
        scored = sorted(
            [(m, _coverage_score(m, metrics) + (0.1 if m['canonical'] else 0))
             for m in covering],
            key=lambda x: x[1],
            reverse=True,
        )
        return scored[0][0]

    # Fallback: no declared grain covers the required dims (e.g. staging models with
    # row-level grain that have all raw columns).  Use coverage score across all models.
    all_terms = dimensions + metrics
    scored = sorted(
        [(m, _coverage_score(m, all_terms) + (0.1 if m['canonical'] else 0))
         for m in models],
        key=lambda x: x[1],
        reverse=True,
    )
    if scored and scored[0][1] >= 0.3:
        return scored[0][0]

    canonical = [m for m in models if m['canonical']]
    return canonical[0] if len(canonical) == 1 else None


async def run_data_modeler(prd, dbt_path: str) -> DataModelResult:
    """
    Function-first: check if an existing dbt model covers the PRD metrics.
    If yes, return it directly — no LLM, no SQL generation.
    If no, flag needs_new_model=True (dbt model creation is a future step).
    """
    dimensions = getattr(prd, 'dimensions', [])
    best = find_best_model(dbt_path, dimensions=dimensions, metrics=prd.metrics)
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
