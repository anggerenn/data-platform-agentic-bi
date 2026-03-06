"""
Chart catalog — single source of truth for all supported chart types.

Each entry defines:
  description          : what the chart communicates (used in agent prompt)
  requires             : minimum column counts needed to build this chart
  row_limit            : max rows before this chart becomes unreadable (optional)
  dashboard_compatible : BI tools that natively render this chart type
                         - widget mode  : all charts usable regardless of this field
                         - dashboard mode: filter catalog to entries containing the target tool
  plotly_type          : how the frontend renders it (informational)
"""

import re
from datetime import date, datetime
from typing import Optional

CHART_CATALOG = {
    "big_number": {
        "description": "Highlight a single KPI value — best for one-row, one-metric results",
        "requires": {"row_count_max": 1, "num_cols_min": 1},
        "dashboard_compatible": ["lightdash"],
        "plotly_type": "big_number",
    },
    "bar": {
        "description": "Compare a metric across categories",
        "requires": {"cat_cols_min": 1, "num_cols_min": 1},
        "dashboard_compatible": ["lightdash"],
        "plotly_type": "bar",
    },
    "grouped_bar": {
        "description": "Compare a metric across categories broken down by a second dimension",
        "requires": {"cat_cols_min": 2, "num_cols_min": 1},
        "dashboard_compatible": ["lightdash"],
        "plotly_type": "grouped_bar",
    },
    "line": {
        "description": "Show a single metric trend over time (one line)",
        "requires": {"date_cols_min": 1, "num_cols_min": 1, "cat_cols_max": 0},
        "dashboard_compatible": ["lightdash"],
        "plotly_type": "line",
    },
    "grouped_line": {
        "description": "Show a metric trend over time split by a category — one line per group (e.g. revenue per city over time)",
        "requires": {"date_cols_min": 1, "num_cols_min": 1, "cat_cols_min": 1},
        "dashboard_compatible": ["lightdash"],
        "plotly_type": "grouped_line",
    },
    "area": {
        "description": "Show cumulative or stacked trends over time (single metric, no category split)",
        "requires": {"date_cols_min": 1, "num_cols_min": 1, "cat_cols_max": 0},
        "dashboard_compatible": ["lightdash"],
        "plotly_type": "area",
    },
    "scatter": {
        "description": "Show correlation between two numeric variables",
        "requires": {"num_cols_min": 2},
        "dashboard_compatible": ["lightdash"],
        "plotly_type": "scatter",
    },
    "pie": {
        "description": "Show part-to-whole composition — best with few categories",
        "requires": {"cat_cols_min": 1, "num_cols_min": 1},
        "row_limit": 8,
        "dashboard_compatible": ["lightdash"],
        "plotly_type": "pie",
    },
    "heatmap": {
        "description": "Show value intensity across two categorical dimensions",
        "requires": {"cat_cols_min": 2, "num_cols_min": 1},
        "dashboard_compatible": [],  # widget-only — not natively in Lightdash
        "plotly_type": "heatmap",
    },
}

_DATE_COL_RE = re.compile(
    r'(date|month|week|year|day|period|time|created|updated)', re.I
)
_DATE_VAL_RE = re.compile(r'^\d{4}-\d{2}-\d{2}')


def analyze_result(columns: list, rows: list) -> dict:
    """
    Classify each column as numeric, date, or categorical using the first row's
    values plus column-name heuristics.

    Returns a metadata dict — no raw data, safe to pass directly to an LLM prompt.
    """
    num_cols, date_cols, cat_cols = [], [], []

    for col in columns:
        sample_val = rows[0].get(col) if rows else None

        if isinstance(sample_val, (int, float)):
            num_cols.append(col)
        elif isinstance(sample_val, (date, datetime)):
            date_cols.append(col)
        elif isinstance(sample_val, str):
            if _DATE_COL_RE.search(col) or _DATE_VAL_RE.match(sample_val):
                date_cols.append(col)
            else:
                cat_cols.append(col)
        else:
            # null / unknown — fall back to column name heuristic
            if _DATE_COL_RE.search(col):
                date_cols.append(col)
            else:
                cat_cols.append(col)

    return {
        "row_count": len(rows),
        "num_cols": num_cols,
        "date_cols": date_cols,
        "cat_cols": cat_cols,
    }


def match_catalog(meta: dict, dashboard: Optional[str] = None) -> list:
    """
    Return chart type names that are structurally compatible with the metadata.

    Pass dashboard="lightdash" (or another tool name) to restrict results to
    charts that tool can render natively.
    """
    row_count = meta["row_count"]
    n_num = len(meta["num_cols"])
    n_date = len(meta["date_cols"])
    n_cat = len(meta["cat_cols"])

    compatible = []
    for name, spec in CHART_CATALOG.items():
        req = spec["requires"]

        if dashboard and dashboard not in spec["dashboard_compatible"]:
            continue

        if req.get("row_count_max") and row_count > req["row_count_max"]:
            continue
        if req.get("num_cols_min", 0) > n_num:
            continue
        if req.get("date_cols_min", 0) > n_date:
            continue
        if req.get("cat_cols_min", 0) > n_cat:
            continue
        if "cat_cols_max" in req and n_cat > req["cat_cols_max"]:
            continue
        if spec.get("row_limit") and row_count > spec["row_limit"]:
            continue

        compatible.append(name)

    return compatible
