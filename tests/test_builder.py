"""Unit tests for _uncovered_metrics() in vanna/agents/builder.py."""
import pytest
from agents.builder import _uncovered_metrics

# Representative model matching transformed_marts.daily_sales:
# - no customer_id column (aggregated grain)
DAILY_SALES = {
    'name': 'daily_sales',
    'columns': [
        'order_date', 'category', 'city',
        'order_count', 'customer_count', 'total_revenue',
        'average_order_value', 'revenue_per_customer', 'units_per_order',
    ],
    'metric_names': {
        'order_count_sum', 'total_revenue_sum', 'average_order_value',
        'revenue_per_customer', 'customer_count_sum',
    },
    'description': 'Daily aggregated sales by category and city',
    'grain': ['order_date', 'category', 'city'],
    'canonical': True,
}

# Representative model matching transformed_staging.stg_orders:
# - has customer_id column (row-level grain)
STG_ORDERS = {
    'name': 'stg_orders',
    'columns': [
        'order_id', 'customer_id', 'order_date', 'category', 'city',
        'amount', 'quantity', 'line_total',
    ],
    'metric_names': set(),
    'description': 'Staging orders table at order-line grain',
    'grain': ['order_id'],
    'canonical': False,
}


# ── Hard grain signal checks (daily_sales — no customer_id) ───────────────────

def test_active_customer_count_uncovered_on_daily_sales():
    result = _uncovered_metrics(DAILY_SALES, ['Active Customer Count'])
    assert result == ['Active Customer Count']


def test_inactive_customer_count_uncovered_on_daily_sales():
    result = _uncovered_metrics(DAILY_SALES, ['Inactive Customer Count'])
    assert result == ['Inactive Customer Count']


def test_churn_rate_uncovered_on_daily_sales():
    result = _uncovered_metrics(DAILY_SALES, ['Customer Churn Rate'])
    assert result == ['Customer Churn Rate']


def test_retention_rate_uncovered_on_daily_sales():
    result = _uncovered_metrics(DAILY_SALES, ['Customer Retention Rate'])
    assert result == ['Customer Retention Rate']


def test_leaderboard_uncovered_on_daily_sales():
    result = _uncovered_metrics(DAILY_SALES, ['Customer Leaderboard by Revenue'])
    assert result == ['Customer Leaderboard by Revenue']


# ── Metrics that daily_sales CAN cover ────────────────────────────────────────

def test_total_revenue_covered_on_daily_sales():
    result = _uncovered_metrics(DAILY_SALES, ['Total Revenue per Customer'])
    assert result == []


def test_order_count_covered_on_daily_sales():
    result = _uncovered_metrics(DAILY_SALES, ['Order Count per Customer'])
    assert result == []


def test_average_order_value_covered_on_daily_sales():
    result = _uncovered_metrics(DAILY_SALES, ['Average Order Value'])
    assert result == []


def test_customer_count_covered_on_daily_sales():
    # "Customer Count" — no hard signal keywords; customer_count column present
    result = _uncovered_metrics(DAILY_SALES, ['Customer Count'])
    assert result == []


# ── Hard grain signals + keyword score on stg_orders ─────────────────────────
# stg_orders has customer_id so the hard check passes, but it has no
# 'active', 'count', or 'revenue' column — keyword score still flags these.
# That triggers scaffold_model, which builds a new customer-summary mart.

def test_active_customer_count_uncovered_on_stg_orders():
    # customer_id present → hard check passes, but 'active'/'count' keywords
    # don't match any stg_orders column → score 1/3 < 0.5 → uncovered.
    result = _uncovered_metrics(STG_ORDERS, ['Active Customer Count'])
    assert result == ['Active Customer Count']


def test_churn_rate_covered_on_stg_orders():
    # 'rate' is a filler word → keywords = ['customer', 'churn'] (2 terms).
    # 'customer' matches customer_id → score 1/2 = 0.5 → NOT uncovered.
    result = _uncovered_metrics(STG_ORDERS, ['Customer Churn Rate'])
    assert result == []


def test_leaderboard_uncovered_on_stg_orders():
    # hard check passes (customer_id present); 'revenue' doesn't match
    # stg_orders columns (line_total, amount) → score 1/3 < 0.5 → uncovered.
    result = _uncovered_metrics(STG_ORDERS, ['Customer Leaderboard by Revenue'])
    assert result == ['Customer Leaderboard by Revenue']


# ── Mixed PRD: some covered, some not ────────────────────────────────────────

def test_mixed_prd_returns_only_uncovered():
    metrics = [
        'Total Revenue per Customer',    # covered
        'Order Count per Customer',      # covered
        'Average Order Value',           # covered
        'Active Customer Count',         # uncovered — hard signal
        'Inactive Customer Count',       # uncovered — hard signal
        'Customer Retention Rate',       # uncovered — hard signal
    ]
    result = _uncovered_metrics(DAILY_SALES, metrics)
    assert set(result) == {'Active Customer Count', 'Inactive Customer Count', 'Customer Retention Rate'}


def test_empty_metrics_returns_empty():
    assert _uncovered_metrics(DAILY_SALES, []) == []
