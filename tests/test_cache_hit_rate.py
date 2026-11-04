"""Prompt cache hit rate: what the ledger records, what the Use page's totals and the per-round timeline show.

The share counts only calls whose provider reported a cache figure. OpenRouter (and the OpenAI-style providers)
report prompt_tokens_details.cached_tokens, Anthropic cache_read_input_tokens, Gemini cachedContentTokenCount
(left out when nothing was cached). Ollama and the local embedder report none: those calls are "n/a", never a
0 % miss that drags the number down.
"""
import ast
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "brain"))
from f_usage import UsageMeter, normalize_usage  # noqa: E402
import f_bridge  # noqa: E402

PRICING = {"metadata": {"currency": "USD", "unit": "per_million_tokens"},
           "models": {"openrouter:deepseek/deepseek-v4.1-flash": {"input": .14, "output": .42, "cache_read": .0042},
                      "openrouter:z-ai/glm-5": {"input": .6, "output": 1.92, "cache_read": .12},
                      "ollama:qwen3:14b": {"input": 0.0, "output": 0.0, "cache_read": 0.0},
                      "local:bge-m3-d768": {"input": 0.0, "output": 0.0}}}


def openrouter_usage(prompt, cached, completion=20, cost=.0001):
    return {"prompt_tokens": prompt, "completion_tokens": completion, "cost": cost,
            "prompt_tokens_details": {"cached_tokens": cached, "cache_write_tokens": 0}}


class NormalizeCacheReportedTests(unittest.TestCase):
    def test_openrouter_reports_the_cache_even_when_nothing_was_cached(self):
        hit = normalize_usage("openrouter", openrouter_usage(4766, 4608), "deepseek/deepseek-v4.1-flash")
        self.assertTrue(hit["cache_reported"])
        self.assertEqual(hit["tokens"]["cache_read_tokens"], 4608)
        self.assertEqual(hit["tokens"]["total_input_tokens"], 4766)
        miss = normalize_usage("openrouter", openrouter_usage(4725, 0), "z-ai/glm-5")
        self.assertTrue(miss["cache_reported"])
        self.assertEqual(miss["tokens"]["cache_read_tokens"], 0)

    def test_usage_without_a_cache_figure_is_not_reported(self):
        result = normalize_usage("openrouter", {"prompt_tokens": 100, "completion_tokens": 5, "cost": 0}, "vendor/model")
        self.assertFalse(result["cache_reported"])
        self.assertTrue(result["complete"])
        explicit_null = normalize_usage("openrouter", {"prompt_tokens": 100, "completion_tokens": 5,
                                                       "prompt_tokens_details": {"cached_tokens": None}}, "vendor/model")
        self.assertFalse(explicit_null["cache_reported"])

    def test_deepseek_native_cache_hit_field(self):
        result = normalize_usage("deepseek", {"prompt_tokens": 1000, "completion_tokens": 10,
                                              "prompt_cache_hit_tokens": 768, "prompt_cache_miss_tokens": 232})
        self.assertTrue(result["cache_reported"])
        self.assertEqual(result["tokens"]["cache_read_tokens"], 768)
        self.assertEqual(result["tokens"]["input_uncached_tokens"], 232)

    def test_ollama_counts_are_recorded_and_its_cache_is_unknown(self):
        # f_ollama maps prompt_eval_count / eval_count onto prompt_tokens / completion_tokens
        result = normalize_usage("ollama", {"prompt_tokens": 812, "completion_tokens": 9, "cost": 0.0}, "qwen3:14b")
        self.assertTrue(result["has_usage"])
        self.assertTrue(result["complete"])
        self.assertFalse(result["cache_reported"])
        self.assertEqual(result["tokens"]["total_input_tokens"], 812)
        self.assertEqual(result["tokens"]["output_tokens"], 9)
        native = normalize_usage("ollama", {"prompt_eval_count": 40, "eval_count": 3}, "qwen3:14b")
        self.assertEqual((native["tokens"]["total_input_tokens"], native["tokens"]["output_tokens"]), (40, 3))
        self.assertFalse(native["cache_reported"])

    def test_embeddings_never_report_a_prompt_cache(self):
        self.assertFalse(normalize_usage("local", {"prompt_tokens": 17}, "bge-m3-d768")["cache_reported"])
        self.assertFalse(normalize_usage("openrouter", {"prompt_tokens": 17, "prompt_tokens_details": {"cached_tokens": 0}},
                                         "google/gemini-embedding-001")["cache_reported"])
        self.assertFalse(normalize_usage("gemini", {"promptTokenCount": 17}, "gemini-embedding-2")["cache_reported"])

    def test_anthropic_and_gemini_chat_calls_report_it(self):
        self.assertTrue(normalize_usage("anthropic", {"input_tokens": 10, "output_tokens": 2,
                                                      "cache_read_input_tokens": 0})["cache_reported"])
        gemini = normalize_usage("gemini", {"promptTokenCount": 100, "candidatesTokenCount": 5}, "gemini-3-flash")
        self.assertTrue(gemini["cache_reported"])         # no cachedContentTokenCount = nothing was cached
        self.assertEqual(gemini["tokens"]["cache_read_tokens"], 0)

    def test_no_usage_is_never_reported(self):
        self.assertFalse(normalize_usage("openrouter", None)["cache_reported"])


class LedgerCacheHitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.meter = UsageMeter(Path(self.tmp.name) / "usage.sqlite", PRICING)

    def tearDown(self):
        self.tmp.cleanup()

    def book(self, provider, model, phase, usage, turn="t1", **kwargs):
        rid = self.meter.begin(provider, model, phase, turn)
        return self.meter.finish(rid, usage, **kwargs)

    def test_hit_rate_counts_only_calls_that_report_a_cache_figure(self):
        self.book("openrouter", "deepseek/deepseek-v4.1-flash", "surface", openrouter_usage(1000, 900))
        self.book("openrouter", "z-ai/glm-5", "deep", openrouter_usage(2000, 1000))
        self.book("local", "bge-m3-d768", "query_embedding", {"prompt_tokens": 50})
        ollama = self.book("ollama", "qwen3:14b", "reflection", {"prompt_tokens": 800, "completion_tokens": 30, "cost": 0.0},
                           reported_cost_usd=0.0)
        self.book("openrouter", "deepseek/deepseek-v4.1-flash", "agent:hermes", {"prompt_tokens": 5000, "completion_tokens": 9})
        amika = self.meter.summary()["display"]["amika"]
        self.assertAlmostEqual(amika["cache_hit_pct"], 100 * 1900 / 3000)
        self.assertEqual(amika["cache_read_tokens"], 1900)
        self.assertEqual(amika["cache_input_tokens"], 3000)
        self.assertEqual(amika["cache_unreported_requests"], 1)       # the Ollama call; not the embedding, not the agent
        self.assertEqual(amika["input_tokens"], 1000 + 2000 + 50 + 800)
        # an Ollama call is now booked with its tokens at the local price of zero, not as missing usage
        self.assertEqual(ollama["pricing_status"], "priced")
        self.assertEqual(ollama["cost_usd"], 0.0)
        self.assertEqual(ollama["tokens"]["total_input_tokens"], 800)
        self.assertFalse(ollama["cache_reported"])
        by_phase = {p["phase"]: p for p in self.meter.summary()["by_phase"]}
        self.assertAlmostEqual(by_phase["surface"]["cache_hit_pct"], 90.0)
        self.assertAlmostEqual(by_phase["deep"]["cache_hit_pct"], 50.0)
        self.assertIsNone(by_phase["reflection"]["cache_hit_pct"])
        self.assertIsNone(by_phase["query_embedding"]["cache_hit_pct"])
        self.assertIsNone(by_phase["agent:hermes"]["cache_hit_pct"])

    def test_ollama_only_is_not_applicable_rather_than_zero(self):
        for turn in ("t1", "t2"):
            self.book("ollama", "qwen3:14b", "surface", {"prompt_tokens": 500, "completion_tokens": 20, "cost": 0.0},
                      turn=turn, reported_cost_usd=0.0)
        amika = self.meter.summary()["amika"]
        self.assertIsNone(amika["cache_hit_pct"])
        self.assertEqual(amika["cache_unreported_requests"], 2)
        self.assertEqual(amika["cache_input_tokens"], 0)

    def test_rows_written_before_the_flag_are_judged_by_provider(self):
        self.book("openrouter", "deepseek/deepseek-v4.1-flash", "surface", openrouter_usage(1000, 750))
        self.book("local", "bge-m3-d768", "memory_embedding", {"prompt_tokens": 300})
        with closing(sqlite3.connect(self.meter.path)) as conn, conn:
            conn.execute("UPDATE requests SET cache_reported=NULL")
        amika = self.meter.summary()["amika"]
        self.assertAlmostEqual(amika["cache_hit_pct"], 75.0)
        self.assertEqual(amika["cache_input_tokens"], 1000)

    def test_an_older_ledger_gains_the_column(self):
        path = Path(self.tmp.name) / "old.sqlite"
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("""CREATE TABLE requests (id TEXT PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL,
                phase TEXT NOT NULL, turn_id TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
                tokens_json TEXT, usage_complete INTEGER NOT NULL DEFAULT 0, pricing_status TEXT NOT NULL, cost_usd REAL,
                pricing_json TEXT NOT NULL, issues_json TEXT NOT NULL, error_code TEXT, budget_scope TEXT,
                reservation_usd REAL NOT NULL DEFAULT 0)""")
            tokens = {"input_uncached_tokens": 100, "output_tokens": 10, "cache_read_tokens": 900, "total_input_tokens": 1000}
            conn.execute("INSERT INTO requests VALUES ('a','openrouter','vendor/model','deep','t','2026-11-04T13:00:00+00:00',"
                         "'2026-11-04T13:00:01+00:00','complete',?,1,'priced',0.001,?,'[]',NULL,NULL,0)",
                         (json.dumps(tokens), json.dumps({"rates": {}, "metadata": {}})))
        meter = UsageMeter(path, PRICING)
        with closing(sqlite3.connect(path)) as conn:
            self.assertIn("cache_reported", {row[1] for row in conn.execute("PRAGMA table_info(requests)")})
        self.assertAlmostEqual(meter.summary()["amika"]["cache_hit_pct"], 90.0)

    def test_receipt_carries_the_flag_and_the_call_receipt_shows_na(self):
        embed = self.book("local", "bge-m3-d768", "query_embedding", {"prompt_tokens": 17})
        self.assertFalse(embed["cache_reported"])
        call = f_bridge.ledger_call_receipt(embed)
        self.assertEqual(call["input_tokens"], 17)
        self.assertIsNone(call["cached_tokens"])
        surface = self.book("openrouter", "deepseek/deepseek-v4.1-flash", "surface", openrouter_usage(4766, 4608))
        self.assertTrue(surface["cache_reported"])
        self.assertEqual(f_bridge.ledger_call_receipt(surface)["cached_tokens"], 4608)


class CallReceiptTokenTests(unittest.TestCase):
    def test_ollama_receipt_has_no_cache_figure(self):
        fields = f_bridge.raw_token_fields("ollama", {"prompt_tokens": 812, "completion_tokens": 9, "cost": 0.0})
        self.assertEqual(fields, {"input_tokens": 812, "cached_tokens": None, "output_tokens": 9})

    def test_openrouter_receipt_keeps_a_reported_zero(self):
        fields = f_bridge.raw_token_fields("openrouter", openrouter_usage(4725, 0))
        self.assertEqual(fields["cached_tokens"], 0)

    def test_gemini_chat_without_cached_content_is_zero_and_embedding_is_null(self):
        chat = f_bridge.raw_token_fields("gemini", {"promptTokenCount": 100, "candidatesTokenCount": 5})
        self.assertEqual(chat["cached_tokens"], 0)
        embedding = f_bridge.raw_token_fields("gemini_embedding", {"promptTokenCount": 17}, embedding=True)
        self.assertIsNone(embedding["cached_tokens"])

    def test_ledger_fallback_tokens(self):
        tokens = {"total_input_tokens": 500, "cache_read_tokens": 0, "output_tokens": 9}
        self.assertEqual(f_bridge._token_fields(tokens, cache_reported=False)["cached_tokens"], None)
        self.assertEqual(f_bridge._token_fields(tokens, cache_reported=True)["cached_tokens"], 0)
        self.assertEqual(f_bridge._token_fields(tokens)["cached_tokens"], 0)      # unknown: as before


def _timeline_book():
    """TimelineBook from speech/api.py by AST (pure Python, no speech stack import)."""
    src = (ROOT / "speech" / "api.py").read_text(encoding="utf-8")
    node = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "TimelineBook")
    namespace = {}
    exec(compile(ast.get_source_segment(src, node), "api.py:TimelineBook", "exec"), namespace)  # noqa: S102
    return namespace["TimelineBook"]


class TimelineCacheTests(unittest.TestCase):
    def setUp(self):
        self.book = _timeline_book()()
        self.book.open_round("t1", marks={"speech_start_ms": 1000, "speech_end_ms": 2000, "commit_ms": 2100})

    def receipt(self, phase, inp, cached, start=2200):
        self.book.add_receipt({"turn_id": "t1", "phase": phase, "status": "complete", "model": "m", "provider": "p",
                               "started_ms": start, "first_token_ms": start + 400, "last_token_ms": start + 900,
                               "input_tokens": inp, "cached_tokens": cached, "output_tokens": 20, "cost_usd": .0001,
                               "request_id": phase + str(start)})

    def snapshot(self):
        return self.book.snapshot("t1")

    def test_round_cache_share_ignores_calls_without_a_cache_figure(self):
        self.receipt("surface", 1000, 900)
        self.receipt("deep", 500, None, start=2600)        # e.g. a deep layer on Ollama
        msg = self.snapshot()
        summary = msg["round_summary"]
        self.assertEqual(summary["total_input_tokens"], 1500)
        self.assertEqual(summary["cached_pct"], 90.0)       # not 900 / 1500 = 60 %
        self.assertEqual((summary["cached_tokens"], summary["cache_input_tokens"]), (900, 1000))
        segments = {s["key"]: s for s in msg["segments"]}
        self.assertEqual(segments["surface_llm"]["tokens"], {"input": 1000, "cached": 900, "output": 20, "cache_input": 1000})
        self.assertEqual(segments["deep_llm"]["tokens"]["cache_input"], None)
        self.assertIsNone(segments["deep_llm"]["tokens"]["cached"])

    def test_round_with_no_cache_figures_is_not_applicable(self):
        self.receipt("surface", 1000, None)
        summary = self.snapshot()["round_summary"]
        self.assertEqual(summary["total_input_tokens"], 1000)
        self.assertIsNone(summary["cached_pct"])
        self.assertIsNone(summary["cached_tokens"])
        self.assertIsNone(summary["cache_input_tokens"])

    def test_memory_writer_counts_toward_the_round(self):
        self.receipt("surface", 4766, 4608)
        self.receipt("deep", 4427, 3840, start=2600)
        self.receipt("memory_write", 636, 512, start=5000)
        summary = self.snapshot()["round_summary"]
        self.assertEqual(summary["cache_input_tokens"], 4766 + 4427 + 636)
        self.assertEqual(summary["cached_pct"], round(100 * (4608 + 3840 + 512) / (4766 + 4427 + 636), 1))


if __name__ == "__main__":
    unittest.main()
