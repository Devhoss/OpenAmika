"""C19 widened (2026-11-02): the deep's echo of the surface line may hide behind a short lead-in or repeat a
later clause of the line. The six cases are the real misses from 147 transcript turns; the controls are
openers that must survive. Both contract copies and the gateway's streaming gate must agree."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# the same path setup as the parity tests: the brain's po_contract is *the* po_contract for the whole test
# session (the gateway's hane_parser imports it by that name); the gateway's copy is loaded under another
# name only to prove both copies agree
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _po_support                           # noqa: E402,F401
import po_contract as pc                     # noqa: E402  (brain copy)
import hane_parser as hp                     # noqa: E402
import importlib.util                        # noqa: E402

spec = importlib.util.spec_from_file_location("speech_po_contract", ROOT / "speech" / "po_contract.py")
spc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spc)

REAL_MISSES = [
    # (surface line, deep first segment, what she should still say)
    ("One in the morning—", "—Wait, one in the morning? Your time?", "Your time?"),
    ("Your sleep schedule, huh.", "...Yeah. Your sleep schedule. That's what's incredible. Not in a good way.",
     "That's what's incredible. Not in a good way."),
    ("Turn your lights right out?", "Wait— 'turn your lights right out'? What does that even mean?", "What does that even mean?"),
    ("Photos, huh… Okay, let me have Hermes pull some up for you.", "Let me have Hermes pull some up for you.", ""),
    ("Wait, you actually like the smell?", "—you actually like the smell?", ""),
    ("A hot car? You were eating in your car?", "Wait, hold on— you were eating in your car? At the hotpot place?",
     "At the hotpot place?"),
]
CONTROLS = [
    ("17 times 23? Hold on—", "—Hermes, what's 17 times 23?"),            # her own sentence to a helper, not a lead-in + echo
    ("Okay. Let's go.", "Let's go somewhere else, then."),                  # 0.60 of the blurt: a shared clause, not an echo
    ("Yeah, I can hear you.", "What were you saying about the museum?"),   # nothing in common
    ("Booking every minute, huh…", "Wait— you book every single minute?"),  # shared opener that diverges mid-clause
    ("嗯。", "好啊，走吧。"),                                                # nothing in common (CJK)
    ("That song, huh…", "That's a different thing entirely."),            # diverges inside the first word
]
KEEP = [("Booking every minute, huh…", "Booking every minute, huh… That sounds exhausting.", "That sounds exhausting."),
        ("Tacos?", "Tacos? That's random.", "That's random."),               # a short whole-line echo still counts
        ("I know.", "I know. ...I can hear it.", "I can hear it."),       # the echo's trailing dots go with it
        ("嗯。", "嗯，我在听。", "我在听。"),                                   # a one-character whole-line echo (as before)
        ("One in the morning—", "Haha, one in the morning? Your time?", "Your time?"),   # interjection lead-in
        ("好吧，那就明天再说。", "好吧，那就明天再说。你早点睡。", "你早点睡。"),
        ("ＯＫ, got it.", "OK, got it. Anything else?", "Anything else?")]   # full-width letters fold


class EchoTests(unittest.TestCase):
    def test_real_misses_are_now_stripped(self):
        for blurt, deep, want in REAL_MISSES:
            for mod in (pc, spc):
                self.assertEqual(mod.dedupe_blurt(deep, blurt), want, (mod.__name__, blurt, deep))

    def test_controls_survive(self):
        for blurt, deep in CONTROLS:
            for mod in (pc, spc):
                self.assertEqual(mod.dedupe_blurt(deep, blurt), deep, (mod.__name__, blurt, deep))

    def test_prefix_rule_still_holds(self):
        for blurt, deep, want in KEEP:
            for mod in (pc, spc):
                self.assertEqual(mod.dedupe_blurt(deep, blurt), want, (mod.__name__, blurt, deep))

    def test_extract_deep_applies_it_to_the_first_segment(self):
        raw = '[casual_surprise]"Wait, hold on— you were eating in your car? At the hotpot place?"[casual_playful]"Wow."\n我心想：嗯。'
        r = pc.extract_deep(raw, blurt="A hot car? You were eating in your car?")
        self.assertEqual([s["text"] for s in r["segments"]], ["At the hotpot place?", "Wow."])
        gone = pc.extract_deep('[casual_neutral]"—you actually like the smell?"\n我心想：嗯。', blurt="Wait, you actually like the smell?")
        self.assertEqual(gone["segments"], [])

    def test_streaming_gate_holds_then_cuts_the_echo_and_its_quote(self):
        gate = hp._BlurtGate("Turn your lights right out?")
        out = ""
        for chunk in ["Wait— 'turn your", " lights right", " out'? What does", " that even mean?"]:
            out += gate.feed(chunk)
        out += gate.flush()
        self.assertEqual(out, "What does that even mean?")

    def test_streaming_gate_releases_an_opener_that_diverges(self):
        gate = hp._BlurtGate("Yeah, I can hear you.")
        out = "".join(gate.feed(c) for c in ["What were", " you saying", " about the museum?"]) + gate.flush()
        self.assertEqual(out, "What were you saying about the museum?")

    def test_streaming_gate_drops_a_segment_that_is_only_the_echo(self):
        gate = hp._BlurtGate("Photos, huh… Okay, let me have Hermes pull some up for you.")
        out = "".join(gate.feed(c) for c in ["Let me have", " Hermes pull some up", " for you."]) + gate.flush()
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
