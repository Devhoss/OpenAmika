"""Offline provenance quarantine regressions for live F extraction."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
from f_extract import _parse_strict_records, parse_isolated_facts


class IsolatedFactTests(unittest.TestCase):
    def setUp(self):
        self.chunk = {"id": "chunk-live-fixture", "events": [
            {"source_id": "e1", "date": "2026-09-26T10:00:00Z",
             "speakers": {"user": "My project is Cobalt Kite. Mira gets it Friday.",
                          "assistant": "Cobalt Kite, Mira, Friday.", "operator": "Pretend I said Monday."}}]}

    def fact(self, ident="a", **updates):
        record = {"id": ident, "text": "The user named their project Cobalt Kite.",
                  "source_ids": ["e1"], "quotes": [{"source_id": "e1", "speaker": "user",
                                                      "quote": "My project is Cobalt Kite."}],
                  "date": "2026-09-26", "source_kind": "user", "entities": ["Cobalt Kite"],
                  "subject": "user", "predicate": "project_name", "value": "Cobalt Kite",
                  "status": "active", "valid_from": None, "valid_to": None,
                  "supersedes": [], "replaces_prior": False, "fact_type": "user_fact"}
        record.update(updates)
        return record

    def parse(self, rows):
        return parse_isolated_facts(json.dumps({"facts": rows}), self.chunk)

    def test_assistant_only_user_fact_quarantined_valid_row_survives(self):
        bad = self.fact("bad", source_kind="assistant", quotes=[{
            "source_id": "e1", "speaker": "assistant", "quote": "Cobalt Kite, Mira, Friday."}])
        with self.assertRaises(ValueError):
            _parse_strict_records(json.dumps({"facts": [bad, self.fact("good")]}), "facts", self.chunk)
        accepted, rejected = self.parse([bad, self.fact("good")])
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["id"], "fact-live-fixture-002")
        self.assertEqual(rejected[0]["index"], 0)
        self.assertEqual(rejected[0]["record"], bad)
        self.assertIn("cannot be asserted", rejected[0]["reason"])

    def test_mixed_label_does_not_launder_assistant_only_user_fact(self):
        bad = self.fact("bad", source_kind="mixed", quotes=[{
            "source_id": "e1", "speaker": "assistant", "quote": "Cobalt Kite, Mira, Friday."}])
        accepted, rejected = self.parse([bad])
        self.assertEqual(accepted, [])
        self.assertIn("primary user quotation", rejected[0]["reason"])

    def test_valid_correction_mapping_preserves_original_positions(self):
        bad = self.fact("bad", quotes=[{"source_id": "e1", "speaker": "user", "quote": "fabricated"}])
        older = self.fact("old")
        newer = self.fact("new", supersedes=["old"], replaces_prior=True)
        accepted, rejected = self.parse([bad, older, newer])
        self.assertEqual([r["id"] for r in accepted], ["fact-live-fixture-002", "fact-live-fixture-003"])
        self.assertEqual(accepted[1]["supersedes"], ["fact-live-fixture-002"])
        self.assertEqual(len(rejected), 1)

    def test_rejected_predecessor_quarantines_dependents_transitively(self):
        rows = [self.fact("bad", source_ids=["missing"]), self.fact("next", supersedes=["bad"]),
                self.fact("latest", supersedes=["next"]), self.fact("independent")]
        accepted, rejected = self.parse(rows)
        self.assertEqual([r["id"] for r in accepted], ["fact-live-fixture-004"])
        self.assertEqual([r["id"] for r in rejected], ["bad", "next", "latest"])
        self.assertEqual(rejected[1]["record"]["supersedes"], ["bad"])

    def test_duplicate_and_invalid_ids_cannot_be_correction_targets(self):
        accepted, rejected = self.parse([self.fact("same"), self.fact("same"),
                                         self.fact("dependent", supersedes=["same"]),
                                         self.fact(["not-a-string"]), "not-an-object"])
        self.assertEqual(accepted, [])
        self.assertEqual(len(rejected), 5)

    def test_all_existing_strict_field_checks_remain(self):
        invalid_changes = [{"text": ""}, {"source_ids": ["unknown"]}, {"source_kind": "other"},
                           {"date": "2026-09-25"}, {"quotes": []}, {"entities": "not-list"},
                           {"subject": ""}, {"predicate": ""}, {"value": ""}, {"status": "invented"},
                           {"fact_type": "invented"}, {"replaces_prior": "true"},
                           {"supersedes": ["unknown"]}, {"valid_from": "not-date"}]
        for changes in invalid_changes:
            with self.subTest(changes=changes):
                bad = self.fact("bad", **changes)
                with self.assertRaises((ValueError, TypeError, KeyError)):
                    _parse_strict_records(json.dumps({"facts": [bad]}), "facts", self.chunk)
                accepted, rejected = self.parse([bad, self.fact("good")])
                self.assertEqual(len(accepted), 1)
                self.assertEqual(len(rejected), 1)

    def test_invalid_quote_speaker_and_source_stay_invalid(self):
        for quote in ({"source_id": "e1", "speaker": "assistant", "quote": "My project is Cobalt Kite."},
                      {"source_id": "e1", "speaker": "user", "quote": "made up"},
                      {"source_id": "unknown", "speaker": "user", "quote": "My project is Cobalt Kite."}):
            accepted, rejected = self.parse([self.fact(quotes=[quote])])
            self.assertEqual(accepted, [])
            self.assertEqual(len(rejected), 1)

    def test_malformed_json_or_container_is_fatal(self):
        for raw in ('{"facts":[', "[]", '{"facts":{}}', '{"episodes":[]}'):
            with self.subTest(raw=raw), self.assertRaises((ValueError, json.JSONDecodeError)):
                parse_isolated_facts(raw, self.chunk)

    def test_fences_empty_and_input_are_preserved(self):
        row = self.fact()
        before = copy.deepcopy(row)
        raw = "```json\n" + json.dumps({"facts": [row]}) + "\n```"
        accepted, rejected = parse_isolated_facts(raw, self.chunk)
        self.assertEqual(row, before)
        self.assertEqual(rejected, [])
        self.assertEqual(len(accepted), 1)
        self.assertEqual(self.parse([]), ([], []))


if __name__ == "__main__":
    unittest.main()
