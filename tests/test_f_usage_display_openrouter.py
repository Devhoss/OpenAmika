"""Offline regression tests for persistent display windows and router billing."""
import concurrent.futures
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
from f_usage import BudgetExceeded, DisplayResetBusy, UsageMeter


PRICING = {"models": {
    "openrouter:vendor/model": {"input": 1, "output": 2, "cache_read": .1},
    "anthropic:direct": {"input": 2, "output": 10},
}}
USAGE = {"prompt_tokens": 1000, "completion_tokens": 200,
         "prompt_tokens_details": {"cached_tokens": 800},
         "completion_tokens_details": {"reasoning_tokens": 150}}


class MeterTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "usage.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def meter(self, **kwargs):
        return UsageMeter(self.path, PRICING, **kwargs)

    def router_request(self, meter, **kwargs):
        return meter.begin("openrouter", "vendor/model", "surface", **kwargs)


class DisplayWindowTests(MeterTestCase):
    def test_reset_retains_lifetime_receipts_and_budget_and_survives_restart(self):
        meter = self.meter(budget_usd=1)
        rid = self.router_request(meter, estimated_max_usd=.5)
        meter.finish(rid, {**USAGE, "cost": .3})
        before = meter.summary()
        window = meter.reset_display()
        resumed = self.meter(budget_usd=1)
        after = resumed.summary()
        self.assertEqual(after["display"]["total_requests"], 0)
        self.assertEqual(after["display"]["tokens"]["total_tokens"], 0)
        self.assertEqual(after["display"]["priced_known_usd"], 0)
        self.assertEqual(after["total_requests"], before["total_requests"])
        self.assertEqual(after["priced_known_usd"], before["priced_known_usd"])
        self.assertEqual(after["recent_requests"], before["recent_requests"])
        self.assertEqual(after["budget"], before["budget"])
        self.assertEqual(after["display_window"], window)
        self.assertEqual(window["reset_count"], 1)
        self.assertTrue(window["reset_id"])
        self.assertTrue(after["can_reset_display"])
        rid2 = self.router_request(resumed, estimated_max_usd=.5)
        resumed.finish(rid2, {**USAGE, "cost": .2})
        after = resumed.summary()
        self.assertEqual(after["display"]["total_requests"], 1)
        self.assertAlmostEqual(after["display"]["priced_known_usd"], .2)
        self.assertAlmostEqual(after["priced_known_usd"], .5)
        with self.assertRaises(BudgetExceeded):
            self.router_request(resumed, estimated_max_usd=.6)

    def test_reset_rejects_inflight_from_another_instance_without_moving_window(self):
        first, second = self.meter(), self.meter()
        before = first.summary()["display_window"]
        rid = self.router_request(second)
        self.assertFalse(first.summary()["can_reset_display"])
        with self.assertRaises(DisplayResetBusy):
            first.reset_display()
        self.assertEqual(first.summary()["display_window"], before)
        second.finish(rid, status="unknown")
        self.assertEqual(first.reset_display()["reset_count"], 1)

    def test_rowid_boundary_ignores_backdated_timestamps_and_late_billing(self):
        meter = self.meter()
        old = self.router_request(meter)
        meter.finish(old, USAGE)
        meter.reset_display()
        meter.finish(old, reported_cost_usd=.1)
        new = self.router_request(meter)
        meter.finish(new, {**USAGE, "cost": .2})
        with closing(sqlite3.connect(self.path)) as conn:
            with conn:
                conn.execute("UPDATE requests SET started_at='1900-01-01' WHERE id=?", (new,))
        after = meter.summary()
        self.assertEqual(after["display"]["total_requests"], 1)
        self.assertEqual(after["display"]["recent_requests"][0]["id"], new)
        self.assertAlmostEqual(after["priced_known_usd"], .3)

    def test_begin_and_reset_are_linearizable_across_connections(self):
        first, second = self.meter(), self.meter()
        barrier = threading.Barrier(2)

        def begin():
            barrier.wait()
            return self.router_request(first)

        def reset():
            barrier.wait()
            try:
                return second.reset_display()
            except DisplayResetBusy:
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            request_future = pool.submit(begin)
            reset_future = pool.submit(reset)
            rid, window = request_future.result(), reset_future.result()
        summary = first.summary()
        # Either reset won before begin, or reset rejected the active request.
        # It must never hide a request that began before its active check.
        self.assertEqual(summary["display"]["total_requests"], 1)
        self.assertEqual(summary["display"]["recent_requests"][0]["id"], rid)
        self.assertEqual(summary["display_window"]["reset_count"], int(window is not None))

    def test_legacy_database_migration_is_additive(self):
        with closing(sqlite3.connect(self.path)) as conn:
            with conn:
                conn.execute("""CREATE TABLE requests (
                    id TEXT PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL,
                    phase TEXT NOT NULL, turn_id TEXT NOT NULL, started_at TEXT NOT NULL,
                    finished_at TEXT, status TEXT NOT NULL, tokens_json TEXT,
                    usage_complete INTEGER NOT NULL DEFAULT 0, pricing_status TEXT NOT NULL,
                    cost_usd REAL, pricing_json TEXT NOT NULL, issues_json TEXT NOT NULL,
                    error_code TEXT, budget_scope TEXT, reservation_usd REAL NOT NULL DEFAULT 0)""")
                conn.execute("""INSERT INTO requests
                    (id,provider,model,phase,turn_id,started_at,finished_at,status,tokens_json,
                     usage_complete,pricing_status,cost_usd,pricing_json,issues_json)
                    VALUES ('old','anthropic','direct','deep','','2026-01-01','2026-01-01',
                            'complete','{}',1,'priced',0.25,'{"rates":{"input":2,"output":10}}','[]')""")
        meter = self.meter()
        self.assertEqual(meter.summary()["priced_known_usd"], .25)
        self.assertEqual(meter.summary()["display"]["total_requests"], 1)
        meter.reset_display()
        self.assertEqual(self.meter().summary()["total_requests"], 1)
        self.assertEqual(self.meter().summary()["display"]["total_requests"], 0)


class RouterChargeTests(MeterTestCase):
    def test_router_embedding_reports_no_completion_field(self):
        meter = self.meter()
        rid = meter.begin('openrouter', 'google/gemini-embedding-2', 'query_embedding')
        receipt = meter.finish(rid, {'prompt_tokens': 100, 'total_tokens': 100, 'cost': .00002})
        self.assertTrue(receipt['usage_complete'])
        self.assertEqual(receipt['tokens']['output_tokens'], 0)
        self.assertEqual(receipt['pricing_status'], 'priced')

    def test_reported_charge_overrides_estimate_and_reasoning_not_double_counted(self):
        meter = self.meter(budget_usd=1)
        rid = self.router_request(meter, estimated_max_usd=.5)
        receipt = meter.finish(rid, {"usage": {**USAGE, "cost": .04, "cost_details": {"upstream_inference_cost": .9}}},
                               actual_model="vendor/model-actual", actual_provider="Example Provider",
                               provider_request_id="gen-123")
        self.assertEqual(receipt["cost_usd"], .04)
        self.assertAlmostEqual(receipt["estimated_cost_usd"], .00068)
        self.assertEqual(receipt["cost_source"], "provider_reported")
        self.assertEqual(receipt["tokens"]["total_tokens"], 1200)
        self.assertEqual(receipt["tokens"]["output_tokens"], 200)
        self.assertEqual(receipt["tokens"]["reasoning_tokens"], 150)
        self.assertEqual(receipt["actual_model"], "vendor/model-actual")
        self.assertEqual(receipt["provider_request_id"], "gen-123")
        self.assertEqual(meter.summary()["budget"]["held_usd"], 0)
        self.assertEqual(meter.summary()["provider_reported_usd"], .04)

    def test_confirmed_zero_is_valid_but_missing_cost_is_never_confirmed_zero(self):
        meter = self.meter(budget_usd=1)
        missing = self.router_request(meter, estimated_max_usd=.4)
        receipt = meter.finish(missing, USAGE)
        self.assertTrue(receipt["usage_complete"])
        self.assertIsNone(receipt["cost_usd"])
        self.assertEqual(receipt["pricing_status"], "estimated")
        self.assertIn("missing_reported_cost", receipt["issues"])
        summary = meter.summary()
        self.assertFalse(summary["cost_complete"])
        self.assertEqual(summary["unconfirmed_cost_requests"], 1)
        self.assertEqual(summary["unpriced_requests"], 1)
        self.assertAlmostEqual(summary["unconfirmed_estimate_usd"], .00068)
        # settle/drain update: the booked estimate is deducted from the hold; the
        # remainder stays reserved until the cost is confirmed by settlement.
        self.assertAlmostEqual(summary["budget"]["held_usd"], .4 - summary["unconfirmed_estimate_usd"])
        held_before_reset = summary["budget"]["held_usd"]
        meter.reset_display()
        self.assertAlmostEqual(meter.summary()["budget"]["held_usd"], held_before_reset)
        with self.assertRaises(BudgetExceeded):
            self.router_request(meter, estimated_max_usd=.7)
        zero = self.router_request(meter, estimated_max_usd=.4)
        receipt = meter.finish(zero, {**USAGE, "cost": 0})
        self.assertEqual(receipt["cost_usd"], 0)
        self.assertEqual(receipt["cost_source"], "provider_reported")
        self.assertEqual(receipt["pricing_status"], "priced")

    def test_late_cost_only_receipt_refines_once_without_erasing_token_counts(self):
        meter = self.meter(budget_usd=1)
        rid = self.router_request(meter, estimated_max_usd=.4)
        first = meter.finish(rid, USAGE)
        receipt = meter.finish(rid, reported_cost_usd=.2, provider_request_id="gen-final")
        self.assertEqual(receipt["tokens"], first["tokens"])
        self.assertEqual(receipt["cost_usd"], .2)
        self.assertEqual(receipt["pricing_status"], "priced")
        self.assertNotIn("missing_reported_cost", receipt["issues"])
        self.assertEqual(meter.summary()["budget"]["held_usd"], 0)
        self.assertEqual(meter.finish(rid, reported_cost_usd=.8), receipt)
        self.assertEqual(meter.finish(rid, status="failed", error="cleanup"), receipt)
        self.assertEqual(meter.summary()["total_requests"], 1)

    def test_invalid_costs_fail_closed_and_do_not_persist_unsafe_metadata(self):
        meter = self.meter(budget_usd=2)
        for cost in (-.1, float("nan"), float("inf"), True, "sk-private-billing-text"):
            with self.subTest(cost=cost):
                rid = self.router_request(meter, estimated_max_usd=.2)
                receipt = meter.finish(rid, {**USAGE, "cost": cost}, actual_model="sk-secret-model",
                                       actual_provider="provider\nprivate content", provider_request_id="Bearer secret")
                self.assertIsNone(receipt["cost_usd"])
                self.assertIn("invalid_reported_cost", receipt["issues"])
                self.assertIsNone(receipt["actual_model"])
                self.assertIsNone(receipt["actual_provider"])
                self.assertIsNone(receipt["provider_request_id"])
        final = meter.summary()
        # 5 requests × .2 held, each minus its booked token estimate (settle/drain update)
        self.assertAlmostEqual(final["budget"]["held_usd"],
                               1 - final.get("unconfirmed_estimate_usd", 0), places=6)
        with closing(sqlite3.connect(self.path)) as conn:
            dump = "\n".join(conn.iterdump())
        for text in ("sk-private-billing-text", "sk-secret-model", "private content", "Bearer secret"):
            self.assertNotIn(text, dump)

    def test_actual_charge_works_without_catalog_rates_but_tokens_still_required(self):
        meter = self.meter()
        rid = meter.begin("openrouter", "unlisted/model", "surface")
        receipt = meter.finish(rid, {**USAGE, "cost": .015})
        self.assertEqual(receipt["cost_usd"], .015)
        self.assertEqual(receipt["pricing_status"], "priced")
        self.assertIsNone(receipt["estimated_cost_usd"])
        partial = self.router_request(meter)
        receipt = meter.finish(partial, {"cost": .02})
        self.assertEqual(receipt["cost_usd"], .02)
        self.assertFalse(receipt["usage_complete"])
        self.assertEqual(receipt["pricing_status"], "partial")

    def test_partial_actual_charge_retained_until_final_cost_is_known(self):
        meter = self.meter(budget_usd=1)
        rid = self.router_request(meter, estimated_max_usd=.5)
        meter.finish(rid, {**USAGE, "cost": .1}, status="cancelled")
        receipt = meter.finish(rid, {**USAGE, "completion_tokens": 300})
        self.assertEqual(receipt["cost_usd"], .1)
        self.assertEqual(receipt["pricing_status"], "partial")
        self.assertAlmostEqual(meter.summary()["budget"]["held_usd"], .4)
        receipt = meter.finish(rid, reported_cost_usd=.2)
        self.assertEqual(receipt["pricing_status"], "priced")
        self.assertEqual(receipt["tokens"]["output_tokens"], 300)
        self.assertEqual(meter.summary()["budget"]["held_usd"], 0)

    def test_router_cost_kwarg_does_not_override_direct_provider_semantics(self):
        meter = self.meter()
        rid = meter.begin("anthropic", "direct", "deep")
        receipt = meter.finish(rid, {"input_tokens": 1000, "output_tokens": 100}, reported_cost_usd=99)
        self.assertAlmostEqual(receipt["cost_usd"], .003)
        self.assertEqual(receipt["cost_source"], "snapshot_rates")
        self.assertIsNone(receipt["estimated_cost_usd"])


if __name__ == "__main__":
    unittest.main()
