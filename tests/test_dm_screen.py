"""
test_dm_screen.py — the DM's screen: what the players do not see, and rolls behind it.

/dm-screen is a page for the DM alone (players are refused): the foes' hit
points from tracker.json, the party's passive scores and spell save DCs read
from the sheets, every clock and faction (secret ones too), the secret dates,
prep.md, supplies and downtime, and dice rolled in secret — never broadcast.

Run from repo root:
    python -m pytest tests/test_dm_screen.py -q
"""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(SKILL / "scripts"))

SHEET = """# Vardamir
## Combat Stats
- **Passive Perception:** 12

## Skills
| Skill | Ability | Bonus | Proficient |
|-------|---------|-------|-----------|
| Insight | WIS | +4 | ✓ |
| Investigation | INT | −1 | — |
| Perception | WIS | +2 | — |
| Stealth | DEX | +3 | — |

*Caratteristica da incantatore: CAR — CD dei tiri salvezza 13, attacco con incantesimi +5*
"""


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod             # Flask finds templates/ through the module
    spec.loader.exec_module(mod)
    return mod


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load("dnd_display_app_dm_screen", DISPLAY / "dnd-display-app.py")
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.camp = pathlib.Path(tmp.name)
        (self.camp / "vardamir.md").write_text(SHEET, encoding="utf-8")
        m = self.mod
        saved = (m._active_campaign, m._find_campaign, m._broadcast, m._find_sheet_file)
        self.addCleanup(lambda: (setattr(m, "_active_campaign", saved[0]), setattr(m, "_find_campaign", saved[1]),
                                 setattr(m, "_broadcast", saved[2]), setattr(m, "_find_sheet_file", saved[3])))
        self.sent = []
        m._active_campaign, m._find_campaign, m._broadcast = (lambda: "prova"), (lambda n: self.camp), self.sent.append
        m._find_sheet_file = lambda name: (str(self.camp / "vardamir.md"), name, "prova")
        with m._stats_lock:
            m._current_stats["players"] = [{"name": "Vardamir", "hp": {"current": 9, "max": 14}, "ac": 12,
                                            "concentration": "Benedizione", "load": {"status": "encumbered"}}]
            m._current_stats["turn_order"] = {"order": ["Goblin", "Vardamir"], "current": "Goblin", "round": 2}
        with m._dm_rolls_lock:
            m._dm_rolls.clear()
        self.write("tracker.json", {"goblin": {"name": "Goblin", "hp": 3, "max_hp": 7, "ac": 15, "conditions": ["prone"]},
                                    "vardamir": {"name": "Vardamir", "conditions": []},
                                    "old": {"name": "Morto da tempo", "conditions": []}})
        self.write("clocks.json", {"clocks": [{"name": "Rinforzi", "segments": 6, "filled": 2, "kind": "danger", "secret": True}]})
        self.write("factions.json", {"factions": [{"name": "La Confraternita", "score": -8, "secret": True}]})
        self.write("calendar.json", {"events": [{"name": "Il rito", "day": 16, "month": 10, "year": 1147, "secret": True},
                                                {"name": "Festa", "day": 30, "month": 10}]})
        self.write("timeline.json", {"events": [{"title": "Il patto di Corradi", "date": "13 Brumosa", "secret": True}]})
        (self.camp / "prep.md").write_text("# Session 4 prep\n## 2. Strong Start\nLa campana.", encoding="utf-8")

    def write(self, name, data):
        (self.camp / name).write_text(json.dumps(data), encoding="utf-8")

    def test_the_data_holds_what_the_players_do_not_see(self):
        d = self.client.get("/dm-screen/data").get_json()
        self.assertEqual([(f["name"], f["hp"], f["max_hp"], f["ac"]) for f in d["foes"]], [("Goblin", 3, 7, 15)])
        (p,) = d["party"]
        self.assertEqual((p["passive"], p["spell_dc"], p["load"], p["concentration"]),
                         ({"perception": 12, "insight": 14, "investigation": 9}, 13, "encumbered", "Benedizione"))
        self.assertEqual(d["clocks"][0]["name"], "Rinforzi")
        self.assertEqual(d["factions"], [{"name": "La Confraternita", "score": -8, "standing": "Hostile", "secret": True}])
        self.assertEqual([s["what"] for s in d["secrets"]], ["Il rito", "Il patto di Corradi"])
        self.assertIn("La campana", d["prep"])
        self.assertEqual(d["order"]["current"], "Goblin")
        self.assertEqual(d["dcs"][2], ["medium", 15])

    def test_hidden_rolls_are_kept_here_and_never_broadcast(self):
        r = self.client.post("/dm-screen/roll", json={"notation": "1d20+5", "label": "Furtività del goblin", "advantage": "advantage"})
        self.assertEqual(r.status_code, 200)
        roll = r.get_json()
        self.assertEqual((roll["notation"], len(roll["both"]), roll["total"]), ("1d20+5", 2, max(roll["both"]) + 5))
        g = self.client.post("/dm-screen/roll", json={"skill": "Insight"}).get_json()
        self.assertEqual([(x["who"], x["notation"]) for x in g["group"]], [("Vardamir", "1d20+4")])
        self.assertEqual(self.client.post("/dm-screen/roll", json={"notation": "tanti"}).status_code, 400)
        self.assertEqual(self.client.post("/dm-screen/roll", json={"skill": "Volare"}).status_code, 400)
        self.assertEqual(self.sent, [])
        rolls = self.client.get("/dm-screen/data").get_json()["rolls"]
        self.assertEqual([r.get("skill") for r in rolls], ["Insight", None])

    def test_the_page_and_the_data_are_the_dms_only(self):
        for ep in ("dm_screen", "dm_screen_data", "dm_screen_roll"):
            self.assertIn(ep, self.mod._DM_ENDPOINTS)
        page = self.client.get("/dm-screen")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"/dm-screen/data", page.data)


class TrackerHpTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        (root / "campaigns" / "prova").mkdir(parents=True)
        old = os.environ.get("DND_CAMPAIGN_ROOT")
        os.environ["DND_CAMPAIGN_ROOT"] = str(root)
        self.addCleanup(lambda: os.environ.pop("DND_CAMPAIGN_ROOT", None) if old is None
                        else os.environ.__setitem__("DND_CAMPAIGN_ROOT", old))
        self.tr = _load("tracker_under_test_hp", SKILL / "scripts" / "tracker.py")
        self.tr._send_announce = lambda msg: None
        self.tr._push_conditions = lambda *a, **k: None

    def quiet(self, fn, *a, **k):
        with contextlib.redirect_stdout(io.StringIO()):
            return fn(*a, **k)

    def test_hit_takes_damage_off_and_a_fall_ends_concentration(self):
        self.quiet(self.tr.cmd_hp, "prova", "Isolda", 29, maximum=52, ac=14)
        self.quiet(self.tr.cmd_concentrate, "prova", "Isolda", "Blocca persone")
        res = self.quiet(self.tr.cmd_hit, "prova", "Isolda", 30)
        ent = self.tr._entity(self.tr._load("prova"), "Isolda")
        self.assertEqual((ent["hp"], ent["max_hp"], ent["ac"]), (0, 52, 14))
        self.assertEqual((res["down"], ent["concentration"]), (True, None))
        self.quiet(self.tr.cmd_clear, "prova")
        self.assertNotIn("hp", self.tr._entity(self.tr._load("prova"), "Isolda"))


class PageTests(unittest.TestCase):
    def test_the_display_links_to_it_and_the_strings_exist(self):
        html = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("window.open('/dm-screen'", html)
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("dms.title", "dms.rolls", "dms.groupRoll", "dms.dc.medium", "dms.prep"):
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
