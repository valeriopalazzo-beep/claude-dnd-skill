"""
test_concentration.py — damage asks for the concentration save by itself.

When an HP push lowers the HP (or the temporary HP) of a character who is
concentrating, the display opens a Constitution save on that player's screen:
DC 10 or half the damage, the modifier and War Caster read from the sheet.
A result below the DC ends the spell on the display and tells the DM; at 0 HP
the concentration simply ends. tracker.py hit does the same for monsters.

Run from repo root:
    python -m pytest tests/test_concentration.py -q
"""
import contextlib
import importlib.util
import io
import os
import pathlib
import random
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(SKILL / "scripts"))

import spellbook  # noqa: E402

SHEET = """# Lyra
## Ability Scores
| STR | DEX | CON | INT | WIS | CHA |
|-----|-----|-----|-----|-----|-----|
| 8 (-1) | 16 (+3) | 14 (+2) | 12 (+1) | 10 (+0) | 16 (+3) |

## Saving Throws
| STR | DEX | CON | INT | WIS | CHA |
|-----|-----|-----|-----|-----|-----|
| -1 | +5* | +4* | +1 | +0 | +3 |

## Features & Traits
{feat}
"""


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load("dnd_display_app_concentration", DISPLAY / "dnd-display-app.py")
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.sheet = pathlib.Path(tmp.name) / "lyra.md"
        self.sheet.write_text(SHEET.format(feat="- —"), encoding="utf-8")
        m = self.mod
        saved = (m._find_sheet_file, m._broadcast, m._spellbook.casts_add, m._persist_stats, m._spell_lang,
                 m.secrets.randbelow, m._phone_present)
        self.addCleanup(lambda: (setattr(m, "_find_sheet_file", saved[0]), setattr(m, "_broadcast", saved[1]),
                                 setattr(m._spellbook, "casts_add", saved[2]), setattr(m, "_persist_stats", saved[3]),
                                 setattr(m, "_spell_lang", saved[4]), setattr(m.secrets, "randbelow", saved[5]),
                                 setattr(m, "_phone_present", saved[6])))
        self.sent, self.notes = [], []
        m._find_sheet_file = lambda name: (str(self.sheet), name, "prova")
        m._broadcast = self.sent.append
        m._spellbook.casts_add = self.notes.append
        m._persist_stats = lambda: None
        m._spell_lang = lambda value="": "it"
        m._phone_present = lambda name: True
        with m._stats_lock:
            m._current_stats["players"] = [
                {"name": "Lyra", "hp": {"current": 20, "max": 20, "temp": 5}, "concentration": "Benedizione",
                 "effects": [{"name": "Benedizione", "concentration": True, "duration_type": "rounds", "duration_remaining": 10}]},
                {"name": "Ajeje", "hp": {"current": 17, "max": 17}}]

    def player(self, name="Lyra"):
        return next(p for p in self.mod._current_stats["players"] if p["name"] == name)

    def hp(self, current, temp=None, name="Lyra"):
        hp = {"current": current, "max": 20}
        if temp is not None:
            hp["temp"] = temp
        r = self.client.post("/stats", json={"players": [{"name": name, "hp": hp}]})
        self.assertIn(r.status_code, (200, 204))

    def requests(self):
        return [p["dice_request"] for p in self.sent if "dice_request" in p]

    def roll(self, rid, natural):
        self.mod.secrets.randbelow = lambda n: natural - 1
        r = self.client.post("/player-input/dice", json={"character": "Lyra", "spec": "1d20", "modifier": 4, "request_id": rid})
        self.assertEqual(r.status_code, 200)
        return r.get_json()["total"]

    def test_damage_opens_the_save_with_the_right_dc_and_modifier(self):
        self.hp(12, 0)                                  # 8 HP + 5 temporary = 13 damage → DC 10
        (req,) = self.requests()
        self.assertEqual((req["characters"], req["spec"], req["modifier"], req["advantage"], req["dc"]),
                         (["Lyra"], "1d20", 4, "normal", 10))
        self.assertEqual(req["label"], "Concentrazione: Benedizione")
        self.hp(1, 0)                                   # 11 more → DC 10 again (half is 5)
        self.hp(1, 0)                                   # no damage, no save
        self.assertEqual(len(self.requests()), 2)

    def test_a_big_hit_raises_the_dc_and_war_caster_gives_advantage(self):
        self.sheet.write_text(SHEET.format(feat="- **War Caster:** advantage on concentration saves."), encoding="utf-8")
        self.hp(20, 0)                                  # temp HP only: 5 damage
        with self.mod._stats_lock:
            self.player()["hp"] = {"current": 40, "max": 40}
        self.hp(10)                                     # 30 damage → DC 15
        reqs = self.requests()
        self.assertEqual([(r["dc"], r["advantage"]) for r in reqs], [(10, "advantage"), (15, "advantage")])

    def test_a_failed_save_ends_the_spell(self):
        self.hp(12, 0)
        rid = self.requests()[-1]["request_id"]
        total = self.roll(rid, 3)                       # 3 + 4 = 7 < 10
        self.assertEqual(total, 7)
        self.assertIsNone(self.player()["concentration"])
        self.assertEqual(self.player()["effects"], [])
        self.assertEqual(self.notes[-1]["concentration_save"], "lost")
        self.assertEqual(self.sent[-1]["concentration_result"],
                         {"character": "Lyra", "spell": "Benedizione", "kept": False, "total": 7, "dc": 10})
        self.assertIn("broken", spellbook.format_cast(self.notes[-1]))

    def test_a_passed_save_keeps_it_and_a_roll_counts_once(self):
        self.hp(12, 0)
        rid = self.requests()[-1]["request_id"]
        self.assertEqual(self.roll(rid, 6), 10)         # exactly the DC: kept
        self.assertEqual(self.player()["concentration"], "Benedizione")
        self.assertEqual(self.notes[-1]["concentration_save"], "kept")
        self.assertIn("still concentrating", spellbook.format_cast(self.notes[-1]))
        before = len(self.notes)
        self.roll(rid, 1)                               # the same request again: nothing more
        self.assertEqual((len(self.notes), self.player()["concentration"]), (before, "Benedizione"))

    def test_at_zero_hp_the_concentration_ends_without_a_roll(self):
        self.hp(0, 0)
        self.assertEqual(self.requests(), [])
        self.assertIsNone(self.player()["concentration"])
        self.assertEqual(self.notes[-1]["concentration_save"], "down")
        self.assertIn("0 HP", spellbook.format_cast(self.notes[-1]))

    def test_no_concentration_or_healing_asks_nothing(self):
        self.hp(10, name="Ajeje")
        self.hp(20, 8)                                  # healing and more temporary HP
        self.assertEqual(self.requests(), [])
        self.assertEqual(self.notes, [])


class ConSaveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load("dnd_display_app_con_save", DISPLAY / "dnd-display-app.py")

    def test_the_modifier_comes_from_the_saves_else_from_constitution(self):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "s.md"
            saved = self.mod._find_sheet_file
            self.mod._find_sheet_file = lambda name: (str(path), name, "")
            try:
                path.write_text(SHEET.format(feat="- —"), encoding="utf-8")
                self.assertEqual(self.mod._con_save("Lyra"), (4, "normal"))
                path.write_text(SHEET.format(feat="- Mago da guerra").split("## Saving Throws")[0], encoding="utf-8")
                self.assertEqual(self.mod._con_save("Lyra"), (2, "normal"))
                path.write_text("# Nessuno\n\n- Mago da guerra\n", encoding="utf-8")
                self.assertEqual(self.mod._con_save("Lyra"), (0, "advantage"))
            finally:
                self.mod._find_sheet_file = saved


class TrackerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        (root / "campaigns" / "prova").mkdir(parents=True)
        old = os.environ.get("DND_CAMPAIGN_ROOT")
        os.environ["DND_CAMPAIGN_ROOT"] = str(root)
        self.addCleanup(lambda: os.environ.pop("DND_CAMPAIGN_ROOT", None) if old is None
                        else os.environ.__setitem__("DND_CAMPAIGN_ROOT", old))
        self.tr = _load("tracker_under_test", SKILL / "scripts" / "tracker.py")
        self.tr._send_announce = lambda msg: None

    def quiet(self, fn, *a, **k):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            res = fn(*a, **k)
        return res, out.getvalue()

    def test_a_monster_rolls_and_breaks_on_a_failure(self):
        self.assertEqual(self.tr.concentration_dc(9), 10)
        self.assertEqual(self.tr.concentration_dc(31), 15)
        self.quiet(self.tr.cmd_concentrate, "prova", "Isolda", "Blocca persone")
        res, out = self.quiet(self.tr.cmd_hit, "prova", "Isolda", 22, con=2, rng=random.Random(1))
        self.assertEqual(res["dc"], 11)
        self.assertIn("vs DC 11", out)
        state = self.tr._load("prova")
        conc = self.tr._entity(state, "Isolda").get("concentration")
        self.assertEqual(conc is None, not res["kept"])

    def test_down_ends_it_and_nobody_concentrating_rolls_nothing(self):
        self.quiet(self.tr.cmd_concentrate, "prova", "Isolda", "Blocca persone")
        res, out = self.quiet(self.tr.cmd_hit, "prova", "Isolda", 5, down=True)
        self.assertEqual((res["kept"], res["down"]), (False, True))
        self.assertIsNone(self.tr._entity(self.tr._load("prova"), "Isolda").get("concentration"))
        res, out = self.quiet(self.tr.cmd_hit, "prova", "Isolda", 5)
        self.assertEqual(res, {"concentrating": False})


if __name__ == "__main__":
    unittest.main()
