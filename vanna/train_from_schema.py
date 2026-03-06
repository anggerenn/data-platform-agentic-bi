"""
Semantic layer trainer — reads dbt schema.yml and PRD files, generates
Vanna Q&A pairs and documentation context.

Hash-based incremental training: only processes files that have changed
since the last retrain, avoiding ChromaDB duplication.

Usage:
  docker-compose exec vanna python train_from_schema.py
"""
import hashlib
import json
import os
import re
import sys
sys.path.insert(0, '/app')

import yaml
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

_SCHEMA_FILES = [
    '/dbt/models/marts/schema.yml',
]
_PRD_DIR    = '/dbt/lightdash/prd'
_STATE_FILE = '/data/vanna-retrain-state.json'
_DB_SCHEMA  = 'transformed_marts'


# ── Hash utilities ─────────────────────────────────────────────────────────────

def _file_hash(path: str) -> str:
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def _load_state() -> dict:
    try:
        with open(_STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict):
    os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
    with open(_STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)


# ── SQL generation helpers ─────────────────────────────────────────────────────

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
    return None


def _resolve_derived_sql(raw_sql: str, col_to_agg: dict) -> str:
    """Replace ${metric_name} references with their SQL expressions."""
    def replace(m):
        ref = m.group(1)
        return col_to_agg.get(ref, ref)
    return re.sub(r'\$\{([^}]+)\}', replace, raw_sql)


# ── Schema parser ──────────────────────────────────────────────────────────────

def parse_schema(path: str) -> dict:
    """Extract model name, metrics, and dimensions from a dbt schema.yml."""
    with open(path) as f:
        doc = yaml.safe_load(f)

    result = {}
    for model in doc.get('models', []):
        if not model.get('meta', {}).get('canonical', False):
            continue

        name = model['name']
        metrics = {}
        dimensions = []
        col_to_agg = {}

        for col in model.get('columns', []):
            col_name = col['name']
            meta = col.get('meta', {})

            dim = meta.get('dimension')
            if dim:
                dimensions.append({
                    'col': col_name,
                    'label': dim.get('label', col_name),
                    'type': dim.get('type', 'string'),
                    'description': dim.get('description', ''),
                    'groups': dim.get('groups', []),
                })

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
                    'groups': mdef.get('groups', []),
                }

        for mk, mdef in metrics.items():
            if mdef['raw_sql']:
                mdef['agg'] = _resolve_derived_sql(mdef['raw_sql'], col_to_agg)

        result[name] = {'metrics': metrics, 'dimensions': dimensions}

    return result


# ── Q&A pair generator ─────────────────────────────────────────────────────────

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

        pairs.append((f"What is the {label.lower()}?", f"SELECT {agg} AS {mk} FROM {table}"))
        pairs.append((f"Show me the {label.lower()}", f"SELECT {agg} AS {mk} FROM {table}"))

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

        if len(cat_dims) >= 2:
            d1, d2 = cat_dims[0], cat_dims[1]
            pairs.append((
                f"{label} by {d1['label'].lower()} and {d2['label'].lower()}",
                f"SELECT {d1['col']}, {d2['col']}, {agg} AS {mk} "
                f"FROM {table} GROUP BY {d1['col']}, {d2['col']} ORDER BY {mk} DESC",
            ))

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
            for d in cat_dims:
                pairs.append((
                    f"{label} by {d['label'].lower()} over time",
                    f"SELECT {dcol}, {d['col']}, {agg} AS {mk} "
                    f"FROM {table} GROUP BY {dcol}, {d['col']} ORDER BY {dcol}",
                ))

    return pairs


# ── Documentation generators ───────────────────────────────────────────────────

def generate_docs(model_name: str, model: dict) -> list:
    """Generate documentation strings from a model's metrics and dimensions.

    These give Vanna business context beyond Q&A pairs — metric meanings,
    when to use them, and their dimensional relationships.
    """
    table = f"{_DB_SCHEMA}.{model_name}"
    docs = []

    for dim in model['dimensions']:
        groups = ', '.join(dim.get('groups', [])) or 'none'
        docs.append(
            f"Dimension '{dim['label']}' (column: {dim['col']}, type: {dim['type']}) "
            f"in table {table}. {dim.get('description', '')} Groups: {groups}."
        )

    for mk, m in model['metrics'].items():
        if not m['agg']:
            continue
        groups = ', '.join(m.get('groups', [])) or 'none'
        docs.append(
            f"Metric '{m['label']}' (key: {mk}) in table {table}. "
            f"Formula: {m['agg']}. {m.get('description', '')} Groups: {groups}."
        )

    return docs


def _prd_doc(prd: dict) -> str:
    """Generate a documentation string from a PRD dict."""
    metrics = ', '.join(prd.get('metrics', []))
    dims    = ', '.join(prd.get('dimensions', []))
    return (
        f"Dashboard: '{prd['title']}'. "
        f"Objective: {prd.get('objective', '')} "
        f"Audience: {prd.get('audience', '')} "
        f"Metrics: {metrics}. Dimensions: {dims}. "
        f"Model: {prd.get('model', '')}."
    )


# ── Main retrain ───────────────────────────────────────────────────────────────

def retrain(vn) -> dict:
    """Incremental retrain: only processes files changed since last run.

    Returns stats: qa_added, qa_skipped, docs_added, docs_skipped.
    """
    state = _load_state()
    stats = {'qa_added': 0, 'qa_skipped': 0, 'docs_added': 0, 'docs_skipped': 0}

    # --- Schema files ---
    for path in _SCHEMA_FILES:
        if not os.path.exists(path):
            continue
        current_hash = _file_hash(path)

        if state.get(path) == current_hash:
            models = parse_schema(path)
            for model_name, model in models.items():
                stats['qa_skipped']   += len(generate_pairs(model_name, model))
                stats['docs_skipped'] += len(generate_docs(model_name, model))
            continue

        models = parse_schema(path)
        for model_name, model in models.items():
            for question, sql in generate_pairs(model_name, model):
                vn.train(question=question, sql=sql)
                stats['qa_added'] += 1
            for doc in generate_docs(model_name, model):
                vn.train(documentation=doc)
                stats['docs_added'] += 1

        state[path] = current_hash

    # --- PRD files ---
    if os.path.isdir(_PRD_DIR):
        for fname in sorted(os.listdir(_PRD_DIR)):
            if not fname.endswith('.json'):
                continue
            fpath = os.path.join(_PRD_DIR, fname)
            current_hash = _file_hash(fpath)

            if state.get(fpath) == current_hash:
                stats['docs_skipped'] += 1
                continue

            try:
                with open(fpath) as f:
                    prd = json.load(f)
                vn.train(documentation=_prd_doc(prd))
                stats['docs_added'] += 1
                state[fpath] = current_hash
            except Exception:
                continue

    _save_state(state)
    return stats


def run():
    from vn import get_vanna
    vn = get_vanna()
    stats = retrain(vn)
    print(
        f"Q&A pairs:  {stats['qa_added']} added, {stats['qa_skipped']} skipped\n"
        f"Docs:       {stats['docs_added']} added, {stats['docs_skipped']} skipped"
    )


if __name__ == '__main__':
    run()
