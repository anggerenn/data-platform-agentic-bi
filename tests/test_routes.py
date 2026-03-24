"""
Flask route tests for /chat/stream, /dashboard/build, /export, /feedback.
Validates input validation, response shapes, and side-effects.
All LLM / DB / agent dependencies are mocked — no live services required.
"""
import json
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

# ── Environment + stubs (must happen before app import) ───────────────────────

os.environ.setdefault('LIGHTDASH_PUBLIC_URL', 'http://lightdash.test')
os.environ.setdefault('FEEDBACK_PATH', '/tmp/test-vanna-feedback.jsonl')

# dotenv is not installed in the test environment
sys.modules.setdefault('dotenv', MagicMock())
# agents.lightdash uses Python 3.10+ `X | None` union syntax; stub to stay 3.9-compatible
sys.modules.setdefault('agents.lightdash', MagicMock())

import app as _app  # noqa: E402  (imports after sys.modules setup)

# ── Inject a real-ish PRD so dashboard_build can construct and access fields ──

class _FakePRD:
    def __init__(self, **kwargs):
        self.title      = kwargs.get('title', 'Test Dashboard')
        self.objective  = kwargs.get('objective', 'track revenue')
        self.audience   = kwargs.get('audience', 'sales team')
        self.metrics    = kwargs.get('metrics', ['total_revenue'])
        self.dimensions = kwargs.get('dimensions', ['city'])
        self.action_items = kwargs.get('action_items', [])

    def model_dump(self):
        return {
            'title': self.title, 'objective': self.objective,
            'audience': self.audience, 'metrics': self.metrics,
            'dimensions': self.dimensions, 'action_items': self.action_items,
        }

_app.PRD = _FakePRD


# ── Shared fixtures ────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_state():
    _app.sessions.clear()
    _app.dpm_sessions.clear()
    yield


@pytest.fixture()
def client():
    _app.flask_app.config['TESTING'] = True
    with _app.flask_app.test_client() as c:
        yield c


# ─── /chat/stream ──────────────────────────────────────────────────────────────

class TestChatStream:
    def test_missing_message_returns_400(self, client):
        r = client.post('/chat/stream', json={})
        assert r.status_code == 400
        assert r.get_json()['error']

    def test_empty_message_returns_400(self, client):
        r = client.post('/chat/stream', json={'message': '   '})
        assert r.status_code == 400

    def test_valid_request_returns_event_stream(self, client):
        """Pre-fill the queue so the generator can yield without spawning a thread."""
        import queue as _queue_mod

        real_q = _queue_mod.Queue()
        real_q.put(('output', {
            'intent': 'semantic', 'text': 'Hello', 'sql': None,
            'data': None, 'columns': None, 'row_count': None,
            'chart_spec': None, 'session_id': 'sid1',
        }, []))

        with patch('app.queue.Queue', return_value=real_q), \
             patch('app.threading.Thread'):  # prevent thread spawn (queue already filled)
            r = client.post('/chat/stream', json={'message': 'hello', 'session_id': 'sid1'})

        assert r.status_code == 200
        assert 'text/event-stream' in r.content_type
        body = r.data.decode()
        assert 'data:' in body
        assert 'result' in body


# ─── /dashboard/build ─────────────────────────────────────────────────────────

class TestDashboardBuild:
    def test_missing_session_id_returns_400(self, client):
        r = client.post('/dashboard/build', json={})
        assert r.status_code == 400

    def test_unknown_session_id_returns_400(self, client):
        r = client.post('/dashboard/build', json={'dpm_session_id': 'does-not-exist'})
        assert r.status_code == 400

    def test_session_without_prd_returns_400(self, client):
        _app.dpm_sessions['sess1'] = {'summary': 'exploration', 'history': []}
        r = client.post('/dashboard/build', json={'dpm_session_id': 'sess1'})
        assert r.status_code == 400

    def test_needs_new_model_returns_sql_suggestion(self, client):
        _app.dpm_sessions['sess1'] = {
            'summary': 'exploration', 'history': [],
            'prd': {
                'title': 'LTV Dashboard', 'objective': 'track ltv',
                'audience': 'growth', 'metrics': ['customer_ltv'],
                'dimensions': ['customer_id'], 'action_items': [],
            },
        }
        mock_result = MagicMock()
        mock_result.needs_new_model = True
        _app.vn.generate_sql.return_value = 'SELECT SUM(customer_ltv) FROM stg_orders'

        with patch('app.asyncio') as mock_asyncio:
            mock_asyncio.run.return_value = mock_result
            r = client.post('/dashboard/build', json={'dpm_session_id': 'sess1'})

        assert r.status_code == 200
        data = r.get_json()
        assert data['needs_new_model'] is True
        assert 'message' in data

    def test_successful_build_returns_url(self, client, tmp_path):
        (tmp_path / 'lightdash' / 'prd').mkdir(parents=True)
        original_dbt_path = _app._DBT_PATH
        _app._DBT_PATH = str(tmp_path)

        _app.dpm_sessions['sess1'] = {
            'summary': 'exploration', 'history': [],
            'prd': {
                'title': 'Revenue by City', 'objective': 'track revenue',
                'audience': 'sales', 'metrics': ['total_revenue'],
                'dimensions': ['city'], 'action_items': [],
            },
        }
        mock_result = MagicMock()
        mock_result.needs_new_model = False
        mock_result.model_name = 'daily_sales'
        mock_result.model_dump.return_value = {
            'model_name': 'daily_sales', 'db_schema': 'transformed_marts',
            'columns': ['order_date', 'city', 'total_revenue'],
            'is_new': False, 'needs_new_model': False,
        }

        mock_verdict = MagicMock()
        mock_verdict.verdict = 'none'

        mock_guide = MagicMock()
        mock_guide.model_dump.return_value = {'overview': 'test guide'}

        try:
            with patch('app.asyncio') as mock_asyncio, \
                 patch('app.housekeeper_check', return_value=mock_verdict), \
                 patch('app.generate_guide', return_value=mock_guide), \
                 patch('app.create_dashboard', return_value={'url': 'http://lightdash.test/d/1'}):
                mock_asyncio.run.return_value = mock_result
                r = client.post('/dashboard/build', json={'dpm_session_id': 'sess1'})
        finally:
            _app._DBT_PATH = original_dbt_path

        assert r.status_code == 200
        data = r.get_json()
        assert data.get('url') == 'http://lightdash.test/d/1'


# ─── /export ──────────────────────────────────────────────────────────────────

class TestExport:
    def test_missing_sql_returns_400(self, client):
        r = client.post('/export', json={})
        assert r.status_code == 400

    def test_limit_stripped_before_execution(self, client):
        try:
            import pandas as pd
            df = pd.DataFrame({'col': ['val']})
        except ImportError:
            pytest.skip('pandas not available')

        captured = []
        original = _app.vn.run_sql

        def _capture(sql):
            captured.append(sql)
            return df

        _app.vn.run_sql = _capture
        try:
            client.post('/export', json={'sql': 'SELECT * FROM t LIMIT 20'})
        finally:
            _app.vn.run_sql = original

        assert captured, 'run_sql was never called'
        assert 'LIMIT' not in captured[0].upper()

    def test_returns_csv_with_header_and_rows(self, client):
        try:
            import pandas as pd
        except ImportError:
            pytest.skip('pandas not available')

        _app.vn.run_sql.return_value = pd.DataFrame({
            'city': ['London', 'Berlin'],
            'revenue': [1000.0, 2000.0],
        })
        r = client.post('/export', json={'sql': 'SELECT city, revenue FROM daily_sales'})

        assert r.status_code == 200
        assert 'text/csv' in r.content_type
        lines = r.data.decode().strip().split('\n')
        assert lines[0] == 'city,revenue'
        assert len(lines) == 3  # header + 2 data rows

    def test_sql_error_returns_500(self, client):
        _app.vn.run_sql.side_effect = Exception('table not found')
        try:
            r = client.post('/export', json={'sql': 'SELECT * FROM nonexistent'})
            assert r.status_code == 500
        finally:
            _app.vn.run_sql.side_effect = None


# ─── /feedback ────────────────────────────────────────────────────────────────

class TestFeedback:
    def test_missing_question_returns_400(self, client):
        r = client.post('/feedback', json={'sql': 'SELECT 1', 'rating': 'up'})
        assert r.status_code == 400

    def test_missing_sql_returns_400(self, client):
        r = client.post('/feedback', json={'question': 'test', 'rating': 'up'})
        assert r.status_code == 400

    def test_invalid_rating_returns_400(self, client):
        r = client.post('/feedback', json={'question': 'q', 'sql': 'SELECT 1', 'rating': 'meh'})
        assert r.status_code == 400

    def test_up_rating_calls_train(self, client):
        _app.vn.train.reset_mock()
        r = client.post('/feedback', json={
            'question': 'total revenue',
            'sql': 'SELECT SUM(total_revenue) FROM daily_sales',
            'rating': 'up',
        })
        assert r.status_code == 200
        assert r.get_json()['status'] == 'trained'
        _app.vn.train.assert_called_once_with(
            question='total revenue',
            sql='SELECT SUM(total_revenue) FROM daily_sales',
        )

    def test_down_rating_writes_jsonl_entry(self, client):
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.jsonl', delete=False
        ) as f:
            feedback_path = f.name

        try:
            with patch.dict(os.environ, {'FEEDBACK_PATH': feedback_path}):
                r = client.post('/feedback', json={
                    'question': 'bad query',
                    'sql': 'SELECT broken',
                    'rating': 'down',
                })
            assert r.status_code == 200
            assert r.get_json()['status'] == 'recorded'

            with open(feedback_path) as f:
                entry = json.loads(f.readline())
            assert entry['question'] == 'bad query'
            assert entry['sql'] == 'SELECT broken'
            assert 'timestamp' in entry
        finally:
            os.unlink(feedback_path)
