"""
Pytest configuration: path setup and module stubs.

`vanna/app.py` calls `get_vanna()` at module level, which connects to ClickHouse.
Stub `vn` in sys.modules before any test imports so app.py can be imported
in isolation without a live ClickHouse instance.
"""
import os
import sys
from unittest.mock import MagicMock

# Make vanna/ importable as a package root.
# Support two layouts: repo root (tests/../vanna) and Docker container (/app).
_candidates = [
    os.path.join(os.path.dirname(__file__), '..', 'vanna'),
    '/app',
]
for _c in _candidates:
    if os.path.isdir(_c) and os.path.exists(os.path.join(_c, 'app.py')):
        sys.path.insert(0, os.path.abspath(_c))
        break

# Stub modules not available in local (non-Docker) environment
_mock_vanna = MagicMock()
sys.modules['vn'] = MagicMock(get_vanna=lambda: _mock_vanna, VannaLite=MagicMock)
sys.modules.setdefault('psycopg2', MagicMock())
