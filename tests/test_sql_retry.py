"""Unit tests for VannaAI.validate_sql and generate_sql_with_retry."""
import importlib.util
import os
import sys
from unittest.mock import MagicMock

import pytest

# ── Stub heavy deps before loading vn.py ────────────────────────────────────
# ChromaDB_VectorStore and OpenAI_Chat must be real Python classes (not
# MagicMock) to avoid metaclass conflicts when VannaAI subclasses them.

class _FakeChromaBase:
    def __init__(self, *args, **kwargs):
        pass

class _FakeOpenAIBase:
    def __init__(self, *args, **kwargs):
        pass

_chromadb_mod = MagicMock()
_chromadb_mod.ChromaDB_VectorStore = _FakeChromaBase
_openai_mod = MagicMock()
_openai_mod.OpenAI_Chat = _FakeOpenAIBase

sys.modules['vanna.legacy.chromadb'] = _chromadb_mod
sys.modules['vanna.legacy.openai'] = _openai_mod
sys.modules.setdefault('vanna', MagicMock())
sys.modules.setdefault('vanna.legacy', MagicMock())
sys.modules.setdefault('chromadb', MagicMock())
sys.modules.setdefault('openai', MagicMock())
sys.modules.setdefault('psycopg2', MagicMock())
sys.modules.setdefault('pandas', MagicMock())

_vn_path = os.path.join(os.path.dirname(__file__), '..', 'vanna', 'vn.py')
_spec = importlib.util.spec_from_file_location('vn_real', _vn_path)
_vn_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_vn_mod)
VannaAI = _vn_mod.VannaAI


def _make_vanna():
    """Return a VannaAI instance bypassing __init__, with DB connection mocked."""
    vn = object.__new__(VannaAI)
    vn._conn = MagicMock()
    vn._conn.closed = False
    vn._conn_kwargs = {}
    return vn


# ── validate_sql ────────────────────────────────────────────────────────────

def test_validate_sql_ok():
    vn = _make_vanna()
    cursor = MagicMock()
    vn._conn.cursor.return_value.__enter__ = lambda s: cursor
    vn._conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    ok, err = vn.validate_sql("SELECT 1")

    assert ok is True
    assert err == ""
    cursor.execute.assert_called_once_with("EXPLAIN SELECT 1")


def test_validate_sql_bad():
    vn = _make_vanna()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("relation does not exist")
    vn._conn.cursor.return_value.__enter__ = lambda s: cursor
    vn._conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    ok, err = vn.validate_sql("SELECT * FROM nonexistent")

    assert ok is False
    assert "relation does not exist" in err


# ── generate_sql_with_retry ─────────────────────────────────────────────────

def test_retry_succeeds_first_attempt():
    vn = _make_vanna()
    vn.generate_sql = MagicMock(return_value="SELECT 1")
    vn.validate_sql = MagicMock(return_value=(True, ""))

    result = vn.generate_sql_with_retry("how many orders?")

    assert result == "SELECT 1"
    vn.generate_sql.assert_called_once_with("how many orders?")


def test_retry_succeeds_on_second_attempt():
    vn = _make_vanna()
    bad_sql = "SELECT * FROM nonexistent"
    good_sql = "SELECT COUNT(*) FROM transformed_marts.daily_sales"

    vn.generate_sql = MagicMock(side_effect=[bad_sql, good_sql])
    vn.validate_sql = MagicMock(side_effect=[
        (False, 'relation "nonexistent" does not exist'),
        (True, ""),
    ])

    result = vn.generate_sql_with_retry("how many orders?")

    assert result == good_sql
    assert vn.generate_sql.call_count == 2
    # Second prompt must include the error and the bad SQL
    second_prompt = vn.generate_sql.call_args_list[1][0][0]
    assert 'relation "nonexistent" does not exist' in second_prompt
    assert bad_sql in second_prompt


def test_retry_raises_after_max_attempts():
    vn = _make_vanna()
    vn.generate_sql = MagicMock(return_value="SELECT * FROM bad_table")
    vn.validate_sql = MagicMock(return_value=(False, "relation does not exist"))

    with pytest.raises(ValueError, match="SQL generation failed after 3 attempts"):
        vn.generate_sql_with_retry("how many orders?", max_attempts=3)

    assert vn.generate_sql.call_count == 3


def test_retry_returns_only_validated_sql():
    """Only SQL that passed EXPLAIN is returned (and thus eligible for caching)."""
    vn = _make_vanna()
    bad_sql = "SELECT * FROM bad"
    good_sql = "SELECT COUNT(*) FROM transformed_marts.daily_sales"

    vn.generate_sql = MagicMock(side_effect=[bad_sql, good_sql])
    vn.validate_sql = MagicMock(side_effect=[(False, "relation does not exist"), (True, "")])

    result = vn.generate_sql_with_retry("how many orders?")
    assert result == good_sql
