"""Deep-output parity: speech layer vs brain (CONTRACT §1.1, §4.2, §4.7, §7).

For every archived deep output in replay/corpus/deep.jsonl (built by
replay/build_corpus.py), `hane_parser.DeepSpeechExtractor` fed the raw text in seeded
random chunks (1-40 chars) must produce the same spoken text and the same tone sequence
as `po_contract.extract_deep(raw)`.

Comparison (see _po_support.norm_speech):
  text  = all extractor text deltas concatenated  vs  " ".join(segment texts);
          【...】 tags removed on both sides, whitespace collapsed, a space kept only
          between two Latin letters/digits.
  tones = the tone of every non-blank text delta, consecutive repeats collapsed  vs
          the tones of segments with speakable text, consecutive repeats collapsed.
          A delta tagged None counts as po_contract.DEFAULT_EMOTION (tts_emotion.FALLBACK
          equals it under CONTRACT §4.1).
  sfx   = compared separately: the sound-effect sentinels the extractor emits vs the
          canonical 【Name】 tags inside the contract's segments, in order.
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _po_support import (collapse_runs, display_speech, load_corpus, norm_speech,  # noqa: E402
                         pending_on, random_chunks, short)
import po_contract as pc  # noqa: E402
import hane_parser as hp  # noqa: E402
import tts_sfx  # noqa: E402

SEEDS = (20260927, 7, "whole")
SHOW = 5


def _chunks(raw, seed):
    return [raw] if seed == "whole" else random_chunks(raw, seed if isinstance(seed, int) else 0)


def run_extractor(raw, seed, blurt=None):
    ex = hp.DeepSpeechExtractor()
    if blurt is not None:
        ex.set_blurt(blurt)
    events = []
    for chunk in _chunks(raw, seed):
        events.extend(ex.feed(chunk))
    events.extend(ex.flush())
    text, tones, sfx = [], [], []
    for key, delta in events:
        name = tts_sfx.parse_sfx_delta(delta)
        if name is not None:
            sfx.append(pc.canonical_sfx(name) or name)
            continue
        text.append(delta)
        if norm_speech(delta):
            tones.append(key or pc.DEFAULT_EMOTION)
    return "".join(text), collapse_runs(tones), sfx


def contract_view(raw, blurt=None):
    segs = pc.extract_deep(raw, blurt)["segments"]
    text = " ".join(s["text"] for s in segs)
    tones = collapse_runs([s["tone"] for s in segs if norm_speech(s["text"])])
    sfx = []
    for s in segs:
        for m in pc.SFX_TAG_RE.finditer(s["text"]):
            name = pc.canonical_sfx(m.group(1))
            if name:
                sfx.append(name)
    return text, tones, sfx


def synthetic_blurt(raw, index):
    """A surface opener for the dedupe path: the first sentence of the first spoken
    segment (even rows), or a phrase that never matches (odd rows)."""
    if index % 2:
        return "Zqx vvk."
    segs = pc.extract_deep(raw)["segments"]
    if not segs:
        return "Zqx vvk."
    t = pc.strip_sfx(segs[0]["text"]).strip()
    m = re.search(r"[。！？!?…]+|\.(?=\s|$)", t)
    cut = t[:m.end()] if m else t[:12]
    return cut[:60] or "Zqx vvk."


def _report(title, total, bad):
    lines = [f"{title}: {len(bad)} of {total} checks differ (showing {min(SHOW, len(bad))})"]
    for b in bad[:SHOW]:
        lines.append("-" * 70)
        lines.extend(f"{k}: {v}" for k, v in b.items())
    return "\n".join(lines)


class DeepParityTests(unittest.TestCase):
    """Brain (po_contract.extract_deep) is the reference; the streaming extractor must agree."""

    @classmethod
    def setUpClass(cls):
        cls.rows = load_corpus("deep")

    def test_corpus_present(self):
        self.assertGreater(len(self.rows), 800, "replay/corpus/deep.jsonl looks incomplete; run replay/build_corpus.py")

    @pending_on("speech")
    def test_text_and_tone_parity(self):
        bad, total = [], 0
        for row in self.rows:
            raw = row["raw"]
            want_text, want_tones, _ = contract_view(raw)
            want = norm_speech(want_text)
            for seed in SEEDS:
                total += 1
                try:
                    got_text, got_tones, _ = run_extractor(raw, seed)
                except Exception as exc:  # an exception is a parity failure too
                    bad.append({"id": f"{row['src']}:{row['id']}", "seed": seed, "error": repr(exc), "raw": short(raw, 1500)})
                    continue
                got = norm_speech(got_text)
                if got != want or got_tones != want_tones:
                    bad.append({"id": f"{row['src']}:{row['id']} ({row['model']})", "seed": seed,
                                "want_text": short(display_speech(want_text), 400), "got_text": short(display_speech(got_text), 400),
                                "want_tones": want_tones, "got_tones": got_tones, "raw": short(raw, 1500)})
        if bad:
            self.fail(_report("deep text/tone parity", total, bad))

    @pending_on("speech")
    def test_sfx_events_parity(self):
        bad, total = [], 0
        for row in self.rows:
            raw = row["raw"]
            _, _, want = contract_view(raw)
            for seed in SEEDS[:1]:
                total += 1
                try:
                    _, _, got = run_extractor(raw, seed)
                except Exception as exc:
                    bad.append({"id": f"{row['src']}:{row['id']}", "error": repr(exc), "raw": short(raw, 1500)})
                    continue
                if got != want:
                    bad.append({"id": f"{row['src']}:{row['id']} ({row['model']})", "seed": seed,
                                "want_sfx": want, "got_sfx": got, "raw": short(raw, 1500)})
        if bad:
            self.fail(_report("deep SFX-event parity", total, bad))

    @pending_on("speech")
    def test_blurt_dedupe_parity(self):
        if not hasattr(hp.DeepSpeechExtractor, "set_blurt"):
            self.skipTest("DeepSpeechExtractor has no set_blurt() yet (speech agent, CONTRACT §4.7); "
                          "blurt sub-check not run")
        bad, total = [], 0
        for i, row in enumerate(self.rows):
            raw = row["raw"]
            blurt = synthetic_blurt(raw, i)
            want_text, want_tones, _ = contract_view(raw, blurt)
            want = norm_speech(want_text)
            seed = SEEDS[i % 2]
            total += 1
            try:
                got_text, got_tones, _ = run_extractor(raw, seed, blurt=blurt)
            except Exception as exc:
                bad.append({"id": f"{row['src']}:{row['id']}", "blurt": blurt, "error": repr(exc), "raw": short(raw, 1500)})
                continue
            got = norm_speech(got_text)
            if got != want or got_tones != want_tones:
                bad.append({"id": f"{row['src']}:{row['id']} ({row['model']})", "seed": seed, "blurt": blurt,
                            "want_text": short(display_speech(want_text), 400), "got_text": short(display_speech(got_text), 400),
                            "want_tones": want_tones, "got_tones": got_tones, "raw": short(raw, 1500)})
        if bad:
            self.fail(_report("deep blurt-dedupe parity", total, bad))


_TAG_BEFORE_RE = re.compile(r"\[\s*[A-Za-z][A-Za-z_\- ,]*?\s*(?:\d+(?:\.\d+)?\s*)?\]\s*[:：]?\s*$")


@pending_on("speech")
class DeepStreamingTests(unittest.TestCase):
    """CONTRACT §4.2: tagged segments stream as they arrive; the text of an untagged quote is
    never emitted before its close quote arrives (thought-leak guard)."""

    @classmethod
    def setUpClass(cls):
        cls.rows = load_corpus("deep")

    @staticmethod
    def _emitted(prefix, seed):
        ex = hp.DeepSpeechExtractor()
        out = []
        for chunk in random_chunks(prefix, seed):
            out.extend(ex.feed(chunk))
        return norm_speech("".join(d for _, d in out if tts_sfx.parse_sfx_delta(d) is None))

    def test_tagged_speech_streams_before_its_close(self):
        checked, late = 0, []
        for row in self.rows:
            raw = row["raw"]
            m = pc.TAGGED_QUOTE_RE.search(raw)
            if not m or m.start() != raw.find("["):
                continue
            group = next(g for g in ("b1", "b2", "b3") if m.group(g) is not None)
            body_start = m.start(group)
            body = raw[body_start:m.end()]
            think = pc.THINK_RE.search(raw)
            # plain first segments only: no sanitiser look-ahead characters in the first 45 chars
            if len(body) < 60 or re.search(r"[*＊\\【\[“\"「『]", body[:45]) or (think and think.start() < m.start()):
                continue
            checked += 1
            if not self._emitted(raw[:body_start + 45], 1):
                late.append(f"{row['src']}:{row['id']}: {short(raw, 300)}")
        self.assertGreater(checked, 100)
        self.assertEqual(late, [], f"{len(late)} of {checked} tagged first segments emitted nothing after 45 chars:\n"
                         + "\n".join(late[:SHOW]))

    def test_untagged_quote_not_emitted_before_close(self):
        checked, leaked = 0, []
        for row in self.rows:
            raw = row["raw"]
            think = pc.THINK_RE.search(raw)
            boundary = think.start() if think else len(raw)
            spans = [(m.start(), m.end()) for m in pc.TAGGED_QUOTE_RE.finditer(raw)]
            for i, ch in enumerate(raw[:boundary]):
                if ch not in pc.OPEN_QUOTES or _TAG_BEFORE_RE.search(raw[max(0, i - 40):i]):
                    continue
                if any(a < i < b for a, b in spans):
                    continue
                close = next((j for j in range(i + 1, len(raw)) if raw[j] in pc._QUOTE_CLOSE_FOR[ch]), None)
                if close is None or close - i < 14:
                    continue
                probe = norm_speech(pc.sanitize_speech(raw[i + 1:close]))[:10]
                if not probe or probe in norm_speech(raw[:i]):
                    continue
                checked += 1
                if probe in self._emitted(raw[:close], 3):
                    leaked.append(f"{row['src']}:{row['id']}: {raw[i:close + 1][:120]!r}")
                break
        self.assertGreater(checked, 20)
        self.assertEqual(leaked, [], f"{len(leaked)} of {checked} untagged quotes spoken before their close:\n"
                         + "\n".join(leaked[:SHOW]))


if __name__ == "__main__":
    unittest.main()
