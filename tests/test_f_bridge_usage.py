"""Offline provider-adapter contract tests using the real pricing and usage ledger."""
import ast
import contextlib
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

PAYLOAD = Path(__file__).resolve().parents[1] / 'payload'
if not PAYLOAD.is_dir():
    PAYLOAD = Path(__file__).resolve().parents[1] / 'brain'   # repo layout
CONFIG_DIR = PAYLOAD if (PAYLOAD / 'pricing.json').exists() else PAYLOAD.parent / 'config'
sys.path.insert(0, str(PAYLOAD))
from f_usage import UsageMeter, BudgetExceeded


class Response:
    def __init__(self, data=None, events=None, close_error=False):
        self.data = data
        self.events = events or []
        self.close_error = close_error
        self.closed = False

    def raise_for_status(self):
        pass

    def json(self):
        return self.data

    def iter_lines(self, **kwargs):
        for event in self.events:
            yield 'data: ' + (event if isinstance(event, str) else json.dumps(event))

    def close(self):
        self.closed = True
        if self.close_error:
            raise RuntimeError('close failed')


class HTTP:
    def __init__(self):
        self.responses = []
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class BridgeAccountingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.pricing = json.loads((CONFIG_DIR / 'pricing.json').read_text())
        self.meter = UsageMeter(self.folder / 'usage.sqlite', self.pricing, budget_usd=2)
        self.http = HTTP()
        self.g = {
            'ANTHROPIC_BASE': 'https://example.invalid/anthropic',
            '_anthropic_headers': lambda key=None: {'x-api-key': 'fake-secret'},
            '_anthropic_body': lambda parts, model, **kw: {'model': model, 'messages': [], 'max_tokens': 4096},
            '_openai_headers': lambda provider, **kw: ('https://example.invalid/xai', {'Authorization': 'fake-secret'}),
            '_openai_messages': lambda parts, **kw: [{'role': 'user', 'content': 'test'}],
            'build_messages': lambda message: {'user_content': message, '_f_phase': 'deep', '_f_turn_id': 'test-turn'},
        }
        self.namespace = {'contextlib': contextlib, 'json': json, 'local': threading.local(),
                          'pricing': self.pricing, 'meter': self.meter, 'http': self.http,
                          'folder': self.folder, 'g': self.g}
        tree = ast.parse((PAYLOAD / 'f_bridge.py').read_text(encoding='utf-8'))
        install = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'install')
        names = {'scope', 'start', 'json_call', 'stream', 'call_anthropic', 'call_llm'}
        functions = [node for node in install.body if isinstance(node, ast.FunctionDef) and node.name in names]
        self.assertEqual({n.name for n in functions}, names)
        exec(compile(ast.Module(body=functions, type_ignores=[]), '<bridge provider functions>', 'exec'), self.namespace)

    def tearDown(self):
        self.tmp.cleanup()

    def test_actual_pricing_keys_and_embedding_usage(self):
        self.assertIn('checked_at', self.meter.summary()['pricing_metadata'])
        self.http.responses.append(Response({'usageMetadata': {'promptTokenCount': 1000}, 'embeddings': []}))
        self.namespace['json_call']('gemini', 'gemini-embedding-2', 'https://example.invalid', {}, {'requests': []}, 'query_embedding')
        result = self.meter.summary()
        self.assertAlmostEqual(result['priced_known_usd'], .0002)
        self.assertEqual(result['incomplete_requests'], 0)
        self.assertEqual(result['unpriced_requests'], 0)
        self.assertEqual(result['recent_requests'][0]['provider'], 'gemini')

    def test_stream_usage_snapshots_keep_input_and_hour_cache(self):
        response = Response(events=[
            {'type': 'message_start', 'message': {'usage': {'input_tokens': 1000, 'output_tokens': 0,
             'cache_creation_input_tokens': 100, 'cache_creation': {'ephemeral_1h_input_tokens': 100, 'ephemeral_5m_input_tokens': 0}}}},
            {'type': 'content_block_delta', 'delta': {'text': 'hello'}},
            {'type': 'message_delta', 'usage': {'input_tokens': 0, 'output_tokens': 100}},
            {'type': 'message_stop'}], close_error=True)
        self.http.responses.append(response)
        output = ''.join(self.namespace['stream']('anthropic', {'_f_phase': 'deep'}, 'claude-sonnet-5'))
        self.assertEqual(output, 'hello')
        self.assertTrue(response.closed)
        self.assertAlmostEqual(self.meter.summary()['priced_known_usd'], .0034)
        self.assertEqual(self.meter.summary()['incomplete_requests'], 0)

    def test_embedding_missing_usage_stays_unpriced_without_holding_budget(self):
        self.http.responses.append(Response({'embeddings': []}))
        self.namespace['json_call']('gemini', 'gemini-embedding-2', 'https://example.invalid', {}, {'requests': []}, 'query_embedding')
        result = self.meter.summary()
        self.assertEqual(result['unpriced_requests'], 1)
        self.assertEqual(result['incomplete_requests'], 1)
        self.assertIsNone(result['recent_requests'][0]['cost_usd'])
        # settle/drain update: a completed-but-unpriced call no longer parks its
        # reservation forever — it stays visible as unpriced/incomplete instead.
        self.assertEqual(result['budget']['held_usd'], 0)

    def test_cancel_before_dispatch_makes_no_request_or_reservation(self):
        cancel = threading.Event()
        cancel.set()
        with self.namespace['scope']('deep', cancel=cancel):
            self.assertEqual(list(self.namespace['stream']('anthropic', {}, 'claude-sonnet-5')), [])
        self.assertEqual(self.http.calls, [])
        self.assertEqual(self.meter.summary()['total_requests'], 0)
        self.assertEqual(self.meter.summary()['budget']['held_usd'], 0)

    def test_disk_archive_failure_does_not_erase_paid_usage(self):
        (self.folder / 'responses').write_text('blocks directory')
        self.http.responses.append(Response({'usage': {'input_tokens': 100, 'output_tokens': 10}}))
        with self.assertRaises(RuntimeError):
            self.namespace['json_call']('anthropic', 'claude-sonnet-5', 'https://example.invalid', {}, {'max_tokens': 4096}, 'memory_write')
        summary = self.meter.summary()
        self.assertAlmostEqual(summary['priced_known_usd'], .0003)
        self.assertEqual(summary['incomplete_requests'], 0)
        self.assertEqual(summary['recent_requests'][0]['status'], 'complete')

    def test_nonstreaming_xai_fallback_is_metered_and_bounded(self):
        self.http.responses.append(Response({'usage': {'prompt_tokens': 100, 'completion_tokens': 20},
                                             'choices': [{'message': {'content': 'hello'}}]}))
        answer = self.namespace['call_llm']('test', 'grok-4.3', 'xai')
        self.assertEqual(answer, 'hello')
        self.assertEqual(self.http.calls[0][1]['json']['max_tokens'], 1024)
        receipt = self.meter.summary()['recent_requests'][0]
        self.assertEqual((receipt['phase'], receipt['turn_id']), ('deep', 'test-turn'))
        self.assertAlmostEqual(receipt['cost_usd'], .000175)

    def test_cancelled_generator_preserves_partial_usage_and_reserve(self):
        response = Response(events=[
            {'type': 'message_start', 'message': {'usage': {'input_tokens': 100, 'output_tokens': 0}}},
            {'type': 'content_block_delta', 'delta': {'text': 'hello'}},
            {'type': 'message_stop'}])
        self.http.responses.append(response)
        generator = self.namespace['stream']('anthropic', {}, 'claude-sonnet-5')
        self.assertEqual(next(generator), 'hello')
        generator.close()
        summary = self.meter.summary()
        self.assertEqual(summary['recent_requests'][0]['status'], 'cancelled')
        self.assertEqual(summary['recent_requests'][0]['pricing_status'], 'partial')
        self.assertGreater(summary['budget']['held_usd'], 0)
        self.assertTrue(response.closed)

    def test_stream_without_final_marker_is_explicitly_incomplete(self):
        self.http.responses.append(Response(events=[{'usage': {'prompt_tokens': 100, 'completion_tokens': 20}, 'choices': []}]))
        with self.assertRaises(RuntimeError):
            list(self.namespace['stream']('xai', {}, 'grok-4.3'))
        summary = self.meter.summary()
        self.assertEqual(summary['recent_requests'][0]['status'], 'unknown')
        self.assertEqual(summary['incomplete_requests'], 1)
        self.assertAlmostEqual(summary['priced_known_usd'], .000175)

    def test_unpriced_model_unbounded_output_and_oversize_are_refused_before_http(self):
        with self.assertRaises(BudgetExceeded):
            list(self.namespace['stream']('anthropic', {}, 'unknown-model'))
        with self.assertRaises(ValueError):
            self.namespace['json_call']('xai', 'grok-4.3', 'https://example.invalid', {}, {}, 'deep')
        with self.assertRaises(ValueError):
            self.namespace['json_call']('anthropic', 'claude-sonnet-5', 'https://example.invalid', {},
                                        {'max_tokens': 4096, 'message': 'x' * 130000}, 'deep')
        self.assertEqual(self.http.calls, [])
        self.assertEqual(self.meter.summary()['total_requests'], 0)

    def test_unsupported_provider_fallback_routes_never_call_http(self):
        for provider in ('groq', 'cerebras', 'ollama', 'gemini'):
            with self.subTest(provider=provider):
                with self.assertRaises(ValueError):
                    list(self.namespace['stream'](provider, {}, 'example'))
                with self.assertRaises(ValueError):
                    self.namespace['call_llm']('test', 'example', provider)
        self.assertEqual(self.http.calls, [])


if __name__ == '__main__':
    unittest.main()
