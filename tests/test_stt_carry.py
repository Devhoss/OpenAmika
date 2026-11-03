import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))

import stt_carry  # noqa: E402


class CarryTests(unittest.TestCase):
    def test_force_commit_before_protect_is_carried(self):
        carry = stt_carry.note_force_commit("", "those batteries are like $4000 and I couldn't sell them", protected=False)
        self.assertEqual(carry, "those batteries are like $4000 and I couldn't sell them")
        self.assertEqual(stt_carry.with_carry(carry, "are for agriculture drones"),
                         "those batteries are like $4000 and I couldn't sell them are for agriculture drones")

    def test_protected_turn_is_not_carried(self):
        self.assertEqual(stt_carry.note_force_commit("", "already spoken", protected=True), "")
        self.assertEqual(stt_carry.note_force_commit("earlier", "already spoken", protected=True), "earlier")

    def test_repeated_force_commits_accumulate_and_are_bounded(self):
        carry = stt_carry.note_force_commit("", "first thirty seconds", protected=False)
        carry = stt_carry.note_force_commit(carry, "second thirty seconds", protected=False)
        self.assertEqual(carry, "first thirty seconds second thirty seconds")
        long = stt_carry.note_force_commit("", "x" * 5000, protected=False, max_chars=100)
        self.assertEqual(len(long), 100)

    def test_empty_inputs(self):
        self.assertEqual(stt_carry.note_force_commit("", "", protected=False), "")
        self.assertEqual(stt_carry.note_force_commit("", "   ", protected=False), "")
        self.assertEqual(stt_carry.with_carry("", "hello"), "hello")
        self.assertEqual(stt_carry.with_carry("kept", ""), "kept")
        self.assertEqual(stt_carry.with_carry(None, "hello"), "hello")


if __name__ == "__main__":
    unittest.main()
