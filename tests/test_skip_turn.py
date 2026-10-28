"""
test_skip_turn.py — "Skip turn" stages a line in the table's language.

The phone sends its display language with the skip; the queue then reads
"salta il turno" at an Italian table instead of the English "skips their
turn". Without a language the campaign's is used, and English underneath.

Run from repo root:
    python -m pytest tests/test_skip_turn.py -q
"""
import importlib.util
import pathlib
import unittest

DISPLAY = pathlib.Path(__file__).resolve().parent.parent / "skills" / "dnd" / "display"


class SkipTurnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_skip", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.mod._device_ok = lambda device, ip: "approved"
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        m = self.mod
        saved = (m._broadcast, m._check_auto_trigger, m._active_campaign)
        def restore():
            (m._broadcast, m._check_auto_trigger, m._active_campaign) = saved
        self.addCleanup(restore)
        m._broadcast = lambda payload: None
        m._check_auto_trigger = lambda: None
        m._active_campaign = lambda: ""
        with m._staged_lock:
            m._staged.clear()
        with m._stats_lock:
            self._stats = dict(m._current_stats)
            m._current_stats.clear()
            m._current_stats.update({"players": [{"name": "Lyra"}]})
        self.addCleanup(self._restore_stats)

    def _restore_stats(self):
        with self.mod._stats_lock:
            self.mod._current_stats.clear()
            self.mod._current_stats.update(self._stats)

    def skip(self, **body):
        r = self.client.post("/player-input/skip", json=dict({"character": "Lyra"}, **body))
        self.assertEqual(r.status_code, 204)
        with self.mod._staged_lock:
            return self.mod._staged["Lyra"]

    def test_italian_table_reads_italian(self):
        entry = self.skip(lang="it")
        self.assertEqual(entry["text"], "salta il turno")
        self.assertTrue(entry["ready"])

    def test_english_and_unknown_language_read_english(self):
        self.assertEqual(self.skip(lang="en")["text"], "skips their turn")
        self.assertEqual(self.skip(lang="xx")["text"], "skips their turn")

    def test_skip_does_not_wipe_a_staged_action(self):
        # Aerendyl's lost answer (s8): "skip" next to "ready" replaced his text.
        with self.mod._staged_lock:
            self.mod._staged["Lyra"] = {"text": "Rispondo ai corvi.", "ready": False, "timestamp": 0}
        r = self.client.post("/player-input/skip", json={"character": "Lyra", "lang": "it"})
        self.assertEqual(r.status_code, 409)
        with self.mod._staged_lock:
            self.assertEqual(self.mod._staged["Lyra"]["text"], "Rispondo ai corvi.")
        # Confirmed on the phone: forced through.
        self.assertEqual(self.skip(lang="it", force=True)["text"], "salta il turno")

    def test_skip_again_replaces_a_skip(self):
        self.skip(lang="it")
        self.assertEqual(self.skip(lang="en")["text"], "skips their turn")


if __name__ == "__main__":
    unittest.main()
