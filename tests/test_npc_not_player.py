"""
test_npc_not_player.py — the display's player list holds PCs only.

A name with no character sheet (an NPC the DM pushed by mistake, e.g.
`push_stats.py --player "Ser Bertrand" --hp 30 30`) must not become a player
card: every card gets an input tab and an autorun slot the table would have
to skip.

Run from repo root:
    python -m pytest tests/test_npc_not_player.py -q
"""
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
sys.path.insert(0, str(SKILL / "display"))


def _import_app():
    spec = importlib.util.spec_from_file_location(
        "dnd_display_app", str(SKILL / "display" / "dnd-display-app.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class NpcNotPlayerTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.mod = _import_app()
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()
        cls.roster = pathlib.Path(tempfile.mkdtemp(prefix="dnd-test-roster-"))
        (cls.roster / "lyra-ombrafina.md").write_text(
            "# Lyra Ombrafina\n**Player:** Jess\n", encoding="utf-8")
        (cls.roster / "vardamir-ma-feyn.md").write_text(
            "# Vardamir Ma'feyn\n**Player:** —\n", encoding="utf-8")
        import paths
        cls._orig_chars = paths.characters_dir
        paths.characters_dir = lambda: cls.roster
        cls._orig_active = cls.mod._active_campaign
        cls.mod._active_campaign = lambda: ""

    @classmethod
    def tearDownClass(cls):
        import paths
        paths.characters_dir = cls._orig_chars
        cls.mod._active_campaign = cls._orig_active

    def setUp(self):
        self.mod._current_stats = {"players": []}

    def _post(self, body):
        return self.client.post("/stats", data=json.dumps(body),
                                content_type="application/json")

    def _names(self):
        return [p["name"] for p in self.mod._current_stats.get("players", [])]

    def test_pcs_with_a_sheet_are_added(self):
        r = self._post({"replace_players": True, "players": [
            {"name": "Lyra Ombrafina"}, {"name": "Vardamir Ma'feyn"}]})
        self.assertEqual(r.status_code, 204, msg=r.data)
        self.assertEqual(self._names(), ["Lyra Ombrafina", "Vardamir Ma'feyn"])

    def test_npc_without_a_sheet_is_left_out(self):
        r = self._post({"players": [{"name": "Lyra Ombrafina"},
                                    {"name": "Ser Bertrand", "hp": {"current": 30, "max": 30}}]})
        self.assertEqual(self._names(), ["Lyra Ombrafina"])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), {"ignored_players": ["Ser Bertrand"]})

    def test_npc_does_not_count_for_autorun(self):
        self._post({"players": [{"name": "Lyra Ombrafina"}, {"name": "Incappucciato"}]})
        self.assertEqual(self.mod._expected_count, 1)

    def test_existing_card_still_updates(self):
        # A card already on the list keeps taking updates, sheet or not.
        self.mod._current_stats = {"players": [{"name": "Old Hand", "hp": {"current": 5, "max": 9}}]}
        self._post({"players": [{"name": "Old Hand", "hp": {"current": 9, "max": 9}}]})
        self.assertEqual(self.mod._current_stats["players"][0]["hp"]["current"], 9)

    def test_no_roster_means_no_filter(self):
        import paths
        empty = pathlib.Path(tempfile.mkdtemp(prefix="dnd-test-empty-"))
        paths.characters_dir = lambda: empty
        try:
            self._post({"players": [{"name": "Aldric"}]})
            self.assertEqual(self._names(), ["Aldric"])
        finally:
            paths.characters_dir = lambda: self.roster


if __name__ == "__main__":
    unittest.main()
