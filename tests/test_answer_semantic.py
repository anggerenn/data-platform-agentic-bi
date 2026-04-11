"""Unit tests for _summarise_rows and answer_semantic context building."""
import os
import sys
from unittest.mock import MagicMock

import pytest

# ── Path setup ───────────────────────────────────────────────────────────────
_vanna_dir = os.path.join(os.path.dirname(__file__), '..', 'vanna')
if _vanna_dir not in sys.path:
    sys.path.insert(0, os.path.abspath(_vanna_dir))

# Stub modules not installed locally
for _mod in ('vn', 'agents._model', 'pydantic_ai', 'pydantic_ai.models.openai'):
    sys.modules.setdefault(_mod, MagicMock())

# Stub make_model and VannaAI before importing router
sys.modules['agents._model'].make_model = MagicMock(return_value=MagicMock())
sys.modules['vn'].VannaAI = MagicMock

from agents.router import _summarise_rows  # noqa: E402


# ── _summarise_rows ──────────────────────────────────────────────────────────

def test_summarise_empty_returns_empty():
    assert _summarise_rows([], []) == ""
    assert _summarise_rows([], ["col"]) == ""


def test_summarise_numeric_columns():
    rows = [
        {"revenue": 100.0, "orders": 5},
        {"revenue": 200.0, "orders": 10},
        {"revenue": 300.0, "orders": 15},
    ]
    result = _summarise_rows(rows, ["revenue", "orders"])

    assert "3 row(s)" in result
    assert "revenue" in result
    assert "min=100" in result
    assert "max=300" in result
    assert "avg=200" in result
    assert "orders" in result


def test_summarise_categorical_few_distinct():
    rows = [
        {"city": "Jakarta", "category": "Electronics"},
        {"city": "Bandung", "category": "Electronics"},
        {"city": "Jakarta", "category": "Fashion"},
    ]
    result = _summarise_rows(rows, ["city", "category"])

    assert "Jakarta" in result
    assert "Bandung" in result
    assert "Electronics" in result
    assert "Fashion" in result


def test_summarise_categorical_many_distinct():
    rows = [{"city": f"City{i}"} for i in range(20)]
    result = _summarise_rows(rows, ["city"])

    assert "20 distinct values" in result
    assert "sample:" in result


def test_summarise_mixed_columns():
    rows = [
        {"customer": "Alice", "revenue": 500.0},
        {"customer": "Bob",   "revenue": 300.0},
    ]
    result = _summarise_rows(rows, ["customer", "revenue"])

    assert "Alice" in result
    assert "Bob" in result
    assert "min=300" in result
    assert "max=500" in result


def test_summarise_skips_null_values():
    rows = [
        {"revenue": 100.0},
        {"revenue": None},
        {"revenue": 200.0},
    ]
    result = _summarise_rows(rows, ["revenue"])
    # avg of [100, 200] = 150
    assert "avg=150" in result
