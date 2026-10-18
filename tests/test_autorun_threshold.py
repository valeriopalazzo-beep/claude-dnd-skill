"""
test_autorun_threshold.py — --autorun-threshold 0 goes back to the player count.

push_stats.py documents 0 as "reset to player count", but the server stored
the 0 itself, and then any one ready player fired the turn for the table.

Run from repo root:
    python -m pytest tests/test_autorun_threshold.py -q
"""
import importlib.util
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO


class ThresholdTests(unittest.TestCase):
    def test_zero_resets_and_a_number_sticks(self):
        spec = importlib.util.spec_from_file_location("dnd_display_app_threshold", str(SKILL / "display" / "dnd-display-app.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod._token_ok = lambda: True
        mod._broadcast = lambda *_: None
        client = mod.app.test_client()
        self.addCleanup(setattr, mod, "_autorun_threshold", None)
        client.post("/stats", json={"autorun_threshold": 1})
        self.assertEqual(mod._autorun_threshold, 1)
        client.post("/stats", json={"autorun_threshold": 0})
        self.assertIsNone(mod._autorun_threshold)


if __name__ == "__main__":
    unittest.main()
