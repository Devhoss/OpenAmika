import copy
import json
from pathlib import Path
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'payload'))
from f_openrouter import (OpenRouterClient, OpenRouterError, embedding_config, load_catalog,
                          role_config, make_chat_body, estimate_reservation, normalize_usage)

CATALOG = load_catalog(ROOT / 'config' / 'openrouter_models.json')
MESSAGES = [{'role': 'system', 'content': 'Native persona'}, {'role': 'user', 'content': 'Hello'}]


class Response:
    def __init__(self, data=None, events=None, fail=None, close_fail=False):
        self.data, self.events, self.fail = data, events, fail
        self.closed, self.close_fail = False, close_fail

    def raise_for_status(self):
        if self.fail:
            raise self.fail

    def json(self):
        return self.data

    def iter_lines(self, decode_unicode=True):
        for event in self.events:
            if isinstance(event, Exception):
                raise event
            yield 'data: ' + (event if isinstance(event, str) else json.dumps(event))
            yield ''

    def close(self):
        self.closed = True
        if self.close_fail:
            raise OSError('fake close failure')


class HTTP:
    def __init__(self, response):
        self.response, self.posts = response, []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return self.response


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.cfg = role_config(CATALOG, 'deepseek/deepseek-v4-flash-0731', 'surface')
        self.usage = {'prompt_tokens': 100, 'completion_tokens': 10, 'cost': 0.00001,
                      'prompt_tokens_details': {'cached_tokens': 25, 'cache_write_tokens': 5},
                      'completion_tokens_details': {'reasoning_tokens': 6},
                      'cost_details': {'upstream_inference_cost': .004}}

    def client(self, response, before=None, sink=None):
        self.receipts, self.requests = [], []
        def reserve(request):
            self.requests.append(request)
            return 'reserved'
        http = HTTP(response)
        client = OpenRouterClient(http, 'fake-not-a-key', before or reserve,
                                  lambda handle, receipt: self.receipts.append((handle, receipt)), event_sink=sink)
        return client, http

    def response(self, usage=None, provider='DeepInfra'):
        return {'id': 'gen-test', 'model': self.cfg['model_id'], 'provider': provider,
                'usage': self.usage if usage is None else usage,
                'choices': [{'message': {'content': 'Actual speech', 'reasoning': 'Private analysis'}, 'finish_reason': 'stop'}]}

    def stream_events(self):
        return [{'id': 'gen-test', 'model': self.cfg['model_id'], 'provider': 'DeepInfra',
                 'choices': [{'delta': {'reasoning': 'Never speak this', 'reasoning_details': [{'text': 'Private'}]}}]},
                {'choices': [{'delta': {'content': 'reply: hello'}}]},
                {'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
                {'choices': [], 'usage': self.usage}, '[DONE]']

    def test_provider_pinned_no_fallback_bounded_price(self):
        body = make_chat_body(self.cfg, MESSAGES)
        self.assertEqual(body['provider']['only'], ['deepinfra/fp8'])
        self.assertFalse(body['provider']['allow_fallbacks'])
        self.assertTrue(body['provider']['require_parameters'])
        self.assertEqual(body['provider']['quantizations'], ['fp8'])
        self.assertEqual(body['provider']['max_price']['prompt'], .06)
        self.assertEqual(body['reasoning'], {'enabled': False, 'exclude': True})
        self.assertEqual(body['max_tokens'], 1024)

    def test_always_thinking_gets_low_and_larger_total_cap(self):
        config = role_config(CATALOG, 'z-ai/glm-5.3-flash', 'deep')
        self.assertEqual(config['reasoning'], {'effort': 'low', 'exclude': True})
        self.assertEqual(config['max_tokens'], 8192)
        with self.assertRaises(ValueError):
            role_config(CATALOG, 'z-ai/glm-5.3-flash', 'surface', reasoning_enabled=False)

    def test_unknown_unpriced_and_unsupported_schema_rejected(self):
        with self.assertRaises(ValueError):
            role_config(CATALOG, 'unreviewed/model', 'deep')
        bad = copy.deepcopy(CATALOG)
        del bad[self.cfg['model_id']]['usd_per_million_tokens']['prompt']
        with self.assertRaises(ValueError):
            role_config(bad, self.cfg['model_id'], 'surface')
        with self.assertRaises(ValueError):
            role_config(CATALOG, 'qwen/qwen3.7-flash', 'memory_write')

    def test_schema_auto_for_memory_and_quote_fields_required(self):
        config = role_config(CATALOG, self.cfg['model_id'], 'memory_extraction')
        body = make_chat_body(config, MESSAGES, stream=False)
        schema = body['response_format']['json_schema']
        self.assertTrue(schema['strict'])
        self.assertIn('quotes', schema['schema']['properties']['facts']['items']['required'])

    def test_non_reasoning_models_receive_no_thinking_parameter(self):
        config = role_config(CATALOG, 'qwen/qwen3-235b-a22b-2507', 'surface')
        self.assertNotIn('reasoning', make_chat_body(config, MESSAGES))

    def test_reservation_includes_largest_clock_or_context_tier(self):
        # Catalog-driven (the snapshot's absolute prices move between research passes):
        # a single-tier row reserves at exactly its listed price; a tiered row (clock /
        # context discounts) reserves at the LARGEST tier, at or above the listed price.
        config = role_config(CATALOG, 'deepseek/deepseek-v4.1-flash', 'surface')
        listed = CATALOG['deepseek/deepseek-v4.1-flash']['usd_per_million_tokens']
        self.assertAlmostEqual(config['max_price']['prompt'], listed['prompt'])
        self.assertAlmostEqual(config['max_price']['completion'], listed['completion'])
        qwen = role_config(CATALOG, 'qwen/qwen3.7-flash', 'surface')
        qlisted = CATALOG['qwen/qwen3.7-flash']['usd_per_million_tokens']
        self.assertGreaterEqual(qwen['max_price']['prompt'], qlisted['prompt'])
        self.assertGreaterEqual(qwen['reservation_input_rate'], qwen['max_price']['prompt'])
        self.assertGreater(estimate_reservation(config, make_chat_body(config, MESSAGES)), 0)

    def test_size_and_output_caps_reject_before_any_network(self):
        with self.assertRaises(ValueError):
            role_config(CATALOG, self.cfg['model_id'], 'surface', max_tokens=9000)
        with self.assertRaises(ValueError):
            make_chat_body(self.cfg, [{'role': 'user', 'content': 'x'*120001}])

    def test_cost_not_upstream_or_reasoning_doublecount(self):
        result = normalize_usage(self.usage)
        self.assertTrue(result['complete'])
        self.assertEqual(result['cost_usd'], .00001)
        self.assertEqual(result['tokens']['total_tokens'], 110)
        self.assertEqual(result['tokens']['input_uncached_tokens'], 70)

    def test_missing_nan_negative_and_boolean_cost_are_unknown(self):
        for bad in (None, float('nan'), -1, True, '0.01'):
            with self.subTest(bad=bad):
                result = normalize_usage(dict(self.usage, cost=bad))
                self.assertFalse(result['complete'])
                self.assertIsNone(result['cost_usd'])
        self.assertEqual(normalize_usage(dict(self.usage, cost=0))['cost_usd'], 0)

    def test_observed_reasoning_count_conventions_are_warnings_not_false_failures(self):
        for reasoning,completion in ((485,299),(307,219),(173,149)):
            with self.subTest(reasoning=reasoning,completion=completion):
                usage={'prompt_tokens':8000,'completion_tokens':completion,'total_tokens':8000+completion,
                       'completion_tokens_details':{'reasoning_tokens':reasoning},'cost':.00001}
                normalized=normalize_usage(usage)
                self.assertTrue(normalized['complete'])
                self.assertEqual(normalized['cost_usd'],.00001)
                self.assertEqual(normalized['tokens']['total_tokens'],8000+completion)
                self.assertEqual(normalized['tokens']['output_tokens'],completion)
                self.assertEqual(normalized['tokens']['reasoning_tokens'],reasoning)
                self.assertIn('reasoning_count_exceeds_completion_provider_convention',normalized['warnings'])
                client,_=self.client(Response(data=self.response(usage=usage)))
                answer=client.complete(self.cfg,MESSAGES)
                self.assertEqual(answer['receipt']['status'],'complete')
                self.assertEqual(answer['receipt']['raw_usage'],usage)
                self.assertEqual(answer['text'],'Actual speech')

    def test_stream_drains_usage_after_finish_and_hides_reasoning(self):
        archive = []
        response = Response(events=self.stream_events())
        client, http = self.client(response, sink=archive.append)
        result = ''.join(client.stream(self.cfg, MESSAGES, speech_probe=lambda text: 'hello' in text))
        self.assertEqual(result, 'reply: hello')
        self.assertEqual(len(http.posts), 1)
        self.assertEqual(len(self.receipts), 1)
        receipt = self.receipts[0][1]
        self.assertEqual(receipt['status'], 'complete')
        self.assertEqual(receipt['raw_usage'], self.usage)
        self.assertEqual(receipt['request_id'], 'gen-test')
        self.assertIsNotNone(receipt['timing']['first_speech_s'])
        self.assertGreaterEqual(receipt['timing']['first_content_s'], receipt['timing']['first_event_s'])
        self.assertIn('Never speak this', json.dumps(archive))
        self.assertNotIn('Never speak this', receipt['content'])
        self.assertTrue(response.closed)

    def test_before_hook_budget_failure_prevents_network(self):
        def denied(request):
            raise ValueError('budget denied')
        client, http = self.client(Response(data=self.response()), before=denied)
        with self.assertRaises(ValueError):
            client.complete(self.cfg, MESSAGES)
        self.assertEqual(http.posts, [])
        self.assertEqual(self.receipts, [])

    def test_generator_close_marks_cancelled_and_holds_unknown_cost(self):
        client, http = self.client(Response(events=self.stream_events()))
        stream = client.stream(self.cfg, MESSAGES)
        self.assertEqual(next(stream), 'reply: hello')
        stream.close()
        self.assertEqual(self.receipts[0][1]['status'], 'cancelled')
        self.assertIsNone(self.receipts[0][1]['cost_usd'])
        self.assertEqual(len(http.posts), 1)

    def test_stream_error_no_retry_and_records_partial(self):
        events = self.stream_events()[:2]+[TimeoutError('timed out')]
        client, http = self.client(Response(events=events))
        with self.assertRaises(TimeoutError):
            list(client.stream(self.cfg, MESSAGES))
        self.assertEqual(len(http.posts), 1)
        self.assertEqual(self.receipts[0][1]['status'], 'unknown')
        self.assertEqual(self.receipts[0][1]['content'], 'reply: hello')

    def test_missing_tail_does_not_claim_complete(self):
        client, _ = self.client(Response(events=self.stream_events()[:-2]+['[DONE]']))
        with self.assertRaises(OpenRouterError):
            list(client.stream(self.cfg, MESSAGES))
        self.assertEqual(self.receipts[0][1]['status'], 'unknown')

    def test_provider_mismatch_accounts_charge_but_fails_closed(self):
        client, _ = self.client(Response(data=self.response(provider='Unapproved Provider')))
        with self.assertRaises(OpenRouterError):
            client.complete(self.cfg, MESSAGES)
        self.assertEqual(self.receipts[0][1]['cost_usd'], .00001)
        self.assertIn('provider_mismatch', self.receipts[0][1]['issues'])

    def test_close_failure_cannot_erase_accounting(self):
        client, _ = self.client(Response(data=self.response(), close_fail=True))
        result = client.complete(self.cfg, MESSAGES)
        self.assertEqual(result['text'], 'Actual speech')
        self.assertEqual(len(self.receipts), 1)

    def test_token_truncation_is_billable_and_flagged(self):
        data = self.response()
        data['choices'][0]['finish_reason'] = 'length'
        client, _ = self.client(Response(data=data))
        result = client.complete(self.cfg, MESSAGES)
        self.assertEqual(result['receipt']['status'], 'complete')
        self.assertTrue(result['receipt']['truncated'])

    def test_embedding_reorders_and_normalizes_exact_dimensions(self):
        config = embedding_config({'model_id':'google/gemini-embedding-2', 'provider_tag':'google-ai-studio',
                                  'provider_name':'Google AI Studio', 'usd_per_million_tokens':{'prompt':.2,'completion':0}})
        data = {'model': config['model_id'], 'data': [{'index':1,'embedding':[0.,2.]+[0.]*766},
                                                     {'index':0,'embedding':[3.]+[0.]*767}],
                'usage':{'prompt_tokens':12,'total_tokens':12,'cost':.0000024}}
        client, http = self.client(Response(data=data))
        result = client.embed(config, ['task: query | hello','title: historical | past'])
        self.assertEqual(result['vectors'][0][0], 1)
        self.assertEqual(result['vectors'][1][1], 1)
        self.assertTrue(http.posts[0][0].endswith('/embeddings'))
        self.assertEqual(result['receipt']['usage']['tokens']['output_tokens'], 0)

    def test_embedding_missing_cost_is_unknown_not_zero(self):
        config = embedding_config({'model_id':'google/gemini-embedding-2', 'provider_tag':'google',
                                  'usd_per_million_tokens':{'prompt':.2}})
        client, _ = self.client(Response(data={'model': config['model_id'],
            'data':[{'index':0,'embedding':[1.]+[0.]*767}], 'usage':{'prompt_tokens':5,'total_tokens':5}}))
        with self.assertRaises(OpenRouterError):
            client.embed(config, ['hello'])
        self.assertIsNone(self.receipts[0][1]['cost_usd'])

    def test_embedding_context_is_per_item_not_whole_batch(self):
        config = embedding_config({'model_id':'google/gemini-embedding-2', 'provider_tag':'google-vertex/us',
            'provider_name':'Google','quantization':'unknown','endpoint_context_tokens':8192,
            'usd_per_million_tokens':{'prompt':.2,'completion':0}})
        data={'model':config['model_id'], 'data':[{'index':i,'embedding':[1.]+[0.]*767} for i in range(8)],
              'usage':{'prompt_tokens':30000,'cost':.006}}
        client,http=self.client(Response(data=data))
        result=client.embed(config,['x'*6000]*8)
        self.assertEqual(len(result['vectors']),8)
        route=http.posts[0][1]['json']['provider']
        self.assertNotIn('require_parameters',route)
        self.assertNotIn('quantizations',route)
        self.assertEqual(route['only'],['google-vertex/us'])
        self.assertFalse(route['allow_fallbacks'])

    def test_embedding_body_limit_refused_before_reservation(self):
        config=embedding_config({'model_id':'google/gemini-embedding-2','provider_tag':'google',
            'endpoint_context_tokens':8192,'usd_per_million_tokens':{'prompt':.2}})
        client,http=self.client(Response())
        with self.assertRaises(ValueError):
            client.embed(config,['x'*120001])
        self.assertEqual(self.requests,[])
        self.assertEqual(http.posts,[])

    def test_utf8_budget_upper_bound_does_not_claim_actual_context_tokens(self):
        config=role_config(CATALOG,'minimax/minimax-m2-her','surface')
        config['endpoint_context_tokens']=32768
        messages=[{'role':'user','content':'你'*15000}]
        body=make_chat_body(config,messages)
        self.assertGreater(len(json.dumps(body,ensure_ascii=False).encode('utf-8')),32768)
        self.assertGreater(estimate_reservation(config,body),0)
        config['endpoint_context_tokens']=config['max_tokens']
        with self.assertRaises(ValueError):
            make_chat_body(config,messages)


if __name__ == '__main__':
    unittest.main()
