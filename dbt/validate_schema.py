"""
Semantic layer validator for dbt schema.yml.

Enforces conventions on all canonical models:
  - Dimensions: required fields, approved types
  - Metrics: required fields, approved groups, round present
  - Derived metrics (type: number): sql required, ${refs} resolve

Usage:
  python dbt/validate_schema.py                  # validates all schema files
  python dbt/validate_schema.py path/to/schema.yml

Exit code 1 if any errors found.
"""
import re
import sys
import yaml

SCHEMA_FILES = [
    'models/marts/schema.yml',
]

# Approved group names — add new ones here as the model grows
APPROVED_GROUPS = {
    'Time',
    'Geography',
    'Product',
    'Revenue',
    'Orders',
    'Customers',
}

APPROVED_DIMENSION_TYPES = {'date', 'string', 'number', 'boolean'}
APPROVED_METRIC_TYPES = {'sum', 'count', 'count_distinct', 'average', 'max', 'min', 'number'}

errors = []


def err(model, column, rule):
    errors.append(f"  [{model}.{column}] {rule}")


def validate_groups(groups, model, column):
    if not groups or not isinstance(groups, list):
        err(model, column, "missing or empty 'groups'")
        return
    for g in groups:
        if g not in APPROVED_GROUPS:
            err(model, column, f"unknown group '{g}' — approved: {sorted(APPROVED_GROUPS)}")


def validate_model(model_name, model_def):
    columns = model_def.get('columns', [])

    # Collect all metric keys for resolving derived refs
    all_metric_keys = set()
    for col in columns:
        for mk in col.get('meta', {}).get('metrics', {}).keys():
            all_metric_keys.add(mk)

    for col in columns:
        col_name = col.get('name', '?')
        meta = col.get('meta', {})

        # --- Dimension validation ---
        dim = meta.get('dimension')
        if dim:
            if not dim.get('label'):
                err(model_name, col_name, "dimension missing 'label'")
            if not dim.get('description'):
                err(model_name, col_name, "dimension missing 'description'")
            dim_type = dim.get('type')
            if not dim_type:
                err(model_name, col_name, "dimension missing 'type'")
            elif dim_type not in APPROVED_DIMENSION_TYPES:
                err(model_name, col_name, f"dimension type '{dim_type}' not in {APPROVED_DIMENSION_TYPES}")
            validate_groups(dim.get('groups'), model_name, col_name)

        # --- Metric validation ---
        for mk, mdef in meta.get('metrics', {}).items():
            label = f"{col_name}.{mk}"

            if not mdef.get('label'):
                err(model_name, label, "metric missing 'label'")
            if not mdef.get('description'):
                err(model_name, label, "metric missing 'description'")

            mtype = mdef.get('type')
            if not mtype:
                err(model_name, label, "metric missing 'type'")
            elif mtype not in APPROVED_METRIC_TYPES:
                err(model_name, label, f"metric type '{mtype}' not in {APPROVED_METRIC_TYPES}")

            validate_groups(mdef.get('groups'), model_name, label)

            if 'round' not in mdef:
                err(model_name, label, "metric missing 'round'")

            if mtype == 'number':
                sql = mdef.get('sql')
                if not sql:
                    err(model_name, label, "derived metric (type: number) missing 'sql'")
                else:
                    refs = re.findall(r'\$\{([^}]+)\}', sql)
                    for ref in refs:
                        if ref not in all_metric_keys:
                            err(model_name, label, f"sql references unknown metric key '${{{ref}}}'")


def validate_file(path):
    with open(path) as f:
        doc = yaml.safe_load(f)

    for model in doc.get('models', []):
        if not model.get('meta', {}).get('canonical', False):
            continue
        validate_model(model['name'], model)


def main():
    import os
    base = os.path.dirname(os.path.abspath(__file__))
    paths = sys.argv[1:] if len(sys.argv) > 1 else [
        os.path.join(base, p) for p in SCHEMA_FILES
    ]

    for path in paths:
        if not os.path.exists(path):
            print(f"Not found: {path}")
            sys.exit(1)
        validate_file(path)

    if errors:
        print(f"Schema validation FAILED — {len(errors)} error(s):\n")
        for e in errors:
            print(e)
        sys.exit(1)
    else:
        print(f"Schema validation passed ({len(paths)} file(s) checked).")


if __name__ == '__main__':
    main()
