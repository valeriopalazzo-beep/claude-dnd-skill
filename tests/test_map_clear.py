"""
test_map_clear.py — the battle map leaves the Map panel when the fight is over.

The players' Map panel showed the newest map of the story, so the last fight's
map stayed there for the rest of the session. --turn-clear (and
map_render.py --clear) now writes a map_clear line in the log: every screen,
and every later replay, puts the maps sent so far away.

Run from repo root:
    python -m pytest tests/test_map_clear.py -q
"""
import importlib.util
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"
sys.path.insert(0, str(DISPLAY))


class MapClearTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_map_clear", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        m = self.mod
        saved = (m._broadcast, m._persist_log, m._persist_tail, m._persist_stats, m._active_campaign,
                 m._current_stats, list(m._text_log))
        def restore():
            (m._broadcast, m._persist_log, m._persist_tail, m._persist_stats, m._active_campaign,
             m._current_stats, log) = saved
            m._text_log.clear(); m._text_log.extend(log)
        self.addCleanup(restore)
        self.sent = []
        m._broadcast = self.sent.append
        m._persist_log = m._persist_tail = m._persist_stats = lambda: None
        m._active_campaign = lambda: "prova"
        m._current_stats = {"players": []}
        m._text_log.clear()

    def test_the_end_of_the_fight_puts_the_map_away(self):
        self.client.post("/stats", json={"turn_order": {"order": ["Goblin", "Aldric"], "current": "Goblin", "round": 1}})
        self.assertNotIn({"map_clear": True}, self.sent)
        self.client.post("/stats", json={"turn_order": None})
        self.assertIn({"map_clear": True}, self.sent)
        self.assertEqual(self.mod._text_log[-1], {"map_clear": True, "_camp": "prova"}, "a later screen replays it")

    def test_a_turn_clear_with_no_fight_does_nothing(self):
        self.client.post("/stats", json={"turn_order": None})
        self.assertNotIn({"map_clear": True}, self.sent)

    def test_the_dm_clears_it_by_hand(self):
        self.assertEqual(self.client.post("/map-clear").status_code, 204)
        self.assertIn({"map_clear": True}, self.sent)
        self.assertIn("map_clear", self.mod._DM_ENDPOINTS)

    def test_the_page_skips_retired_maps(self):
        html = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("kind-map:not(.map-retired)", html)
        self.assertIn("payload.map_clear", html)


if __name__ == "__main__":
    unittest.main()
