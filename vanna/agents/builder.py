import glob
import os
import re
import subprocess
from typing import Optional

import psycopg2
import yaml
from pydantic import BaseModel


class DataModelResult(BaseModel):
    model_name: str
    db_schema: str
    columns: list[str]
    is_new: bool
    needs_new_model: bool = False
    uncovered_metrics: list[str] = []
    required_grain: list[str] = []   # grain inferred from PRD


_FILLER = {
    'by', 'per', 'and', 'or', 'the', 'a', 'of', 'to', 'in', 'for', 'with',
    'rate', 'trend', 'growth', 'daily', 'monthly', 'total', 'breakdown',
    'over', 'time', 'last', 'current', 'previous', 'vs', 'each', 'all',
}

# Metrics whose remaining keywords are ALL aggregation/time terms are
# computable from any numeric+date model — skip the coverage check.
_AGGREGATE_TERMS = {
    'mom', 'yoy', 'wow', 'mtd', 'ytd', 'wtd',
    'avg', 'average', 'sum', 'count', 'num', 'number',
    'pct', 'percent', 'ratio', 'index', 'rank', 'running', 'cumulative', 'rolling',
}

# Maps PRD keyword → grain column required to support it
_GRAIN_SIGNALS: dict[str, str] = {
    'customer':   'customer_id',
    'user':       'customer_id',
    'account':    'customer_id',
    'client':     'customer_id',
    'leaderboard':'customer_id',
    'active':     'customer_id',
    'inactive':   'customer_id',
    'churn':      'customer_id',
    'retention':  'customer_id',
    'type':       'customer_id',   # "customer type" → customer-level grain
    'city':       'city',
    'region':     'city',
    'location':   'city',
    'category':   'category',
    'product':    'category',
    'segment':    'category',
    'date':       'order_date',
    'day':        'order_date',
    'week':       'order_date',
    'month':      'order_date',
    'trend':      'order_date',
    'daily':      'order_date',
}

# Hard-override: these keywords require their associated column to
# physically exist in the model's columns.  Keyword-score alone cannot
# satisfy coverage — if the column is absent the metric is uncovered.
_HARD_GRAIN_SIGNALS: dict[str, str] = {
    'active':      'customer_id',
    'inactive':    'customer_id',
    'churn':       'customer_id',
    'retention':   'customer_id',
    'leaderboard': 'customer_id',
}


# ── Schema scanning ────────────────────────────────────────────────────────────

_scan_cache: dict[str, list[dict]] = {}


def _scan_models(dbt_path: str) -> list[dict]:
    if dbt_path in _scan_cache:
        return _scan_cache[dbt_path]
    results = []
    schema_prefix = os.environ.get('DBT_SCHEMA_PREFIX', 'transformed_')
    # Match both schema.yml and per-model *.yml files (scaffolded models)
    for schema_file in glob.glob(
        os.path.join(dbt_path, 'models', '**', '*.yml'), recursive=True
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
            # Extract Lightdash meta.metrics names — these are valid coverage targets
            # even if they are not physical columns (e.g. average_order_value derived metric)
            metric_names: set[str] = set()
            for col in m.get('columns', []):
                for mk_key, mk_def in col.get('meta', {}).get('metrics', {}).items():
                    metric_names.add(mk_key.lower())
                    if isinstance(mk_def, dict) and 'label' in mk_def:
                        metric_names.add(mk_def['label'].lower().replace(' ', '_'))

            results.append({
                'name': m['name'],
                'db_schema': db_schema,
                'columns': [c['name'] for c in m.get('columns', [])],
                'metric_names': metric_names,
                'description': m.get('description', ''),
                'canonical': bool(m.get('meta', {}).get('canonical')),
                'grain': m.get('meta', {}).get('grain', []),
            })
    _scan_cache[dbt_path] = results
    return results


# ── Grain inference ────────────────────────────────────────────────────────────

def _infer_grain_from_prd(prd) -> list[str]:
    """
    Parse PRD metrics and dimensions to determine the minimum required grain.

    Returns a sorted, deduplicated list of grain column names from stg_orders.
    Example: PRD mentioning "customer", "city", "daily" → ['city', 'customer_id', 'order_date']
    """
    grain: set[str] = set()
    all_text = ' '.join(prd.metrics + getattr(prd, 'dimensions', []))
    words = set(re.findall(r'\w+', all_text.lower()))
    for word, col in _GRAIN_SIGNALS.items():
        if word in words:
            grain.add(col)
    return sorted(grain)


# ── Coverage scoring ───────────────────────────────────────────────────────────

def _uncovered_metrics(model: dict, metrics: list[str]) -> list[str]:
    """
    Return PRD metrics that the model cannot support.

    Two-stage check:
    1. Hard check — if the metric contains a keyword in _HARD_GRAIN_SIGNALS
       and the required column is absent from the model, it is unconditionally
       uncovered regardless of keyword score.
    2. Keyword-score check — fewer than half the remaining keywords match
       the model's columns, metric names, or description.

    Skips pure aggregation/time-window metrics (computable from any model).
    """
    col_set = {c.lower() for c in model['columns']}
    metric_set = model.get('metric_names', set())
    desc_words = set(re.findall(r'\w+', model['description'].lower()))
    searchable = col_set | metric_set | desc_words

    uncovered = []
    for m in metrics:
        words = set(re.findall(r'\w+', m.lower()))

        # Stage 1: hard grain check — certain semantics need a specific column.
        if any(col not in col_set
               for kw, col in _HARD_GRAIN_SIGNALS.items()
               if kw in words):
            uncovered.append(m)
            continue

        # Stage 2: keyword-score check.
        keywords = list(words - _FILLER)
        if not keywords:
            continue
        if all(kw in _AGGREGATE_TERMS for kw in keywords):
            continue
        matched = sum(1 for kw in keywords if any(kw in s or s in kw for s in searchable))
        if matched / len(keywords) < 0.5:
            uncovered.append(m)
    return uncovered


def _coverage_score(model: dict, metrics: list[str]) -> float:
    keywords = set()
    for m in metrics:
        keywords.update(re.findall(r'\w+', m.lower()))
    keywords -= _FILLER
    if not keywords:
        return 1.0
    col_set = {c.lower() for c in model['columns']}
    metric_set = model.get('metric_names', set())
    desc_words = set(re.findall(r'\w+', model['description'].lower()))
    searchable = col_set | metric_set | desc_words
    matched = sum(1 for kw in keywords if any(kw in s or s in kw for s in searchable))
    return matched / len(keywords)


# ── Model selection ────────────────────────────────────────────────────────────

def find_best_model(
    dbt_path: str,
    required_grain: list[str],
    metrics: list[str],
) -> Optional[dict]:
    """
    Find the best existing model whose declared grain is a superset of
    required_grain and whose columns/metrics cover the PRD metrics.
    """
    models = _scan_models(dbt_path)
    grain_set = {g.lower() for g in required_grain}

    def grain_covers(model: dict) -> bool:
        if not grain_set:
            return True
        col_set = {c.lower() for c in model['columns']}
        return grain_set.issubset(col_set)

    covering = [m for m in models if grain_covers(m)]

    if covering:
        scored = sorted(
            [(m, _coverage_score(m, metrics) + (0.1 if m['canonical'] else 0))
             for m in covering],
            key=lambda x: x[1],
            reverse=True,
        )
        return scored[0][0]

    # Fallback: score across all models
    all_terms = required_grain + metrics
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


# ── Model scaffolding ──────────────────────────────────────────────────────────


_NUM_COL_RE = re.compile(
    r'(count|amount|revenue|total|sum|avg|average|quantity|units|value|rate|pct|percent)',
    re.I,
)
_ID_COL_RE = re.compile(r'_id$', re.I)  # only true entity ID columns get count_distinct metric
_PLAIN_COL_RE = re.compile(r'^[\w]+$')  # plain col ref (no function calls) — e.g. "col" or "a.col"
_RANK_COL_RE = re.compile(r'(_rank|_leaderboard_rank)$', re.I)
_DATE_COL_RE = re.compile(r'(date|_date|month|week|year|period|_at|_time)$|^(date|month|week|year)', re.I)


def _model_name_from_prd(prd) -> str:
    """Derive a short snake_case model name from the PRD title."""
    title = getattr(prd, 'title', 'custom_model')
    words = re.findall(r'\w+', title.lower())
    stops = {
        'the', 'a', 'an', 'for', 'and', 'or', 'of', 'in', 'on', 'at', 'to',
        'with', 'dashboard', 'monitor', 'analysis', 'report', 'tracker',
        'view', 'system', 'early', 'warning', 'performance', 'overview',
    }
    filtered = [w for w in words if w not in stops][:4]
    return '_'.join(filtered) if filtered else 'custom_model'



def _get_model_columns_from_db(model_name: str) -> list[str]:
    """Query PostgreSQL for the materialised model's columns."""
    conn = psycopg2.connect(
        host=os.environ.get('ANALYTICS_DB_HOST'),
        port=int(os.environ.get('ANALYTICS_DB_PORT', 5432)),
        user=os.environ.get('ANALYTICS_DB_USER'),
        password=os.environ.get('ANALYTICS_DB_PASSWORD'),
        dbname=os.environ.get('ANALYTICS_DB_NAME', 'analytics'),
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_schema = 'transformed_marts' AND table_name = %s
                   ORDER BY ordinal_position""",
                (model_name,),
            )
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def _extract_select_terms(sql: str) -> dict[str, str]:
    """
    Parse the SELECT clause into {alias: expression} pairs.
    Handles nested parentheses correctly. Returns empty dict on failure.
    """
    try:
        # Strip dbt config header and LIMIT
        sql = re.sub(r'\{\{[^}]+\}\}', '', sql)
        sql = re.sub(r'\s*LIMIT\s+\d+\s*;?\s*$', '', sql.strip(), flags=re.IGNORECASE)
        # Extract SELECT ... FROM
        m = re.search(r'SELECT\s+(.*?)\s+FROM\b', sql, re.IGNORECASE | re.DOTALL)
        if not m:
            return {}
        select_clause = m.group(1)
        # Split on commas at nesting depth 0
        terms: dict[str, str] = {}
        depth = 0
        current: list[str] = []
        for ch in select_clause:
            if ch == '(':
                depth += 1
                current.append(ch)
            elif ch == ')':
                depth -= 1
                current.append(ch)
            elif ch == ',' and depth == 0:
                _parse_select_term(''.join(current).strip(), terms)
                current = []
            else:
                current.append(ch)
        if current:
            _parse_select_term(''.join(current).strip(), terms)
        return terms
    except Exception:
        return {}


def _parse_select_term(term: str, out: dict[str, str]) -> None:
    """Parse a single SELECT term like 'SUM(x) AS alias' into out[alias] = expression."""
    m = re.match(r'^(.*?)\s+AS\s+(\w+)\s*$', term, re.IGNORECASE)
    if m:
        out[m.group(2).lower()] = m.group(1).strip()
    else:
        # bare column name
        alias = term.split('.')[-1].strip().lower()
        out[alias] = term.strip()


def _infer_metric_type(expr: str) -> Optional[str]:
    """
    Return the Lightdash metric type for a materialised column's SQL expression.

    After dbt materialises the model, the column holds the pre-computed result.
    Lightdash metrics define how to RE-aggregate that column across groups, so:
      SUM(...)             → 'sum'   (additive)
      COUNT(DISTINCT ...)  → 'sum'   (pre-aggregated counts are additive)
      COUNT(...)           → 'sum'   (pre-aggregated counts are additive)
      AVG(...)/AVERAGE(...)→ 'average'
      MIN(...)             → 'min'
      MAX(...)             → 'max'
      expression with multiple agg calls → 'number' (ratio/derived)
      CASE / window / plain column → None (dimension)
    """
    expr_up = expr.upper().strip()
    # CASE expressions produce categorical output → always a dimension
    if expr_up.startswith('CASE'):
        return None
    agg_re = re.compile(
        r'\b(SUM|COUNT|AVG|AVERAGE|MIN|MAX)\s*\(',
        re.IGNORECASE,
    )
    matches = agg_re.findall(expr_up)
    if not matches:
        return None  # plain column or CASE without agg → dimension
    if len(matches) > 1:
        return 'number'  # ratio/derived column (e.g. SUM/COUNT) — needs weighted avg via sql ref
    fn = matches[0].upper()
    if fn == 'SUM':
        return 'sum'
    if fn == 'COUNT':
        # Pre-aggregated counts are additive integers — re-aggregate with sum
        return 'sum'
    if fn in ('AVG', 'AVERAGE'):
        return 'average'
    if fn == 'MIN':
        return 'min'
    if fn == 'MAX':
        return 'max'
    return 'number'


def _build_weighted_sql(expr: str, expr_to_metric_key: dict[str, str]) -> str:
    """
    Replace raw SQL sub-expressions with ${metric_key} Lightdash references.
    Longest match first to avoid partial replacements.
    e.g. 'SUM(x) / NULLIF(COUNT(DISTINCT y), 0)' →
         '${x_sum} / NULLIF(${y_count_distinct}, 0)'
    """
    result = expr
    for raw, key in sorted(expr_to_metric_key.items(), key=lambda kv: len(kv[0]), reverse=True):
        result = result.replace(raw, f'${{{key}}}')
    return result


def _write_schema_file(
    dbt_path: str, model_name: str, columns: list[str], grain: list[str],
    sql_raw: Optional[str] = None,
) -> None:
    """Write a dbt schema YAML file for the scaffolded model."""
    # Build alias→expression map from raw SQL when available
    select_terms: dict[str, str] = _extract_select_terms(sql_raw) if sql_raw else {}

    # Pass 1: build expr→metric_key map for simple (non-number) metrics
    # so ratio columns can reference them as weighted avg components
    expr_to_metric_key: dict[str, str] = {}
    for col in columns:
        if _ID_COL_RE.search(col):
            continue
        expr = select_terms.get(col.lower(), '')
        mt = _infer_metric_type(expr) if expr else None
        if mt is not None and mt != 'number':
            expr_to_metric_key[expr] = f'{col}_{mt}'

    col_entries = []
    for col in columns:
        entry: dict = {'name': col, 'description': col.replace('_', ' ').title()}
        label = col.replace('_', ' ').title()

        expr = select_terms.get(col.lower(), '')
        # Plain column refs (e.g. "a.col", "col") give no aggregation info — treat as no SQL info
        is_plain_ref = not expr or bool(_PLAIN_COL_RE.match(expr.split('.')[-1].strip()))
        metric_type = _infer_metric_type(expr) if expr and not is_plain_ref else None

        is_id_col = bool(_ID_COL_RE.search(col))
        is_rank_col = bool(_RANK_COL_RE.search(col))

        if metric_type is not None and not is_id_col:
            metric_def: dict = {
                'type': metric_type,
                'label': label,
                'description': label,
                'groups': ['Metrics'],
            }
            if metric_type in ('sum', 'average', 'min', 'max', 'number'):
                metric_def['round'] = 2
            if metric_type == 'number':
                weighted_sql = _build_weighted_sql(expr, expr_to_metric_key)
                # Only emit sql: if substitution actually happened — otherwise fall back to average
                if '${' in weighted_sql:
                    metric_def['sql'] = weighted_sql
                else:
                    metric_def['type'] = 'average'
            entry['meta'] = {'metrics': {f'{col}_{metric_def["type"]}': metric_def}}

        elif is_id_col:
            # True entity ID column: dimension + count_distinct metric
            entry['meta'] = {
                'dimension': {
                    'type': 'string',
                    'label': label,
                    'description': label,
                    'groups': ['Dimensions'],
                },
                'metrics': {
                    f'{col}_count_distinct': {
                        'type': 'count_distinct',
                        'label': f'{label} (Unique)',
                        'description': f'Unique {label}',
                        'groups': ['Metrics'],
                    }
                },
            }

        elif expr and not is_plain_ref:
            # SQL parsing ran on a real expression and returned None → confirmed dimension (e.g. CASE)
            # If the column name also looks numeric (e.g. churned_customer_count = CASE...0/1),
            # expose a sum metric too so chart planners can reference it.
            dim_type = 'date' if _DATE_COL_RE.search(col) else ('number' if _NUM_COL_RE.search(col) else 'string')
            meta: dict = {
                'dimension': {
                    'type': dim_type,
                    'label': label,
                    'description': label,
                    'groups': ['Dimensions'],
                }
            }
            if _NUM_COL_RE.search(col) and not is_id_col:
                meta['metrics'] = {
                    f'{col}_sum': {
                        'type': 'sum',
                        'label': label,
                        'description': label,
                        'groups': ['Metrics'],
                        'round': 2,
                    }
                }
            entry['meta'] = meta

        else:
            # No SQL info (plain ref or CTE alias) — fall back to column-name heuristics
            # Rank columns are always dimensions regardless of name
            if is_rank_col:
                entry['meta'] = {
                    'dimension': {
                        'type': 'number',
                        'label': label,
                        'description': label,
                        'groups': ['Dimensions'],
                    }
                }
            elif _NUM_COL_RE.search(col) and not is_id_col:
                entry['meta'] = {
                    'metrics': {
                        f'{col}_sum': {
                            'type': 'sum',
                            'label': label,
                            'description': label,
                            'groups': ['Metrics'],
                            'round': 2,
                        }
                    }
                }
            else:
                dim_type = 'date' if _DATE_COL_RE.search(col) else 'string'
                entry['meta'] = {
                    'dimension': {
                        'type': dim_type,
                        'label': label,
                        'description': label,
                        'groups': ['Dimensions'],
                    }
                }

        col_entries.append(entry)

    schema_doc = {
        'version': 2,
        'models': [{
            'name': model_name,
            'meta': {'canonical': False, 'grain': grain},
            'description': f'Auto-scaffolded model: {model_name.replace("_", " ")}',
            'columns': col_entries,
        }],
    }
    schema_path = os.path.join(dbt_path, 'models', 'marts', f'{model_name}.yml')
    os.makedirs(os.path.dirname(schema_path), exist_ok=True)
    with open(schema_path, 'w') as f:
        yaml.dump(schema_doc, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _validate_sql(sql: str) -> Optional[str]:
    """Run EXPLAIN on raw SQL. Returns the error string if invalid, None if valid."""
    try:
        conn = psycopg2.connect(
            host=os.environ.get('ANALYTICS_DB_HOST'),
            port=int(os.environ.get('ANALYTICS_DB_PORT', 5432)),
            user=os.environ.get('ANALYTICS_DB_USER'),
            password=os.environ.get('ANALYTICS_DB_PASSWORD'),
            dbname=os.environ.get('ANALYTICS_DB_NAME', 'analytics'),
        )
        try:
            with conn.cursor() as cur:
                cur.execute(f"EXPLAIN {sql}")
        finally:
            conn.close()
        return None
    except Exception as exc:
        return str(exc)


def _build_model_question(prd, grain_cols: list[str]) -> str:
    """Compose a natural-language question for vn.generate_sql()."""
    parts = [f"Write a SQL aggregation query that covers these metrics: {', '.join(prd.metrics)}."]
    # Inject business definitions so vanna generates SQL matching the actual business logic
    metric_defs = getattr(prd, 'metric_definitions', {}) or {}
    if metric_defs:
        def_lines = '; '.join(f'"{k}" means {v}' for k, v in metric_defs.items())
        parts.append(f"Use these exact definitions: {def_lines}.")
    dims = getattr(prd, 'dimensions', []) or grain_cols
    if dims:
        parts.append(f"Group by: {', '.join(dims)}.")
    parts.append("Use the most granular available table. Do not add a LIMIT clause.")
    return ' '.join(parts)


def _source_table_for_sql(sql: str) -> str:
    """Return the appropriate source table name based on columns referenced in the SQL."""
    if re.search(r'\bcustomer_id\b', sql, re.IGNORECASE):
        return 'stg_orders'
    return 'daily_sales'


_BARE_REF_RE = re.compile(r"(?<!')\b(stg_\w+|raw_\w+)\b(?!')")


def _wrap_as_dbt_model(sql: str, model_name: str = '') -> str:
    """Strip LIMIT, replace schema-qualified refs with dbt refs, add config header.

    Handles 2-part (schema.table) and 3-part (db.schema.table) qualified names
    for any table under transformed_staging or transformed_marts, excluding the
    model being created (avoids circular self-references).
    Also converts bare stg_* / raw_* table names not already schema-qualified.
    """
    sql = re.sub(r'\s*LIMIT\s+\d+\s*;?\s*$', '', sql.strip(), flags=re.IGNORECASE)
    # Replace both 2-part and 3-part qualified refs under any transformed_* schema
    def _to_ref(m: re.Match) -> str:
        table = m.group(1)
        if model_name and table == model_name:
            return m.group(0)  # don't create a self-reference
        return "{{ ref('" + table + "') }}"
    sql = re.sub(r'(?:\w+\.)?transformed_\w+\.(\w+)', _to_ref, sql)
    # Catch bare stg_* / raw_* refs not already converted (no schema prefix)
    def _bare_to_ref(m: re.Match) -> str:
        table = m.group(1)
        if model_name and table == model_name:
            return m.group(0)
        return "{{ ref('" + table + "') }}"
    sql = _BARE_REF_RE.sub(_bare_to_ref, sql)
    return "{{ config(materialized='table') }}\n\n" + sql


def scaffold_model(prd, grain_cols: list[str], dbt_path: str, vn=None) -> tuple[Optional[dict], Optional[str]]:
    """
    Create a new dbt model using vn.generate_sql() to produce the SQL.

    Steps:
      1. Ask vn.generate_sql() for a query covering all PRD metrics
      2. Validate with EXPLAIN (up to 3 retries)
      3. Write models/marts/<name>.sql
      4. Run `dbt run --select <name>` (profiles.yml baked to ~/.dbt/)
      5. Query resulting columns from PostgreSQL
      6. Write models/marts/<name>.yml
      7. Invalidate _scan_cache

    Returns (model_dict, error_message).
    """
    model_name = _model_name_from_prd(prd)
    sql_path = os.path.join(dbt_path, 'models', 'marts', f'{model_name}.sql')

    if vn is None:
        return None, "vn (Vanna) instance required to generate model SQL"

    base_question = _build_model_question(prd, grain_cols)
    raw_sql = None
    last_error = None
    for attempt in range(3):
        question = base_question if attempt == 0 else (
            f"{base_question}\n\nPrevious attempt failed with this PostgreSQL error: {last_error}. "
            "Fix the SQL so it is valid PostgreSQL."
        )
        try:
            raw_sql = vn.generate_sql(question)
        except Exception as exc:
            return None, f"SQL generation failed: {exc}"
        last_error = _validate_sql(raw_sql)
        if last_error is None:
            break
    else:
        return None, f"SQL validation failed after 3 attempts. Last error: {last_error}"

    # If SQL references customer_id but FROM clause doesn't use stg_orders, regenerate
    # with an explicit source table hint so Vanna doesn't pick the pre-aggregated daily_sales.
    expected_source = _source_table_for_sql(raw_sql)
    if expected_source == 'stg_orders' and not re.search(r'\bstg_orders\b', raw_sql, re.IGNORECASE):
        corrected_question = (
            f"{base_question}\n\nIMPORTANT: You must SELECT FROM stg_orders (not daily_sales) "
            "because the query requires customer_id which is only available at order-level grain."
        )
        for attempt in range(3):
            question = corrected_question if attempt == 0 else (
                f"{corrected_question}\n\nPrevious attempt failed: {last_error}. Fix the SQL."
            )
            try:
                raw_sql = vn.generate_sql(question)
            except Exception as exc:
                return None, f"SQL generation failed: {exc}"
            last_error = _validate_sql(raw_sql)
            if last_error is None:
                break
        else:
            return None, f"SQL validation failed after source correction. Last error: {last_error}"

    sql = _wrap_as_dbt_model(raw_sql, model_name=model_name)
    os.makedirs(os.path.dirname(sql_path), exist_ok=True)
    with open(sql_path, 'w') as f:
        f.write(sql)

    try:
        # profiles.yml is baked to /root/.dbt/ in the Dockerfile — no --profiles-dir needed
        proc = subprocess.run(
            ['dbt', 'run',
             '--project-dir', dbt_path,
             '--target', 'scaffold',
             '--select', model_name],
            capture_output=True, text=True, timeout=180,
        )
        if proc.returncode != 0:
            try:
                os.remove(sql_path)
            except OSError:
                pass
            return None, f"dbt run failed:\n{(proc.stdout + proc.stderr)[-1500:]}"
    except Exception as exc:
        try:
            os.remove(sql_path)
        except OSError:
            pass
        return None, str(exc)

    try:
        columns = _get_model_columns_from_db(model_name)
    except Exception:
        columns = []

    try:
        _write_schema_file(dbt_path, model_name, columns, grain_cols, sql_raw=raw_sql)
    except Exception:
        pass

    _scan_cache.clear()

    schema_prefix = os.environ.get('DBT_SCHEMA_PREFIX', 'transformed_')
    return {
        'name': model_name,
        'db_schema': f'{schema_prefix}marts',
        'columns': columns,
        'metric_names': set(),
        'canonical': False,
        'grain': grain_cols,
        'description': '',
    }, None


# ── Data Modeler entry point ───────────────────────────────────────────────────

async def run_data_modeler(prd, dbt_path: str) -> DataModelResult:
    """
    Infer required grain from PRD, find the best existing model, check coverage.
    Returns needs_new_model=True + required_grain when no model covers the PRD.
    """
    required_grain = _infer_grain_from_prd(prd)

    # Augment grain from PRD dimensions using actual schema columns — no hardcoded list.
    # If the user named a dimension that is a physical column in any model (e.g. 'customer_id'),
    # include it directly. This handles compound names like 'customer_id' that re.findall
    # tokenises as one word and never matches the keyword 'customer' in _GRAIN_SIGNALS.
    models = _scan_models(dbt_path)
    all_cols = {c.lower() for m in models for c in m['columns']}
    grain_set = set(required_grain)
    for dim in getattr(prd, 'dimensions', []):
        col = dim.lower().strip()
        if col in all_cols:
            grain_set.add(col)
    required_grain = sorted(grain_set)

    best = find_best_model(dbt_path, required_grain=required_grain, metrics=prd.metrics)

    if best:
        uncovered = _uncovered_metrics(best, prd.metrics)
        return DataModelResult(
            model_name=best['name'],
            db_schema=best['db_schema'],
            columns=best['columns'],
            is_new=False,
            needs_new_model=bool(uncovered),
            uncovered_metrics=uncovered,
            required_grain=required_grain,
        )
    return DataModelResult(
        model_name='',
        db_schema='',
        columns=[],
        is_new=False,
        needs_new_model=True,
        required_grain=required_grain,
    )
