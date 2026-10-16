"""
test_traps.py — traps noticed, searched for, disarmed and sprung (SRD "Traps", DMG pp. 120-123).

scripts/trap.py keeps the campaign's traps in traps.json and does the rules:
passive Perception from the sheets against the DC to spot, checks with the
sheet's modifiers that move a trap to found, disarmed or sprung, and the
trap's attacks, saves and damage when it goes off.

Run from repo root:
    python -m pytest tests/test_traps.py -q
"""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"
sys.path.insert(0, str(SKILL / "scripts"))
sys.path.insert(0, str(DISPLAY))

import trap as tp  # noqa: E402

LYRA = """# Lyra Ombrafina
- **Race:** Elfa dei Boschi | **Class:** Ladro | **Level:** 2

## Ability Scores
| STR | DEX | CON | INT | WIS | CHA |
|-----|-----|-----|-----|-----|-----|
| 8 (-1) | 17 (+3) | 14 (+2) | 12 (+1) | 14 (+2) | 10 (+0) |

## Combat Stats
- **AC:** 14 (cuoio) | **Initiative:** +3
- **Passive Perception:** 14

## Saving Throws
| STR | DEX | CON | INT | WIS | CHA |
|-----|-----|-----|-----|-----|-----|
| -1 | +5* | +2 | +3* | +2 | +0 |

*\\* = proficient (proficiency bonus +2)*

## Skills
| Skill | Ability | Bonus | Proficient |
|-------|---------|-------|-----------|
| Arcana | INT | +1 | — |
| Investigation | INT | +3 | ✓ |
| Perception | WIS | +4 | ✓ |

**Tool proficiencies:** Arnesi da scasso (+7, Maestria), Set di dadi
"""

HERNIO = """# Hernio
- **Level:** 5

## Ability Scores
| STR | DEX | CON | INT | WIS | CHA |
|-----|-----|-----|-----|-----|-----|
| 16 (+3) | 12 (+1) | 14 (+2) | 10 (+0) | 8 (-1) | 10 (+0) |

## Combat Stats
- **AC:** 18

## Saving Throws
| STR | DEX | CON | INT | WIS | CHA |
|-----|-----|-----|-----|-----|-----|
| +6* | +1 | +5* | +0 | -1 | +0 |

## Skills
| Skill | Ability | Bonus | Proficient |
|-------|---------|-------|-----------|
| Perception | WIS | -1 | — |
"""


def fixed(*values):
    """A dice roller that returns these numbers in turn (whatever the die)."""
    it = iter(values)
    return lambda sides: next(it)


class RulesTests(unittest.TestCase):
    def test_damage_strings(self):
        self.assertEqual(tp.parse_damage("1 piercing + 2d10 poison"), [(0, 0, 1, "piercing"), (2, 10, 0, "poison")])
        self.assertEqual(tp.roll_damage("1 piercing + 2d10 poison", fixed(3, 9)), (13, "1 piercing · 2d10 poison: 3+9 = 12"))
        self.assertEqual(tp.dice_only("1 piercing + 2d10 poison"), "2d10 poison")
        for bad in ("due dadi", "99d6 fire", "2d1 cold"):
            with self.assertRaises(tp.TrapError):
                tp.parse_damage(bad)

    def test_the_srd_table_by_level(self):
        self.assertEqual(tp.rate(3)["dangerous"], {"dc": (12, 15), "attack": (6, 8), "damage": "2d10"})
        self.assertEqual(tp.rate(11)["deadly"]["damage"], "18d10")
        self.assertEqual(tp.rate(20)["setback"]["damage"], "10d10")
        with self.assertRaises(tp.TrapError):
            tp.rate(0)

    def test_presets_keep_the_srd_numbers_and_options_change_them(self):
        darts = tp.build("Dardi", "dardi")
        self.assertEqual((darts["spot"], darts["investigate"], darts["hit"], darts["save"]["dc"], darts["save"]["after_hit"]),
                         (15, 15, {"attack": 8, "shots": 4, "damage": "1d4 piercing"}, 15, True))
        needle = tp.build("Ago", "poison-needle")
        self.assertEqual((needle.get("spot"), needle["investigate"], needle["disarm"], needle["fail_springs"]), (None, 20, 15, True))
        roof = tp.build("Soffitto", "collapsing-roof", disarm=13)
        self.assertEqual((roof["disarm"], roof["save"]["damage"]), (13, "4d10 bludgeoning"))
        for key in tp.PRESETS:
            self.assertEqual(tp.build("x", key)["preset"], key)
        self.assertIn("\n", tp.describe(darts))

    def test_a_trap_of_ones_own(self):
        t = tp.build("Lastra", spot=12, disarm=13, fail_springs=True, save="des", dc=13, damage="2d10 bludgeoning",
                     half=True, condition="prone")
        self.assertEqual(t["save"], {"ability": "DEX", "dc": 13, "damage": "2d10 bludgeoning", "half": True, "condition": "prone"})
        pit = tp.build("Buca", spot=10, damage="2d6 bludgeoning")
        self.assertEqual(pit["auto"], {"damage": "2d6 bludgeoning"})
        for kw in ({"spot": 12}, {"damage": "2d6", "save": "DEX"}, {"damage": "2d6", "spot": 99},
                   {"damage": "2d6", "save": "fortuna", "dc": 10}):
            with self.assertRaises(tp.TrapError):
                tp.build("X", **kw)
        with self.assertRaises(tp.TrapError):
            tp.build("X", "trappola-inesistente")


class SheetTests(unittest.TestCase):
    def test_what_a_sheet_gives(self):
        n = tp.sheet_numbers(LYRA)
        self.assertEqual((n["passive"], n["ac"], n["pb"], n["tools"]), (14, 14, 2, 7))
        self.assertEqual((n["mods"]["DEX"], n["saves"]["DEX"], n["saves"]["STR"], n["skills"]["Investigation"]), (3, 5, -1, 3))
        h = tp.sheet_numbers(HERNIO)
        self.assertEqual((h["passive"], h["pb"], h["tools"], h["saves"]["CON"]), (9, 3, None, 5),
                         "no Passive line: 10 + Perception; the bonus from the level; no tools")

    def test_checks_take_the_right_modifier(self):
        n = tp.sheet_numbers(LYRA)
        darts, roof = tp.build("Dardi", "poison-darts"), tp.build("Soffitto", "collapsing-roof")
        self.assertEqual(tp.check_bonus(darts, "search", n), (4, "Wisdom (Perception)", 15, "normal"))
        self.assertEqual(tp.check_bonus(darts, "investigate", n)[:3], (3, "Intelligence (Investigation)", 15))
        self.assertEqual(tp.check_bonus(roof, "disarm", n), (7, "Dexterity with thieves' tools", 15, "normal"))
        self.assertEqual(tp.check_bonus(roof, "disarm", n, tools=False)[3], "disadvantage")
        h = tp.sheet_numbers(HERNIO)
        self.assertEqual(tp.check_bonus(roof, "disarm", h)[:2], (1, "Dexterity with thieves' tools (not proficient)"))
        with self.assertRaises(tp.TrapError):
            tp.check_bonus(darts, "disarm", n)                         # the darts have no disarm DC
        with self.assertRaises(tp.TrapError):
            tp.check_bonus(tp.build("Ago", "poison-needle"), "disarm", n, tools=False)
        glyph = tp.build("Glifo", magic=True, investigate=15, disarm=15, save="DEX", dc=15, damage="5d8 thunder", half=True)
        self.assertEqual(tp.check_bonus(glyph, "disarm", n, arcana=True)[:3], (1, "Intelligence (Arcana)", 15))


class PlayTests(unittest.TestCase):
    def test_passing_by(self):
        darts = tp.build("Dardi", "poison-darts")
        party = {"Lyra": {"passive": 15}, "Hernio": {"passive": 9}}
        self.assertEqual(tp.passive(darts, party), ([("Lyra", 15)], [("Hernio", 9)]))
        self.assertEqual(tp.passive(tp.build("Ago", "poison-needle"), party)[0], [], "no spot DC: nobody notices")

    def test_checks_move_the_trap_on(self):
        roof = tp.build("Soffitto", "collapsing-roof")
        self.assertIn("FOUND", "\n".join(tp.apply_check(roof, "search", "Lyra", 12, 10)))
        self.assertEqual((roof["status"], roof["found_by"]), ("found", ["Lyra"]))
        lines = tp.apply_check(roof, "disarm", "Lyra", 9, 15)
        self.assertEqual(roof["status"], "sprung")
        self.assertIn("IT GOES OFF", lines[-1])
        net = tp.build("Rete", "falling-net")
        net.pop("fail_springs")
        tp.apply_check(net, "disarm", "Lyra", 9, 15)
        self.assertEqual(net["status"], "armed")
        tp.apply_check(net, "disarm", "Lyra", 15, 15)
        self.assertEqual(net["status"], "disarmed")
        self.assertEqual(len(net["log"]), 2)

    def test_a_save_trap_halves_on_a_success(self):
        roof = tp.build("Soffitto", "collapsing-roof")
        saves = {"Lyra": 17, "Hernio": 4}
        lines = tp.spring(roof, [("Lyra", 14, 5, True), ("Hernio", 18, 1, True)], rng=fixed(10, 10, 10, 10, 1, 2, 3, 4),
                          save_roll=lambda name, bonus, dc, ab: saves[name])
        self.assertIn("  Lyra: DEX save 17 vs DC 15: success, 20 (4d10 bludgeoning: 10+10+10+10 = 40, halved) → 20 damage in all", lines)
        self.assertIn("  Hernio: DEX save 4 vs DC 15: FAILED, 10 (4d10 bludgeoning: 1+2+3+4 = 10) → 10 damage in all", lines)
        self.assertEqual(roof["status"], "sprung")

    def test_darts_attack_and_each_hit_brings_its_save(self):
        darts = tp.build("Dardi", "poison-darts")
        darts["hit"]["shots"] = 2
        # one target, so both darts go to it: d20 15 (hit), 1d4 → 2; d20 1 (miss); one save, failed: 2d10 → 4+5
        lines = tp.spring(darts, [("Lyra", 14, 2, True)], rng=fixed(15, 2, 1, 4, 5),
                          save_roll=lambda *a: 7)
        self.assertTrue(lines[1].startswith("  dart → Lyra: 23 (d20 15 +8) vs AC 14 — HIT, 2"))
        self.assertIn("miss", lines[2])
        self.assertIn("CON save 7 vs DC 15: FAILED, 9", lines[3])
        self.assertTrue(lines[3].endswith("→ 11 damage in all"))
        missed = tp.spring(tp.build("D", "poison-darts"), [("Hernio", 30, 0, True)], rng=lambda s: 2)
        self.assertTrue(all("miss" in l for l in missed[1:5]))
        self.assertIn("  Hernio: untouched", missed)

    def test_no_roll_damage_and_conditions(self):
        needle = tp.build("Ago", "poison-needle")
        lines = tp.spring(needle, [("Hernio", 18, 5, True)], rng=fixed(6, 6), save_roll=lambda *a: 3)
        self.assertIn("  Hernio: 13 (1 piercing · 2d10 poison: 6+6 = 12) · CON save 3 vs DC 15: FAILED, POISONED (1 HOUR) → 13 damage in all", lines)
        net = tp.build("Rete", "falling-net", )
        net["resets"] = True
        lines = tp.spring(net, [("Lyra", 14, -1, True)], save_roll=lambda *a: 15)
        self.assertIn("  Lyra: RESTRAINED · STR save 15 vs DC 10: success", lines)
        self.assertEqual(net["status"], "armed", "a trap that resets is armed again")


class CommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._old = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT",)}
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        camp = self.root / "campaigns" / "prova"
        (camp / "characters").mkdir(parents=True)
        (camp / "characters" / "lyra.md").write_text(LYRA, encoding="utf-8")
        (camp / "characters" / "hernio.md").write_text(HERNIO, encoding="utf-8")
        (camp / "state.md").write_text("## Session Flags\n- absent: Hernio\n", encoding="utf-8")
        self.camp = camp
        self.asked, self.told = [], []
        self._saved = (tp.ask_roll, tp.ask_rolls, tp.tell)
        tp.ask_roll = lambda *a, **k: self.asked.append(a) or 18
        tp.ask_rolls = lambda reqs, timeout=180: self.asked.append(reqs) or {r[0]: 20 for r in reqs}
        tp.tell = lambda who, text: self.told.append((who, text)) or True

    def tearDown(self):
        tp.ask_roll, tp.ask_rolls, tp.tell = self._saved
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def run_t(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = tp.main(["-c", "prova", *argv])
        return code, out.getvalue() + err.getvalue()

    def saved(self):
        return tp.load(str(self.camp / "traps.json"))

    def test_a_trap_from_setting_to_springing(self):
        code, out = self.run_t("add", "Dardi del corridoio", "--preset", "dardi", "--where", "Corridoio est", "--at", "7,3")
        self.assertEqual(code, 0, out)
        code, out = self.run_t("pass", "dardi", "--tell", "Noti dei forellini nel muro.")
        self.assertIn("missed:  Lyra Ombrafina (passive Perception 14 vs DC 15)", out)
        self.assertNotIn("Hernio", out, "the absent PC does not pass by")
        self.assertEqual(self.told, [])
        self.run_t("set", "dardi", "--spot", "14")
        code, out = self.run_t("pass", "dardi", "--tell", "Noti dei forellini nel muro.")
        self.assertIn("NOTICED: Lyra Ombrafina", out)
        self.assertEqual(self.told, [("Lyra Ombrafina", "Noti dei forellini nel muro.")])
        self.assertEqual((self.saved()[0]["status"], self.saved()[0]["noticed_by"]), ("noticed", ["Lyra Ombrafina"]))
        code, out = self.run_t("check", "dardi", "investigate", "lyra")
        self.assertEqual(self.asked[-1], ("Lyra Ombrafina", 3, "Intelligence (Investigation)", None, "normal"),
                         "a search on the phone names neither the trap nor its DC")
        self.assertIn("FOUND", out)
        code, out = self.run_t("spring", "dardi", "--targets", "Lyra, Goblin", "--ac", "Goblin:15", "--here")
        self.assertEqual(code, 0, out)
        self.assertIn("Dardi del corridoio goes off", out)
        self.assertEqual(self.saved()[0]["status"], "sprung")
        self.assertIn("Dardi del corridoio [sprung]", self.run_t("list", "--all")[1])
        self.assertEqual(self.run_t("list")[1].strip(), "no trap left armed")

    def test_a_disarm_asks_the_phone_with_the_dc_and_saves_are_asked_at_once(self):
        self.run_t("add", "Soffitto", "--preset", "collapsing-roof")
        code, out = self.run_t("check", "soffitto", "disarm", "Lyra")
        self.assertEqual(self.asked[-1], ("Lyra Ombrafina", 7, "Dexterity with thieves' tools — Soffitto", 15, "normal"))
        self.assertIn("DISARMED", out)
        self.run_t("add", "Statua", "--preset", "statua")
        code, out = self.run_t("spring", "statua", "--targets", "Lyra, Hernio")
        self.assertEqual(self.asked[-1], [("Lyra Ombrafina", 5, "DEX save — Statua", 13), ("Hernio", 1, "DEX save — Statua", 13)])
        self.assertIn("Lyra Ombrafina: DEX save 20 vs DC 13: success", out)

    def test_mistakes_change_nothing(self):
        self.run_t("add", "Dardi", "--preset", "dardi")
        before = self.saved()
        for argv in (("add", "Dardi", "--preset", "dardi"), ("add", "Nuova"), ("show", "nessuna"),
                     ("check", "dardi", "disarm", "Lyra", "--roll", "20"), ("check", "dardi", "search", "Mira", "--roll", "20"),
                     ("set", "dardi"), ("spring", "dardi", "--targets", "Lyra", "--ac", "Lyra")):
            code, out = self.run_t(*argv)
            self.assertEqual(code, 1, argv)
            self.assertIn("Nothing was changed", out)
        self.assertEqual(self.saved(), before)

    def test_it_runs_as_a_script(self):
        env = dict(os.environ, PYTHONUTF8="1")
        r = subprocess.run([sys.executable, str(SKILL / "scripts" / "trap.py"), "-c", "prova", "list"],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "no trap left armed"), r.stderr[-400:])
        r = subprocess.run([sys.executable, str(SKILL / "scripts" / "trap.py"), "presets"],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertIn("poison-darts (Dardi avvelenati)", r.stdout)


class ScreenTests(unittest.TestCase):
    def test_the_dms_screen_lists_the_traps(self):
        spec = importlib.util.spec_from_file_location("dnd_display_app_traps", str(DISPLAY / "dnd-display-app.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod._token_ok = lambda: True
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        camp = pathlib.Path(tmp.name)
        trap = tp.build("Dardi", "poison-darts", where="Corridoio")
        trap["found_by"] = ["Lyra"]
        tp.save(str(camp / "traps.json"), [trap])
        mod._active_campaign, mod._find_campaign = (lambda: "prova"), (lambda n: camp)
        got = mod.app.test_client().get("/dm-screen/data").get_json()["traps"]
        self.assertEqual((got[0]["name"], got[0]["status"], got[0]["spot"], got[0]["found_by"], got[0]["where"]),
                         ("Dardi", "armed", 15, ["Lyra"], "Corridoio"))
        self.assertNotIn("note", got[0])
        html = (DISPLAY / "templates" / "dm.html").read_text(encoding="utf-8")
        self.assertIn('id="s-traps"', html)
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ["dms.traps"] + [f"dms.trap.{s}" for s in tp.STATUSES]:
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
