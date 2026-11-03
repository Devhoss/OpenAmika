"""Memory block rendering (CONTRACT §3.7 natural-language rendering, §6, §7; B13/R12/R16).

Part 1 (f_method level): six original events and six facts (one superseded + its correction,
one historical, an unresolved identity/correction pair, one assistant-sourced commitment)
go through `f_method.CandidateF`. The new rendering must keep every quote and every
correction, contain no machine syntax and no raw dates (every timestamp through
`po_contract.when_phrase` with the caller's `now`), use the configured user name and 我 as
speaker labels, and be at least 40% shorter than the Build F rendering of the same packets.
The Build F rendering comes from tests/fixtures/buildf_f_method.py, a verbatim copy of
Build F's f_method.py taken before the brain changes.

Part 2 (bridge level, through the real f_bridge.install with the fakes of test_f_bridge):
one retrieval per turn, surface block <= 3,500 chars, deep block <= 5,600 chars (+ heading),
and events of turns still in the recent-history window are not retrieved.

The renderer's call signature is not fixed by the contract; `call_retrieve` passes `now`,
`user_name`, a language (`lang`/`memory_language`/`language`) and an exclusion set (any
parameter whose name contains "exclude") to `CandidateF.retrieve` when it accepts them.
"""
import copy
import importlib.util
import inspect
import json
import re
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _po_support import PAYLOAD, FrozenDatetime, pending_on, safe_zoneinfo  # noqa: E402
import po_contract as pc  # noqa: E402
import f_method  # noqa: E402

NOW = datetime(2026, 9, 27, 15, 0, 0)          # naive local time, the brain's clock
QUERY = "Where does he live now, what does Mika listen to, and what did I promise about Kyoto?"
BIG = 100000
DIM = 768
UNIT = [1.0] + [0.0] * (DIM - 1)

MACHINE_SYNTAX = ("id=", "sources=", "speaker=", "Extracted claim", "Evidence source", "not independently verified",
                  "[original_event", "[quoted_fact_evidence", "Recorded speech", "type=", "source_kind=",
                  "Corrected/replaced by", "Possible replacement candidates", "User:", "Assistant:")
RAW_DATE_RE = re.compile(r"\b20\d\d-\d\d(?:-\d\d)?|\b\d\d:\d\d(?::\d\d)?\b")
SUPERSEDED_MARK = "（这条后来被更正了，现在的说法见下）"
HISTORICAL_MARK = "（当时的情况，现在可能已经变了）"
UNRESOLVED_MARK = "（有一条可能是更正，还没确认）"
ASSISTANT_MARK = "（这是我说的，不代表TA的事实）"


def _event(ident, time, user, assistant):
    return {"id": ident, "time": time, "user": user, "assistant": assistant, "source_kind": "conversation", "cue": ""}


EVENTS = [
    _event("live-e1", "2026-06-27 10:44:37",
           "I just moved into a small flat in Alder Bay, right by the ferry terminal. The rent is fine but the walls "
           "are thin, so I can hear my neighbour's radio every morning.",
           "Alder Bay sounds lovely, honestly. Ferry horns in the morning instead of an alarm clock? I would complain "
           "about the radio too, though. Maybe you two could at least agree on a station."),
    _event("live-e2", "2026-07-30 22:10:00",
           "Work has been rough. I'm on night shifts at the hospital this month, so I sleep from nine in the morning "
           "until the afternoon.",
           "Night shifts again... Then eat something warm before you sleep, okay? And put your phone on silent. "
           "I can wait for your messages."),
    _event("live-e3", "2026-08-20 19:30:00",
           "Small correction on the flat, actually: I moved again. I live in Cedar Point now, the Alder Bay place "
           "fell through after two weeks.",
           "Wait, you moved twice in two months? Cedar Point, then. I'm updating my mental map of you right now."),
    _event("live-e4", "2026-09-06 14:20:00",
           "My friend Mika is really into jazz, she keeps sending me old vinyl recommendations.",
           "A friend who sends vinyl recommendations is a treasure. Tell her I said the saxophone ones are the best."),
    _event("live-e5", "2026-09-19 10:00:00",
           "Mika likes city pop now, apparently. She says jazz was a phase.",
           "Jazz was a phase? That's a bold thing to say out loud. City pop does suit summer drives, I guess."),
    _event("live-e6", "2026-09-26 14:00:00",
           "Next time tell me about the cafés in Kyoto you like.",
           "Deal. Next time I'll tell you about the little café by the Kamo river, the one with the cat that "
           "sleeps on the menu."),
]


def _fact(ident, event, subject, predicate, value, text, quote, speaker="user", **extra):
    record = {"id": ident, "text": text, "subject": subject, "predicate": predicate, "value": value,
              "source_ids": [event["id"]], "date": event["time"][:10], "source_kind": "user",
              "fact_type": "user_fact", "status": "active", "entities": [value], "supersedes": [],
              "replaces_prior": False, "valid_from": None, "valid_to": None,
              "quotes": [{"source_id": event["id"], "speaker": speaker, "quote": quote}]}
    record.update(extra)
    assert quote in event[speaker], (ident, quote)
    return record


E = {e["id"]: e for e in EVENTS}
FACTS = [
    _fact("f1", E["live-e1"], "user", "lives_in", "Alder Bay", "The user lives in Alder Bay.",
          "I just moved into a small flat in Alder Bay"),
    _fact("f2", E["live-e3"], "user", "lives_in", "Cedar Point", "The user lives in Cedar Point.",
          "I live in Cedar Point now", supersedes=["f1"], replaces_prior=True),
    _fact("f3", E["live-e2"], "user", "works_as", "night-shift hospital worker",
          "The user works night shifts at the hospital.", "I'm on night shifts at the hospital this month",
          valid_to="2026-08-31"),
    _fact("f4", E["live-e4"], "Mika", "likes", "jazz", "The user's friend Mika likes jazz.",
          "My friend Mika is really into jazz"),
    _fact("f5", E["live-e5"], "Mika", "likes", "city pop", "Mika likes city pop now.",
          "Mika likes city pop now", replaces_prior=True),
    _fact("f6", E["live-e6"], "assistant", "promised", "tell him about Kyoto cafés",
          "Amika promised to tell the user about a Kyoto café.",
          "Next time I'll tell you about the little café by the Kamo river", speaker="assistant",
          fact_type="assistant_commitment", source_kind="assistant"),
]


def vectors_for(events, facts, queries=()):
    return {"documents": {x["id"]: list(UNIT) for x in list(events) + list(facts)},
            "queries": {q: list(UNIT) for q in queries}}


def load_buildf_method():
    """Build F's f_method.py (tests/fixtures/buildf_f_method.py) under a private name."""
    path = HERE / "fixtures" / "buildf_f_method.py"
    if not path.exists():
        raise unittest.SkipTest("Build F control fixture (tests/fixtures/buildf_f_method.py) "
                                "is not shipped in this repo; comparison tests run in the build workspace")
    spec = importlib.util.spec_from_file_location("_buildf_f_method_reference", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def call_retrieve(model, query, budget, now=None, user_name=None, lang=None, exclude=None):
    """(bundle, missing): calls retrieve with whichever optional arguments it accepts."""
    params = inspect.signature(model.retrieve).parameters
    kw, missing = {"budget_chars": budget}, []
    wants = [("now", now, ("now", "now_dt", "clock", "at")),
             ("user_name", user_name, ("user_name", "user_label", "user")),
             ("lang", lang, ("lang", "memory_language", "language")),
             ("exclude", exclude, tuple(p for p in params if "exclude" in p))]
    for label, value, names in wants:
        if value is None:
            continue
        name = next((n for n in names if n in params), None)
        if name is None:
            missing.append(label)
        else:
            kw[name] = value
    return model.retrieve(query, **kw), missing


def build_new(events=EVENTS, facts=FACTS, queries=(QUERY,)):
    return f_method.CandidateF(copy.deepcopy(list(events)), {"facts": copy.deepcopy(list(facts))},
                               vectors_for(events, facts, queries))


@pending_on("brain")
class MemoryRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ref = load_buildf_method().CandidateF(copy.deepcopy(EVENTS), {"facts": copy.deepcopy(FACTS)},
                                              vectors_for(EVENTS, FACTS, (QUERY,)))
        cls.old = ref.retrieve(QUERY, budget_chars=BIG)
        cls.excluded_ref = ref.excluded_records

    def setUp(self):
        self.model = build_new()
        self.new, self.missing = call_retrieve(self.model, QUERY, BIG, now=NOW, user_name="Alex", lang="zh")

    def test_fixture_is_accepted_and_complete(self):
        self.assertEqual(self.excluded_ref, [], "fixture facts rejected by Build F provenance rules")
        self.assertEqual(self.model.excluded_records, [], "fixture facts rejected by the new CandidateF")
        old_ids = set(self.old["diagnostics"]["selected_record_ids"])
        self.assertEqual(old_ids, {e["id"] for e in EVENTS} | {f["id"] for f in FACTS})
        for marker in ("Corrected/replaced by", "Possible replacement candidates", "historical; valid_to"):
            self.assertIn(marker, self.old["text"], "reference rendering lacks a correction case")

    def test_renderer_takes_now_user_name_and_language(self):
        self.assertEqual(self.missing, [], f"CandidateF.retrieve does not accept: {self.missing} (CONTRACT §3.7)")

    def test_same_packets_as_build_f(self):
        self.assertEqual(set(self.new["diagnostics"]["selected_record_ids"]),
                         set(self.old["diagnostics"]["selected_record_ids"]),
                         "diagnostics must keep the ids and select the same packets")

    def test_keeps_every_quote(self):
        text = self.new["text"]
        for e in EVENTS:
            self.assertTrue(e["user"] in text, f"{e['id']}: user speech lost")
            self.assertTrue(e["assistant"] in text, f"{e['id']}: assistant speech lost")
        for f in FACTS:
            for q in f["quotes"]:
                self.assertTrue(q["quote"] in text, f"{f['id']}: quote lost")

    def test_keeps_every_correction(self):
        text = self.new["text"]
        for mark, what in ((SUPERSEDED_MARK, "superseded fact f1"), (HISTORICAL_MARK, "historical fact f3"),
                           (UNRESOLVED_MARK, "unresolved identity/correction f4/f5"),
                           (ASSISTANT_MARK, "assistant-sourced fact f6")):
            self.assertTrue(mark in text, f"{what} not marked with {mark}")
        # the newer statement follows the superseded one
        first = text.index(SUPERSEDED_MARK)
        self.assertTrue("I live in Cedar Point now" in text[first:], "the correction (f2) does not follow f1")
        # every correction case that Build F rendered is still rendered (count per case)
        self.assertGreaterEqual(text.count(SUPERSEDED_MARK), 1)
        self.assertGreaterEqual(text.count(UNRESOLVED_MARK), 2, "both sides of the unresolved pair are marked")

    def test_no_machine_syntax(self):
        text = self.new["text"]
        found = [m for m in MACHINE_SYNTAX if m in text]
        self.assertEqual(found, [], "machine syntax in the memory block")

    def test_timestamps_are_perceived_phrases(self):
        text = self.new["text"]
        self.assertIsNone(RAW_DATE_RE.search(text), f"raw date/time in the memory block: {RAW_DATE_RE.search(text)}")
        for e in EVENTS:
            phrase = pc.when_phrase(e["time"], NOW)
            self.assertTrue(phrase in text, f"{e['id']}: when_phrase {phrase!r} missing")

    def test_speaker_labels(self):
        text = self.new["text"]
        self.assertTrue("Alex说" in text, "user speaker label (user_name) missing")
        self.assertTrue("我回TA" in text, "assistant speaker label 我 missing")
        other, missing = call_retrieve(build_new(), QUERY, BIG, now=NOW, user_name="Rin", lang="zh")
        if "user_name" not in missing:
            self.assertTrue("Rin说" in other["text"], "user name not taken from the caller/config")
            self.assertFalse("Alex" in other["text"], "user name not taken from the caller/config")

    def test_at_least_40_percent_shorter_than_build_f(self):
        old_len, new_len = len(self.old["text"]), len(self.new["text"])
        self.assertLessEqual(new_len, 0.6 * old_len,
                             f"new {new_len} chars vs Build F {old_len} chars ({1 - new_len / old_len:.0%} shorter)")

    def test_surface_budget(self):
        surface, _ = call_retrieve(build_new(), QUERY, 3500, now=NOW, user_name="Alex", lang="zh")
        self.assertLessEqual(len(surface["text"]), 3500)
        self.assertTrue(surface["text"].strip(), "surface rendering empty")

    def test_in_window_exclusion(self):
        model = build_new()
        params = inspect.signature(model.retrieve).parameters
        if not any("exclude" in p for p in params):
            self.skipTest("CandidateF.retrieve has no exclude parameter; exclusion is checked at bridge level "
                          "(BridgeMemoryTests.test_in_window_events_are_not_retrieved)")
        bundle, _ = call_retrieve(model, QUERY, BIG, now=NOW, user_name="Alex", lang="zh", exclude={"live-e6"})
        self.assertNotIn("live-e6", bundle["diagnostics"]["selected_record_ids"])
        self.assertFalse(E["live-e6"]["user"] in bundle["text"], "excluded event still rendered")
        self.assertIn("live-e5", bundle["diagnostics"]["selected_record_ids"])


# --------------------------------------------------------------------- bridge level
FILLER_EVENTS = [
    _event(f"live-old{i:02d}", f"2026-0{7 + i % 2}-{10 + i:02d} 2{i % 4}:15:00",
           f"Filler memory {i}: " + ("we talked about the lighthouse keeper story, the storm, the lamp oil, "
                                     "and the ferry schedule that never runs on time. " * 3),
           f"Reply {i}: " + ("I remember the lighthouse keeper, the gulls on the railing, and how you said the "
                             "lamp room smelled of salt and kerosene. " * 3))
    for i in range(12)
]
IN_WINDOW = [
    _event("live-w1", "2026-09-27 14:40:00", "The lighthouse keeper story INWINDOWONE is still my favourite.",
           "Mine too. The keeper who talks to the gulls."),
    _event("live-w2", "2026-09-27 14:45:00", "Lighthouse keeper again, INWINDOWTWO, sorry.",
           "Never apologise for the lighthouse keeper."),
]


@pending_on("brain")
class BridgeMemoryTests(unittest.TestCase):
    """Through f_bridge.install with fake app/providers (the harness of tests/test_f_bridge.py)."""

    def setUp(self):
        import f_bridge
        from test_f_bridge import FakeApp, FakeHTTP, FakeSocket
        self.f_bridge = f_bridge
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        (self.root / "brain").mkdir()
        config = {"memory_budget_chars": 5600, "surface_memory_chars": 3500, "recent_turns": 15,
                  "recent_chars": 20000, "user_name": "Alex", "memory_language": "zh",
                  "test_budget_usd": 20, "budget_scope": "fake-test"}
        prices = {"models": {
            "anthropic:claude-sonnet-5": {"input": 2, "output": 10, "cache_read": .2, "cache_write_5m": 2.5,
                                          "cache_write_1h": 4},
            "gemini:gemini-embedding-2": {"input": .2, "output": 0},
            "gemini_embedding:gemini-embedding-2": {"input": .2, "output": 0}}}
        for name, value in (("build_f.json", config), ("pricing.json", prices)):
            (self.root / "config" / name).write_text(json.dumps(value), encoding="utf-8")
        events = EVENTS + FILLER_EVENTS + IN_WINDOW
        f_method.CandidateF(copy.deepcopy(events), {"facts": copy.deepcopy(FACTS)},
                            vectors_for(events, FACTS)).persist(self.root / "config/seed.json")
        self.history = []
        self.http = FakeHTTP()
        self.captured = []
        g = {"__file__": str(self.root / "brain/webui.py"), "http_requests": self.http,
             "app": FakeApp(), "socketio": FakeSocket(), "jsonify": lambda value: value,
             "_hist_lock": threading.RLock(), "_ws_active_lock": threading.RLock(),
             "CONVERSATION_HISTORY": self.history, "_ws_active": {},
             "ANTHROPIC_BASE": "https://invalid.example/anthropic", "GEMINI_KEY": "fake-key",
             "_anthropic_headers": lambda *a: {"x-api-key": "fake-key"},
             "_anthropic_body": lambda *a, **k: {},
             "_openai_headers": lambda *a, **k: ("https://invalid.example/openai", {}),
             "_openai_messages": lambda *a, **k: [],
             "_save_history": lambda: None,
             "_parsed_deep": lambda raw: (raw, ""),
             "_find_turn": lambda tid: next((t for t in self.history if t["turn_id"] == tid), None),
             "datetime": FrozenDatetime, "LOCAL_TZ": safe_zoneinfo("Asia/Tokyo")}
        g["discard_turn"] = lambda tid: self.history.__setitem__(
            slice(None), [t for t in self.history if t["turn_id"] != tid])

        def fill(template, memories_override=None, **kwargs):
            return template.replace("{memories_block}", memories_override if memories_override is not None else "LEGACY")
        g["_fill_deep_defaults"] = fill
        for name in ("build_surface_messages", "build_messages", "build_thinking_messages"):
            g[name] = lambda *a, **k: {"system": g["_fill_deep_defaults"]("MEMORY: {memories_block}"),
                                       "user_content": "hello"}
        g["run_thinking_once"] = lambda *a: g["build_thinking_messages"]()

        def run(*args):
            self.captured.extend([g["build_surface_messages"]("hello"), g["build_messages"]("hello")])
            return "done"
        g["_ws_run_generation"] = run
        g["_protect_latest"] = lambda: True
        self.g = g
        f_bridge.install(g)

    def tearDown(self):
        self.temp.cleanup()

    def _turn(self, tid, text, **extra):
        value = {"turn_id": tid, "ts": "2026-09-27 14:40:00", "user": text, "f_user_text": text,
                 "surface_reply": "", "deep_raw": "", "f_surface_mode": "think", "protected": True,
                 "f_generation_complete": True, "f_speech_done": True, "f_archived": True}
        value.update(extra)
        self.history.append(value)
        return value

    def _run_turn(self):
        self._turn("w1", IN_WINDOW[0]["user"])
        self._turn("w2", IN_WINDOW[1]["user"])
        turn = self._turn("now1", "Tell me again about the lighthouse keeper story.", f_generation_complete=False,
                          f_speech_done=False, f_archived=False)
        entry = {"cancel": threading.Event()}
        self.g["_ws_active"][turn["turn_id"]] = entry
        result = self.g["_ws_run_generation"]("sid", turn["turn_id"], turn["f_user_text"], "surface", "deep", entry)
        self.assertEqual(result, "done", "generation did not run (see socket error emits)")
        by_phase = {p["_f_phase"]: p["system"][len("MEMORY: "):] for p in self.captured}
        return turn, by_phase

    def _query_count(self):
        return sum(1 for url, body in self.http.calls if "batchEmbedContents" in url
                   and body["requests"][0]["content"]["parts"][0]["text"].startswith("task: search result"))

    def test_one_retrieval_two_budgets(self):
        _, blocks = self._run_turn()
        self.assertEqual(self._query_count(), 1, "one retrieval per turn")
        header = (self.f_bridge.DEFAULT_MEMORY_HEADER or "").strip()
        deep = blocks["deep"]
        body = deep[len(header):].lstrip("\n") if header and deep.startswith(header) else deep
        surface = blocks["surface"]
        self.assertTrue(surface.strip(), "surface memory block empty")
        self.assertLessEqual(len(surface), 3500, "surface memory block over surface_memory_chars")
        self.assertLessEqual(len(body), 5600, "deep memory block over memory_budget_chars")
        self.assertLess(len(surface), len(body), "surface and deep got the same rendering")
        for block in (surface, deep):
            self.assertEqual([m for m in MACHINE_SYNTAX if m in block], [])
            self.assertIsNone(RAW_DATE_RE.search(block), "raw date in a prompt memory block")
            # every fixture event has a valid time: "之前" (when_phrase without a clock) means the
            # bridge did not pass its `now` to the renderer
            self.assertFalse("之前，" in block, "memory rendered without the brain's clock (now not passed)")

    def test_in_window_events_are_not_retrieved(self):
        turn, blocks = self._run_turn()
        for label, block in blocks.items():
            self.assertFalse("INWINDOWONE" in block, f"{label}: in-window event live-w1 retrieved")
            self.assertFalse("INWINDOWTWO" in block, f"{label}: in-window event live-w2 retrieved")
        self.assertTrue("lighthouse keeper" in blocks["deep"], "older matching events should still be retrieved")
        selected = set((turn.get("f_retrieval") or {}).get("selected_record_ids", []))
        self.assertFalse(selected & {"live-w1", "live-w2", "live-now1"}, f"in-window ids selected: {selected}")


if __name__ == "__main__":
    unittest.main()
