"""Surface-output parity: speech layer vs brain (CONTRACT §1.2, §4.3, §4.6, §7).

For every archived surface output in replay/corpus/surface.jsonl, the reply text spoken
by `hane_parser.SurfaceSpeechExtractor` (raw fed in seeded random chunks of 1-40 chars)
must equal `po_contract.parse_surface(raw)["reply"]`. 【...】 tags are removed on both
sides and whitespace is normalised as in test_parity_deep (see _po_support.norm_speech).
A think-mode filler is part of the reply (C14), so it must be spoken too.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _po_support import display_speech, load_corpus, norm_speech, pending_on, random_chunks, short  # noqa: E402
import po_contract as pc  # noqa: E402
import hane_parser as hp  # noqa: E402
import tts_sfx  # noqa: E402

SEEDS = (20260927, 11, "whole")
SHOW = 5


def run_extractor(raw, seed):
    ex = hp.SurfaceSpeechExtractor()
    chunks = [raw] if seed == "whole" else random_chunks(raw, seed)
    events = []
    for chunk in chunks:
        events.extend(ex.feed(chunk))
    events.extend(ex.flush())
    return "".join(d for _, d in events if tts_sfx.parse_sfx_delta(d) is None)


class SurfaceParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = load_corpus("surface")

    def test_corpus_present(self):
        self.assertGreater(len(self.rows), 900, "replay/corpus/surface.jsonl looks incomplete; run replay/build_corpus.py")

    @pending_on("speech")
    def test_reply_parity(self):
        bad, total = [], 0
        for row in self.rows:
            raw = row["raw"]
            parsed = pc.parse_surface(raw)
            want = norm_speech(parsed["reply"])
            for seed in SEEDS:
                total += 1
                try:
                    got_text = run_extractor(raw, seed)
                    got = norm_speech(got_text)
                except Exception as exc:
                    bad.append({"id": f"{row['src']}:{row['id']}", "seed": seed, "error": repr(exc), "raw": short(raw, 1200)})
                    continue
                if got != want:
                    bad.append({"id": f"{row['src']}:{row['id']} ({row['model']})", "seed": seed,
                                "mode": parsed["mode"], "want_reply": short(display_speech(parsed["reply"]), 300), "got_reply": short(display_speech(got_text), 300),
                                "raw": short(raw, 1200)})
        if bad:
            lines = [f"surface reply parity: {len(bad)} of {total} checks differ (showing {min(SHOW, len(bad))})"]
            for b in bad[:SHOW]:
                lines.append("-" * 70)
                lines.extend(f"{k}: {v}" for k, v in b.items())
            self.fail("\n".join(lines))

    @pending_on("speech")
    def test_think_mode_filler_is_spoken(self):
        """C14 / CONTRACT §4.6: a think-mode reply line is spoken, not suppressed."""
        raw = "status: ok\ntone: casual_neutral\nmode: think\nreply: 我想想……\n"
        self.assertEqual(norm_speech(run_extractor(raw, 3)), norm_speech(pc.parse_surface(raw)["reply"]))
        self.assertEqual(norm_speech(run_extractor(raw, "whole")), "我想想……")

    @pending_on("speech")
    def test_placeholder_sfx_not_spoken(self):
        """CONTRACT §1.5 / §4.3: copied placeholders never reach TTS as text or as a sound."""
        raw = "status: ok\ntone: casual_happy\nmode: say\nreply: 哈哈【声效名】好啊【register_emotion】。\n"
        ex = hp.SurfaceSpeechExtractor()
        events = []
        for chunk in random_chunks(raw, 5):
            events.extend(ex.feed(chunk))
        events.extend(ex.flush())
        spoken = "".join(d for _, d in events if tts_sfx.parse_sfx_delta(d) is None)
        sounds = [tts_sfx.parse_sfx_delta(d) for _, d in events if tts_sfx.parse_sfx_delta(d) is not None]
        self.assertNotIn("声效名", spoken)
        self.assertNotIn("register_emotion", spoken)
        self.assertEqual(sounds, [])
        self.assertEqual(norm_speech(spoken), norm_speech(pc.parse_surface(raw)["reply"]))


if __name__ == "__main__":
    unittest.main()
