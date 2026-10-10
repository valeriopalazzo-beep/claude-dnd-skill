"""
test_turn_order_partial.py — advancing the turn keeps the initiative order.

Each turn the DM sends only the new actor (`push_stats.py --turn-current`).
The server used to store that fragment as the whole turn order, so a browser
that loaded or reloaded mid-fight was sent a tracker with no list.

Run from repo root:
    python -m pytest tests/test_turn_order_partial.py -q
"""
import importlib.util
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
sys.path.insert(0, str(SKILL / "display"))

ORDER = ["Goblin 1", "Aldric", "Mira"]


def _import_app():
    spec = importlib.util.spec_from_file_location(
        "dnd_display_app_turn_order", str(SKILL / "display" / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TurnOrderPartialTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.mod = _import_app()
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        self.mod._current_stats = {"players": []}
        self._post({"order": ORDER, "current": "Goblin 1", "round": 1})

    def _post(self, turn_order):
        r = self.client.post("/stats", json={"turn_order": turn_order})
        self.assertEqual(r.status_code, 204)
        return self.mod._current_stats.get("turn_order")

    def test_advancing_the_turn_keeps_order_and_round(self):
        self.assertEqual(self._post({"current": "Aldric"}),
                         {"order": ORDER, "current": "Aldric", "round": 1})

    def test_new_round_keeps_the_order(self):
        self.assertEqual(self._post({"current": "Goblin 1", "round": 2}),
                         {"order": ORDER, "current": "Goblin 1", "round": 2})

    def test_round_alone_keeps_order_and_actor(self):
        self.assertEqual(self._post({"round": 3}),
                         {"order": ORDER, "current": "Goblin 1", "round": 3})

    def test_a_full_order_still_replaces_everything(self):
        fresh = {"order": ["Mira", "Orc"], "current": "Mira"}
        self.assertEqual(self._post(fresh), fresh)

    def test_clear_still_clears(self):
        self.assertIsNone(self._post(None))

    def test_partial_update_with_no_fight_running_is_stored_as_sent(self):
        self._post(None)
        self.assertEqual(self._post({"current": "Aldric"}), {"current": "Aldric"})


if __name__ == "__main__":
    unittest.main()
