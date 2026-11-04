"""Config contract (CONTRACT §6, final 2026-09-27) for payload/build_f.json."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _po_support import PAYLOAD, pending_on  # noqa: E402
from _shipped import ROOT, shipped_bytes  # noqa: E402

BUILD_F_JSON = PAYLOAD / "build_f.json"
if not BUILD_F_JSON.exists():
    BUILD_F_JSON = PAYLOAD.parent / "config" / "build_f.json"   # repo layout


def shipped_config() -> dict:
    """build_f.json as it ships: the first-run wizard writes the person's name, city and clock into the working copy."""
    try:
        raw = shipped_bytes(BUILD_F_JSON.resolve().relative_to(ROOT).as_posix())
    except ValueError:                                   # outside this checkout (the legacy payload layout)
        raw = BUILD_F_JSON.read_bytes()
    return json.loads(raw.decode("utf-8-sig"))


# Budgets follow the shipped config (the owner tuned them below the CONTRACT §6
# starting values after live A/B runs: 3600/2000 instead of 5600/3500).
WANT = {"app": "OpenAmika", "memory_budget_chars": 3600, "surface_memory_chars": 2000,
        "recent_turns": 15, "recent_chars": 20000, "history_thought_turns": 3, "user_name": "User",
        "memory_language": "zh", "idle_reflection": True, "auto_open_from_reflection": True,
        "legacy_consolidation": False, "memory_profile": "live"}


@pending_on("brain")
class BuildFConfigTests(unittest.TestCase):
    def test_contract_keys(self):
        cfg = shipped_config()
        self.assertEqual({k: cfg.get(k) for k in WANT}, WANT)

    def test_no_bom_and_valid_json(self):
        raw = BUILD_F_JSON.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        json.loads(raw.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
