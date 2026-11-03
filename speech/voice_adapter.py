"""OpenAmika voice adapter — new-vocab (V2) → old-vocab (V1) emotion remap at the TTS seam.

The brain, the parsers and the Live2D banks all speak the 69-key
`<register>_<emotion>` vocabulary (po_contract.VALID_EMOTIONS) — that contract never
changes. The voices OpenAmika SHIPS, however, are the old-vocabulary (V1) ones
(MekaHime_Amika, Amika's voice from scripts/setup_tts.py), whose tag set is a flat 67 reference ids
(`neutral1`, `happy2`, `tsun3` …). This adapter is therefore ON BY DEFAULT: it bridges
the internal 69-key tags to the shipped V1 voice at the TTS boundary. Users who bring
their own V2-native voice simply switch it off on the Setup page. Ported from the
MekaHime_Live_2 backward-compatible controller (sarah_tts_v1_defaults +
stt_tts_v1_shim) with the same guarantees:

  * one shared pure-data module — the two vocabularies, the default map and the pure
    helpers live HERE and nowhere else (the UI reads them over /api/voice_map);
  * adapt only the outbound text + speaker. The `emotion` field reported back to the
    browser / Live2D stays the V2 key: one tag, two vocabularies, two consumers;
  * idempotent passthrough — an already-V1 leading tag is left unchanged, an untagged
    string gets 【neutral1】 prepended (V1 voices must always be tagged);
  * validate-and-gap-fill on read (merge_map): a stale / partial / hand-edited map
    file still yields a complete valid 69-key table;
  * mtime-gated hot reload with last-good retention — edit config/voice_map.json (or
    use the Setup page) and the next sentence uses it; a broken file never throws.

Config file: config/voice_map.json
    {"enabled": true, "voice": "MekaHime_Amika", "map": {"casual_happy": "happy2", ...}}
`enabled: false` leaves requests untouched (bring-your-own V2-native voice). A missing
file falls back to the shipped default: ENABLED, voice MekaHime_Amika, built-in map.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple

import po_contract

# ── V2: the 69 register_emotion keys (single source of truth: po_contract) ──
V2_GROUPED = {reg: list(po_contract.EMOTION_SETS[reg]) for reg in po_contract.REGISTERS}
V2_KEYS = [f"{reg}_{emo}" for reg in po_contract.REGISTERS for emo in po_contract.EMOTION_SETS[reg]]
V2_KEY_SET = set(V2_KEYS)
assert V2_KEY_SET == set(po_contract.VALID_EMOTIONS), "voice_adapter V2 vocab drifted from po_contract"

# ── V1: the 67 flat emotion-reference ids of the old voices ──
V1_IDS = [
    "neutral1",
    "happy1", "happy2", "happy3",
    "sad1", "sad2", "sad3",
    "angry1", "angry2", "angry3", "angry4",
    "fear1", "fear2", "fear3", "fear4",
    "surprise1", "surprise2",
    "awe1",
    "shy1", "shy2", "shy3",
    "embarrassed1",
    "lonely1",
    "jealous1", "jealous2", "jealous3",
    "tsun1", "tsun2", "tsun3",
    "awkwardness1",
    "caring1", "caring2", "caring3",
    "protective1",
    "relieved1", "relieved2",
    "worried1", "worried2",
    "anxiety1",
    "annoyed1", "annoyed2", "annoyed3", "annoyed4",
    "frustrated1",
    "disappointed1",
    "sarcastic1",
    "playful1", "playful2", "playful3",
    "proud1",
    "pout1",
    "cold1", "cold2", "cold3",
    "sexual_desire1", "sexual_desire2", "sexual_desire3",
    "whisper1", "whisper2", "whisper3",
    "tired1", "tired2",
    "sleepy1",
    "breathy1",
    "monotone1",
    "firm1",
    "mumbling1",
]
V1_ID_SET = set(V1_IDS)

# The V1 speakers OpenAmika ships for (suggestions for the UI dropdown; any non-empty
# character_id is accepted — the gateway is the final validator).
V1_VOICES = ["MekaHime_Amika"]
DEFAULT_VOICE = "MekaHime_Amika"
# Shipping policy: only V1 voices are distributed, so the adapter defaults to ON.
DEFAULT_ENABLED = True

# ── Default 69 → 67 map (emotion-fidelity: keep the feeling; delivery-flavored V1 ids
# only on a register's own neutral cells). Operator-editable starting point. ──
DEFAULT_MAP: Dict[str, str] = {
    # polite — measured, mid-low intensity
    "polite_neutral": "neutral1", "polite_smile": "happy1", "polite_happy": "happy1",
    "polite_caring": "caring1", "polite_sad": "sad1", "polite_worried": "worried1",
    "polite_cold": "cold1", "polite_angry": "annoyed1", "polite_embarrassed": "embarrassed1",
    "polite_surprise": "surprise1", "polite_fear": "fear1",
    # casual — default mid variants
    "casual_neutral": "neutral1", "casual_smile": "happy1", "casual_happy": "happy2",
    "casual_very_happy": "happy3", "casual_laughing": "playful3", "casual_playful": "playful2",
    "casual_caring": "caring2", "casual_relieved": "relieved1", "casual_shy": "shy2",
    "casual_sad": "sad2", "casual_worried": "worried1", "casual_frustrated": "frustrated1",
    "casual_angry": "angry2", "casual_jealous": "jealous2", "casual_embarrassed": "embarrassed1",
    "casual_surprise": "surprise1", "casual_fear": "fear2", "casual_tired": "tired1",
    "casual_mumbling": "mumbling1",
    # serious — firmer/intenser
    "serious_neutral": "firm1", "serious_protective": "protective1", "serious_cold": "cold3",
    "serious_worried": "worried2", "serious_anxious": "anxiety1", "serious_disappointed": "disappointed1",
    "serious_sad": "sad2", "serious_angry": "angry3", "serious_very_angry": "angry4",
    "serious_fear": "fear3", "serious_surprise": "surprise2",
    # intimate — warm/soft mid
    "intimate_neutral": "neutral1", "intimate_smile": "happy1", "intimate_happy": "happy2",
    "intimate_very_happy": "happy3", "intimate_caring": "caring3", "intimate_very_caring": "caring3",
    "intimate_playful": "playful1", "intimate_shy": "shy2", "intimate_very_shy": "shy3",
    "intimate_relieved": "relieved2", "intimate_sad": "sad2", "intimate_very_sad": "sad3",
    "intimate_tearful": "sad3", "intimate_worried": "worried1", "intimate_jealous": "jealous1",
    "intimate_tired": "tired2", "intimate_sleepy": "sleepy1", "intimate_mumbling": "mumbling1",
    # whisper — keep the feeling; breathy whisper ids only on neutral/smile
    "whisper_neutral": "whisper1", "whisper_smile": "whisper2", "whisper_happy": "happy1",
    "whisper_caring": "caring1", "whisper_relieved": "relieved2", "whisper_shy": "shy1",
    "whisper_sad": "sad1", "whisper_tearful": "sad1", "whisper_worried": "worried1",
    "whisper_sleepy": "sleepy1",
}

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "voice_map.json"

# ── pure helpers (shared by the TTS client and the /api/voice_map endpoints) ──


def pick_voice(voice: Optional[str]) -> str:
    """Coerce the configured speaker to a usable V1 character_id (fallback = default)."""
    voice = (voice or "").strip()
    return voice if voice else DEFAULT_VOICE


def merge_map(partial: Optional[Dict[str, str]]) -> Dict[str, str]:
    """Full 69-key map = defaults overlaid with the operator's picks, each cell
    validated against BOTH vocabularies — a stale or hand-edited file stays safe."""
    full = dict(DEFAULT_MAP)
    for k, v in (partial or {}).items():
        if k in V2_KEY_SET and v in V1_ID_SET:
            full[k] = v
    return full


_LEAD_TAG = re.compile(r"^\s*【([^】]+)】(.*)$", re.S)


def remap_leading_tag(text: str, full_map: Dict[str, str]) -> str:
    """Rewrite the leading 【V2key】 of a TTS request into 【V1id】 using `full_map`.
    No tag → prepend 【neutral1】 (V1 voices must always be tagged). An already-V1 id
    passes through unchanged (idempotent). Only the FIRST tag is touched — SFX cues
    are a separate branch in the speech layer and never reach the TTS text."""
    if not text:
        return text
    m = _LEAD_TAG.match(text)
    if not m:
        return "【neutral1】" + text
    key = (m.group(1) or "").strip()
    if key in V1_ID_SET:
        v1 = key                                   # already a V1 id — leave it
    else:
        v1 = full_map.get(key) or DEFAULT_MAP.get(key) or "neutral1"
    return f"【{v1}】{m.group(2)}"


# ── config IO: mtime-gated hot reload with last-good retention ──

_cache_sig: Optional[Tuple[float, int]] = None
_cache_state: Tuple[bool, str, Dict[str, str]] = (DEFAULT_ENABLED, DEFAULT_VOICE, dict(DEFAULT_MAP))


def live_state() -> Tuple[bool, str, Dict[str, str]]:
    """(enabled, voice, full_map) from config/voice_map.json. Reloads only when the
    file's mtime/size changes; any read/parse failure keeps the last good value."""
    global _cache_sig, _cache_state
    try:
        stat = CONFIG_PATH.stat()
        sig = (stat.st_mtime, stat.st_size)
    except OSError:
        _cache_sig = None
        _cache_state = (DEFAULT_ENABLED, DEFAULT_VOICE, dict(DEFAULT_MAP))
        return _cache_state
    if sig == _cache_sig:
        return _cache_state
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        enabled = bool(raw.get("enabled"))
        voice = pick_voice(raw.get("voice"))
        full = merge_map(raw.get("map"))
        _cache_sig = sig
        _cache_state = (enabled, voice, full)
    except Exception:
        pass  # keep last good state; a broken file must never break speech
    return _cache_state


def save_state(enabled: bool, voice: str, partial_map: Optional[Dict[str, str]]) -> dict:
    """Validate and atomically write config/voice_map.json; returns the stored doc."""
    doc = {
        "enabled": bool(enabled),
        "voice": pick_voice(voice),
        "map": {k: v for k, v in (partial_map or {}).items()
                if k in V2_KEY_SET and v in V1_ID_SET},
    }
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(CONFIG_PATH.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return doc


def reset_state() -> None:
    """Delete the map file → the shipped default: adapter ON, MekaHime_Amika, built-in map."""
    try:
        CONFIG_PATH.unlink()
    except FileNotFoundError:
        pass
    global _cache_sig, _cache_state
    _cache_sig = None
    _cache_state = (DEFAULT_ENABLED, DEFAULT_VOICE, dict(DEFAULT_MAP))


if __name__ == "__main__":
    # self-test: complete coverage, valid targets, idempotent remap
    assert set(DEFAULT_MAP) == V2_KEY_SET, (
        f"DEFAULT_MAP keys != V2 vocab: missing={V2_KEY_SET - set(DEFAULT_MAP)} "
        f"extra={set(DEFAULT_MAP) - V2_KEY_SET}")
    assert set(DEFAULT_MAP.values()) <= V1_ID_SET
    assert len(V1_IDS) == 67 and len(V2_KEYS) == 69
    full = merge_map({"casual_happy": "playful1", "bogus": "happy1", "polite_sad": "bogus"})
    assert full["casual_happy"] == "playful1" and full["polite_sad"] == "sad1" and "bogus" not in full
    once = remap_leading_tag("【casual_happy】.你好", full)
    assert once == "【playful1】.你好" and remap_leading_tag(once, full) == once
    assert remap_leading_tag("no tag", full) == "【neutral1】no tag"
    assert DEFAULT_ENABLED is True, "shipping policy: V1 voices only, adapter on by default"
    print(f"voice_adapter OK: {len(V2_KEYS)} V2 keys → {len(V1_IDS)} V1 ids, "
          f"default voice {DEFAULT_VOICE}, adapter default-{'ON' if DEFAULT_ENABLED else 'off'}")
