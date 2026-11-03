"""Offline accounting tests: price snapshots, provider semantics, privacy and caps."""
import concurrent.futures
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
from f_usage import BudgetExceeded, UsageMeter, merge_usage, normalize_usage


PRICING = {
    "metadata": {"currency": "USD", "unit": "per_million_tokens", "checked_at": "2026-09-26",
                 "source": "https://platform.claude.com/docs/en/about-claude/pricing"},
    "models": {
        "anthropic:claude-sonnet-5": {"input": 2, "output": 10, "cache_read": .2, "cache_write_5m": 2.5, "cache_write_1h": 4},
        "xai:grok-example": {"input": 1, "output": 2, "cache_read": .1},
        "gemini:gemini-embedding-example": {"input": .15, "output": 0},
    },
}


class ProviderUsageTests(unittest.TestCase):
    def test_anthropic_cache_buckets_are_separate_from_input(self):
        result = normalize_usage("anthropic", {"input_tokens": 100, "output_tokens": 50,
            "cache_read_input_tokens": 500, "cache_creation_input_tokens": 300,
            "cache_creation": {"ephemeral_5m_input_tokens": 200, "ephemeral_1h_input_tokens": 100}})
        self.assertTrue(result["complete"])
        self.assertEqual(result["tokens"]["total_input_tokens"], 900)
        self.assertEqual(result["tokens"]["total_tokens"], 950)
        self.assertEqual(result["tokens"]["cache_write_1h_tokens"], 100)

    def test_default_anthropic_cache_ttl(self):
        result = normalize_usage("anthropic", {"input_tokens": 1, "output_tokens": 2, "cache_creation_input_tokens": 3})
        self.assertEqual(result["tokens"]["cache_write_5m_tokens"], 3)
        self.assertTrue(result["complete"])

    def test_cache_mismatch_is_not_silently_fully_priced(self):
        result = normalize_usage("anthropic", {"input_tokens": 1, "output_tokens": 2,
            "cache_creation_input_tokens": 10, "cache_creation": {"ephemeral_5m_input_tokens": 5}})
        self.assertFalse(result["complete"])
        self.assertEqual(result["tokens"]["cache_write_unknown_tokens"], 5)

    def test_openai_cached_prompt_and_reasoning_not_counted_twice(self):
        result = normalize_usage("xai", {"prompt_tokens": 1000, "completion_tokens": 200,
            "prompt_tokens_details": {"cached_tokens": 800}, "completion_tokens_details": {"reasoning_tokens": 150}})
        self.assertEqual(result["tokens"]["input_uncached_tokens"], 200)
        self.assertEqual(result["tokens"]["total_tokens"], 1200)
        self.assertEqual(result["tokens"]["reasoning_tokens"], 150)

    def test_gemini_embeddings_and_generation(self):
        result = normalize_usage("google", {"usageMetadata": {"promptTokenCount": 17}}, "gemini-embedding-example")
        self.assertTrue(result["complete"])
        self.assertEqual(result["tokens"]["total_tokens"], 17)
        result = normalize_usage("gemini", {"promptTokenCount": 100, "candidatesTokenCount": 20, "thoughtsTokenCount": 10})
        self.assertEqual(result["tokens"]["output_tokens"], 30)
        self.assertEqual(result["tokens"]["total_tokens"], 130)

    def test_cumulative_stream_snapshots_replace_counts(self):
        usage = merge_usage({}, {"input_tokens": 100, "output_tokens": 0})
        usage = merge_usage(usage, {"output_tokens": 12})
        usage = merge_usage(usage, {"output_tokens": 25})
        usage = merge_usage(usage, {"input_tokens": 0, "output_tokens": 25})
        self.assertEqual(usage, {"input_tokens": 100, "output_tokens": 25})
        self.assertEqual(merge_usage({"prompt_tokens_details": {"cached_tokens": 15}},
                                     {"prompt_tokens_details": {"cached_tokens": 20}}),
                         {"prompt_tokens_details": {"cached_tokens": 20}})

    def test_missing_negative_and_invalid_counts_are_incomplete(self):
        for raw in (None, {}, {"output_tokens": 5}, {"input_tokens": -1, "output_tokens": 0},
                    {"input_tokens": True, "output_tokens": 0}, {"input_tokens": float("nan"), "output_tokens": 0}):
            with self.subTest(raw=raw):
                self.assertFalse(normalize_usage("anthropic", raw)["complete"])


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "usage.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def meter(self, **kwargs):
        return UsageMeter(self.path, PRICING, **kwargs)

    def test_exact_cost_and_idempotent_finish(self):
        meter = self.meter()
        rid = meter.begin("anthropic", "claude-sonnet-5", "deep", "turn1")
        receipt = meter.finish(rid, {"input_tokens": 100, "output_tokens": 50,
            "cache_read_input_tokens": 500, "cache_creation_input_tokens": 300,
            "cache_creation": {"ephemeral_5m_input_tokens": 200, "ephemeral_1h_input_tokens": 100}})
        self.assertAlmostEqual(receipt["cost_usd"], .0017)
        meter.finish(rid, {"input_tokens": 100, "output_tokens": 50})
        meter.finish(rid, status="failed", error="cleanup")
        summary = meter.summary()
        self.assertEqual(summary["total_requests"], 1)
        self.assertEqual(summary["tokens"]["total_tokens"], 950)
        self.assertAlmostEqual(summary["priced_known_usd"], .0017)
        self.assertEqual(summary["recent_requests"][0]["status"], "complete")

    def test_snapshot_prices_survive_reload_and_rate_change(self):
        original = self.meter()
        rid = original.begin("anthropic", "claude-sonnet-5", "deep")
        changed = json.loads(json.dumps(PRICING))
        changed["models"]["anthropic:claude-sonnet-5"]["input"] = 200
        changed_meter = UsageMeter(self.path, changed)
        receipt = changed_meter.finish(rid, {"input_tokens": 1000000, "output_tokens": 0})
        self.assertEqual(receipt["cost_usd"], 2)
        new = changed_meter.begin("anthropic", "claude-sonnet-5", "deep")
        changed_meter.finish(new, {"input_tokens": 1000000, "output_tokens": 0})
        self.assertEqual(self.meter().summary()["priced_known_usd"], 202)

    def test_missing_and_unpriced_usage_explicit(self):
        meter = self.meter()
        rid = meter.begin("anthropic", "unknown-model", "deep")
        unknown = meter.finish(rid, {"input_tokens": 100, "output_tokens": 10})
        self.assertIsNone(unknown["cost_usd"])
        self.assertEqual(unknown["pricing_status"], "unpriced")
        rid = meter.begin("anthropic", "claude-sonnet-5", "deep")
        missing = meter.finish(rid)
        self.assertIsNone(missing["cost_usd"])
        self.assertFalse(missing["usage_complete"])
        summary = meter.summary()
        self.assertEqual(summary["unpriced_requests"], 2)
        self.assertEqual(summary["incomplete_requests"], 1)

    def test_cancellation_keeps_usage_without_counting_it_as_final(self):
        meter = self.meter(budget_usd=2)
        rid = meter.begin("anthropic", "claude-sonnet-5", "deep", estimated_max_usd=.5)
        receipt = meter.finish(rid, {"input_tokens": 100000, "output_tokens": 1000}, status="cancelled")
        self.assertEqual(receipt["pricing_status"], "partial")
        self.assertAlmostEqual(receipt["cost_usd"], .21)
        summary = meter.summary()
        self.assertAlmostEqual(summary["budget"]["held_usd"], .29)
        self.assertAlmostEqual(summary["budget"]["remaining_usd"], 1.5)
        # A later final provider receipt refines the same record rather than adding it.
        meter.finish(rid, {"input_tokens": 100000, "output_tokens": 2000})
        self.assertAlmostEqual(meter.summary()["priced_known_usd"], .22)
        self.assertEqual(meter.summary()["total_requests"], 1)
        self.assertEqual(meter.summary()["budget"]["held_usd"], 0)

    def test_unknown_error_releases_hold_and_never_stores_secrets(self):
        meter = self.meter(budget_usd=2)
        rid = meter.begin("anthropic", "claude-sonnet-5", "deep", estimated_max_usd=.4)
        meter.finish(rid, {"secret": "THIS-MUST-NOT-PERSIST"}, status="failed",
                     error=RuntimeError("API key sk-super-secret; response private speech"))
        summary = meter.summary()
        # settle/drain update: a terminal failure releases its reservation immediately
        # (there is nothing left to settle); the record itself stays unpriced.
        self.assertAlmostEqual(summary["budget"]["held_usd"], 0)
        self.assertEqual(summary["recent_requests"][0]["error_code"], "RuntimeError")
        self.assertEqual(summary["recent_requests"][0]["pricing_status"], "missing_usage")
        self.assertIsNone(summary["recent_requests"][0]["cost_usd"])
        with closing(sqlite3.connect(self.path)) as conn:
            dump = "\n".join(conn.iterdump())
        for secret in ("THIS-MUST-NOT-PERSIST", "sk-super-secret", "private speech"):
            self.assertNotIn(secret, dump)

    def test_budget_requires_reservations_and_survives_restart(self):
        meter = self.meter(budget_usd=2)
        for kwargs in ({}, {"estimated_max_usd": 0}, {"estimated_max_usd": 3}):
            with self.assertRaises(BudgetExceeded):
                meter.begin("anthropic", "claude-sonnet-5", "deep", **kwargs)
        with self.assertRaises(BudgetExceeded):
            meter.begin("anthropic", "unknown", "deep", estimated_max_usd=.1)
        meter.begin("anthropic", "claude-sonnet-5", "deep", estimated_max_usd=1.5)
        resumed = self.meter(budget_usd=2)
        with self.assertRaises(BudgetExceeded):
            resumed.begin("anthropic", "claude-sonnet-5", "deep", estimated_max_usd=.6)
        self.assertEqual(resumed.summary()["budget"]["remaining_usd"], .5)
        with self.assertRaises(ValueError):
            self.meter(budget_usd=4)

    def test_concurrent_reservations_cannot_overbook_budget(self):
        meter = self.meter(budget_usd=1)
        other = self.meter(budget_usd=1)
        def reserve(i):
            try:
                return (meter if i % 2 else other).begin("anthropic", "claude-sonnet-5", "deep", estimated_max_usd=.2)
            except BudgetExceeded:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(reserve, range(20)))
        self.assertEqual(sum(r is not None for r in results), 5)
        self.assertAlmostEqual(meter.summary()["budget"]["held_usd"], 1)

    def test_output_bound_underestimate_reports_overrun(self):
        meter = self.meter(budget_usd=1)
        rid = meter.begin("anthropic", "claude-sonnet-5", "deep", estimated_max_usd=.1)
        meter.finish(rid, {"input_tokens": 1000000, "output_tokens": 0})
        self.assertEqual(meter.summary()["budget"]["overrun_usd"], 1)
        with self.assertRaises(BudgetExceeded):
            meter.begin("anthropic", "claude-sonnet-5", "deep", estimated_max_usd=.1)

    def test_phase_and_model_summaries_and_embedding_cost(self):
        meter = self.meter()
        rid = meter.begin("xai", "grok-example", "surface")
        meter.finish(rid, {"prompt_tokens": 1000, "completion_tokens": 100, "prompt_tokens_details": {"cached_tokens": 900}})
        rid = meter.begin("google", "gemini-embedding-example", "memory_embed")
        meter.finish(rid, {"usageMetadata": {"promptTokenCount": 1000}})
        summary = meter.summary()
        self.assertEqual(summary["total_requests"], 2)
        self.assertEqual(summary["tokens"]["total_tokens"], 2100)
        self.assertAlmostEqual(summary["priced_known_usd"], .00054)
        self.assertEqual({p["phase"] for p in summary["by_phase"]}, {"surface", "memory_embed"})
        self.assertEqual({m["provider"] for m in summary["by_model"]}, {"xai", "gemini"})

    def test_worst_case_estimate_accounts_for_cache_writes(self):
        meter = self.meter()
        self.assertAlmostEqual(meter.estimate_reservation("anthropic", "claude-sonnet-5", 100000, 1000), .41)


if __name__ == "__main__":
    unittest.main()
