"""Real temporary memory/meter with fake app: no provider or live operations."""
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'payload'))
from f_controls import install_controls
from f_memory import MemoryRuntime
from f_usage import UsageMeter
from test_f_bridge import FakeApp, FakeSocket
import test_f_bridge


class Request:
    method = 'POST'
    is_json = True
    def __init__(self, value=None, action='reset-meter'):
        self.headers = {'X-Build-F-Action': action}
        self.value = {'action': 'reset_display'} if value is None else value
    def get_json(self, **kwargs):
        return self.value


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.memory = MemoryRuntime(self.root / 'memory')
        self.meter = UsageMeter(self.root / 'usage.sqlite', {'models': {
            'anthropic:test': {'input': 2, 'output': 10}}}, budget_usd=1)
        self.g = {'app': FakeApp(), 'socketio': FakeSocket(), 'jsonify': lambda value: value,
                  '_ws_active_lock': threading.RLock(), '_ws_active': {}, '_thinking_inflight': 0,
                  'request': Request(), 'F_MEMORY': self.memory}
        install_controls(self.g, self.meter)
        self.reset = self.g['app'].view_functions['/api/build_f/meter/reset']
        self.models = self.g['app'].view_functions['/api/build_f/models']
    def tearDown(self):
        self.memory.close()
        self.tmp.cleanup()
    def test_meter_reset_is_non_destructive_and_keeps_lifetime_budget(self):
        ident = self.meter.begin('anthropic', 'test', 'deep', estimated_max_usd=.5)
        self.meter.finish(ident, {'input_tokens': 1000, 'output_tokens': 100})
        before = self.meter.summary()
        result = self.reset()
        self.assertTrue(result['ok'])
        self.assertEqual(result['usage']['display']['total_requests'], 0)
        self.assertEqual(result['usage']['total_requests'], before['total_requests'])
        self.assertEqual(result['usage']['budget'], before['budget'])
        self.assertEqual(self.memory.status()['events'], 0)
    def test_reset_rejects_pending_memory_between_model_calls(self):
        self.memory.archive({'id': 'pending', 'time': '2026-09-26', 'user': 'Keep this fact.', 'assistant': 'Okay.'})
        self.assertEqual(self.meter.summary()['active_requests'], 0)
        result, code = self.reset()
        self.assertEqual(code, 409)
        self.assertFalse(result['ok'])
        self.assertEqual(self.meter.summary()['display_window']['reset_count'], 0)
    def test_reset_never_reads_status_during_retrieval_or_write(self):
        original = self.memory.status
        self.memory.status = Mock(wraps=original)
        for name in ('_query_lock', '_worker_lock'):
            lock = getattr(self.memory, name)
            lock.acquire()
            try:
                self.assertEqual(self.reset()[1], 409)
            finally:
                lock.release()
        self.memory._active_retrievals = 1
        self.assertEqual(self.reset()[1], 409)
        self.memory._active_retrievals = 0
        self.memory.status.assert_not_called()
    def test_guards_reject_bad_origin_payload_and_active_request(self):
        for request, code in [(Request([]), 400), (Request({'action': 'delete'}), 400),
                              (Request(action='wrong'), 403)]:
            self.g['request'] = request
            self.assertEqual(self.reset()[1], code)
        self.g['request'] = Request()
        self.g['request'].headers['Origin'] = 'https://other.invalid'
        self.assertEqual(self.reset()[1], 403)
        self.g['request'] = Request()
        self.meter.begin('anthropic', 'test', 'deep', estimated_max_usd=.2)
        self.assertEqual(self.reset()[1], 409)
    def test_model_snapshot_and_registration_share_manager_lock(self):
        entered, proceed, attempted, finished = [threading.Event() for _ in range(4)]
        pair = []
        g = self.g
        g['DEFAULT_SURFACE_MODEL'], g['DEFAULT_DEEP_MODEL'] = 'old-surface', 'old-deep'
        class Manager:
            lock = threading.RLock()
            def set(self, selections):
                attempted.set()
                with self.lock, g['_ws_active_lock']:
                    if g['_ws_active']:
                        raise RuntimeError('Busy')
                    g['DEFAULT_SURFACE_MODEL'], g['DEFAULT_DEEP_MODEL'] = 'new-surface', 'new-deep'
                return {'ok': True, 'selections': selections}
        g['F_ROLES'] = Manager()
        def handler(data):
            surface = g['DEFAULT_SURFACE_MODEL']
            entered.set()
            self.assertTrue(proceed.wait(2))
            deep = g['DEFAULT_DEEP_MODEL']
            with g['_ws_active_lock']:
                g['_ws_active']['turn'] = {}
            pair.append((surface, deep))
        g['ws_message'] = handler
        install_controls(g, self.meter)
        route = g['app'].view_functions['/api/build_f/models']
        g['request'] = Request({'selections': {}}, action='select-models')
        outcomes = []
        def change():
            outcomes.append(route())
            finished.set()
        message = threading.Thread(target=g['socketio'].handlers['message'], args=({},))
        change_thread = threading.Thread(target=change)
        message.start()
        self.assertTrue(entered.wait(2))
        change_thread.start()
        self.assertTrue(attempted.wait(2))
        self.assertFalse(finished.is_set())
        proceed.set()
        message.join(2)
        change_thread.join(2)
        self.assertFalse(message.is_alive() or change_thread.is_alive())
        self.assertEqual(pair, [('old-surface', 'old-deep')])
        self.assertEqual(outcomes[0][1], 409)


class StatusObserverTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_f_bridge.BridgeTests()
        self.fixture.setUp()
        self.fixture.install()
        self.g = self.fixture.g
        self.memory = self.g['F_MEMORY']
        self.status = self.g['app'].view_functions['/api/build_f/status']
    def tearDown(self):
        self.fixture.tearDown()
    def test_busy_status_uses_labelled_cache_without_touching_journals(self):
        first = self.status()
        self.assertFalse(first['memory']['status_busy'])
        at = first['memory']['status_snapshot_at']
        original = self.memory.status
        self.memory.status = Mock(side_effect=AssertionError('Unsafe journal scan'))
        for name in ('_query_lock', '_worker_lock'):
            lock = getattr(self.memory, name)
            lock.acquire()
            try:
                busy = self.status()
                self.assertTrue(busy['memory']['status_busy'])
                self.assertTrue(busy['memory']['status_stale'])
                self.assertEqual(busy['memory']['status_snapshot_at'], at)
                self.assertEqual(busy['usage']['total_requests'], first['usage']['total_requests'])
            finally:
                lock.release()
        self.memory._active_retrievals = 1
        self.assertTrue(self.status()['memory']['status_busy'])
        self.memory._active_retrievals = 0
        self.memory.status.assert_not_called()
        self.memory.status = original
        self.assertFalse(self.status()['memory']['status_busy'])
        self.assertEqual(self.fixture.http.calls, [])
    def test_first_status_while_busy_has_no_invented_memory_counts(self):
        self.memory._query_lock.acquire()
        try:
            result = self.status()
        finally:
            self.memory._query_lock.release()
        self.assertTrue(result['memory']['status_busy'])
        self.assertFalse(result['memory']['status_stale'])
        self.assertIsNone(result['memory']['status_snapshot_at'])
        self.assertNotIn('events', result['memory'])
        self.assertIn('usage', result)


if __name__ == '__main__':
    unittest.main()
