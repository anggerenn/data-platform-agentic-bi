"""
Semantic layer parser — reads dbt schema.yml metric definitions and generates
Vanna training pairs (question + SQL) automatically.

Covers:
  - Simple aggregations: "What is the total revenue?"
  - By single dimension: "Total revenue by city"
  - By two dimensions: "Total revenue by city and category"
  - Time trend: "Total revenue over time"
  - Ranked: "Which city has the highest total revenue?"
  - Derived metrics (type: number) with description-based questions

Usage:
  docker-compose exec vanna python train_from_schema.py
"""
import os
import sys
sys.path.insert(0, '/app')

import yaml
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

from vn import get_vanna

vn = get_vanna()

_SCHEMA_FILES = [
    '/dbt/models/marts/schema.yml',
]

_DB_SCHEMA = 'transformed_marts'


def _agg_sql(col: str, metric: dict) -> str:
    """Return the SQL aggregation expression for a metric."""
    mtype = metric.get('type', 'sum')
    if mtype == 'sum':
        return f"SUM({col})"
    if mtype == 'count':
        return f"COUNT({col})"
    if mtype == 'count_distinct':
        return f"COUNT(DISTINCT {col})"
    if mtype == 'average':
        return f"AVG({col})"
    if mtype == 'max':
        return f"MAX({col})"
    if mtype == 'min':
        return f"MIN({col})"
    # type: number — derived, no direct agg
    return None


def _resolve_derived_sql(raw_sql: str, col_to_agg: dict) -> str:
    """Replace ${metric_name} references with their SQL expressions."""
    import re
    def replace(m):
        ref = m.group(1)
        return col_to_agg.get(ref, ref)
    return re.sub(r'\$\{([^}]+)\}', replace, raw_sql)


def parse_schema(path: str) -> dict:
    """Extract model name, metrics, and dimensions from a dbt schema.yml."""
    with open(path) as f:
        doc = yaml.safe_load(f)

    result = {}
    for model in doc.get('models', []):
        if not model.get('meta', {}).get('canonical', False):
            continue  # only canonical models go into training

        name = model['name']
        metrics = {}     # metric_key → {label, description, col, agg_sql}
        dimensions = []  # [{col, label, type}]

        # Build a map of metric_name → agg SQL (needed to resolve derived metrics)
        col_to_agg = {}

        for col in model.get('columns', []):
            col_name = col['name']
            meta = col.get('meta', {})

            # Dimensions
            dim = meta.get('dimension')
            if dim:
                dimensions.append({
                    'col': col_name,
                    'label': dim.get('label', col_name),
                    'type': dim.get('type', 'string'),
                })

            # Metrics
            for metric_key, mdef in meta.get('metrics', {}).items():
                agg = _agg_sql(col_name, mdef)
                col_to_agg[metric_key] = agg or ''
                metrics[metric_key] = {
                    'label': mdef.get('label', metric_key),
                    'description': mdef.get('description', ''),
                    'col': col_name,
                    'type': mdef.get('type', 'sum'),
                    'agg': agg,
                    'raw_sql': mdef.get('sql'),
                }

        # Resolve derived metric SQL
        for mk, mdef in metrics.items():
            if mdef['raw_sql']:
                mdef['agg'] = _resolve_derived_sql(mdef['raw_sql'], col_to_agg)

        result[name] = {'metrics': metrics, 'dimensions': dimensions}

    return result


def generate_pairs(model_name: str, model: dict) -> list:
    """Generate (question, sql) training pairs from a model's metrics and dimensions."""
    table = f"{_DB_SCHEMA}.{model_name}"
    metrics = model['metrics']
    dims = model['dimensions']
    cat_dims = [d for d in dims if d['type'] == 'string']
    date_dim = next((d for d in dims if d['type'] == 'date'), None)

    pairs = []

    for mk, m in metrics.items():
        if not m['agg']:
            continue
        label = m['label']
        agg   = m['agg']

        # 1. Simple total
        pairs.append((
            f"What is the {label.lower()}?",
            f"SELECT {agg} AS {mk} FROM {table}",
        ))
        pairs.append((
            f"Show me the {label.lower()}",
            f"SELECT {agg} AS {mk} FROM {table}",
        ))

        # 2. By each categorical dimension
        for d in cat_dims:
            col, dlabel = d['col'], d['label']
            pairs.append((
                f"{label} by {dlabel.lower()}",
                f"SELECT {col}, {agg} AS {mk} FROM {table} GROUP BY {col} ORDER BY {mk} DESC",
            ))
            pairs.append((
                f"Which {dlabel.lower()} has the highest {label.lower()}?",
                f"SELECT {col}, {agg} AS {mk} FROM {table} GROUP BY {col} ORDER BY {mk} DESC LIMIT 1",
            ))
            pairs.append((
                f"Rank {dlabel.lower()} by {label.lower()}",
                f"SELECT {col}, {agg} AS {mk} FROM {table} GROUP BY {col} ORDER BY {mk} DESC",
            ))

        # 3. By two categorical dimensions
        if len(cat_dims) >= 2:
            d1, d2 = cat_dims[0], cat_dims[1]
            pairs.append((
                f"{label} by {d1['label'].lower()} and {d2['label'].lower()}",
                f"SELECT {d1['col']}, {d2['col']}, {agg} AS {mk} "
                f"FROM {table} GROUP BY {d1['col']}, {d2['col']} ORDER BY {mk} DESC",
            ))

        # 4. Trend over time
        if date_dim:
            dcol = date_dim['col']
            pairs.append((
                f"{label} over time",
                f"SELECT {dcol}, {agg} AS {mk} FROM {table} GROUP BY {dcol} ORDER BY {dcol}",
            ))
            pairs.append((
                f"Daily {label.lower()} trend",
                f"SELECT {dcol}, {agg} AS {mk} FROM {table} GROUP BY {dcol} ORDER BY {dcol}",
            ))

            # 5. Trend by dimension
            for d in cat_dims:
                pairs.append((
                    f"{label} by {d['label'].lower()} over time",
                    f"SELECT {dcol}, {d['col']}, {agg} AS {mk} "
                    f"FROM {table} GROUP BY {dcol}, {d['col']} ORDER BY {dcol}",
                ))

    return pairs


def run():
    all_pairs = []
    for path in _SCHEMA_FILES:
        if not os.path.exists(path):
            print(f"Not found: {path}")
            continue
        models = parse_schema(path)
        for model_name, model in models.items():
            pairs = generate_pairs(model_name, model)
            all_pairs.extend(pairs)
            print(f"  {model_name}: {len(pairs)} pairs from "
                  f"{len(model['metrics'])} metrics × {len(model['dimensions'])} dimensions")

    print(f"\nTotal: {len(all_pairs)} training pairs. Training Vanna...")
    for i, (question, sql) in enumerate(all_pairs):
        vn.train(question=question, sql=sql)
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(all_pairs)} trained")

    print(f"Done. {len(all_pairs)} pairs added to ChromaDB.")


if __name__ == '__main__':
    run()
