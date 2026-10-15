"""
test_attacks.py — an attack rolled in one tap from the player's screen.

scripts/attacks.py reads the sheet's Attacks table and rolls to hit and for
damage together (a natural 20 doubles the damage dice). The display puts the
line in the story and stages it as the player's action, already rolled.

Run from repo root:
    python -m pytest tests/test_attacks.py -q
"""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"
SCRIPTS = SKILL / "scripts"
HTML = DISPLAY / "templates" / "index.html"
NODE = shutil.which("node")
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(SCRIPTS))

import attacks  # noqa: E402

SHEET = """# Lyra Ombrafina

## Attacks
| Name | Attack Bonus | Damage | Type | Notes |
|------|-------------|--------|------|-------|
| Stocco | +5 | 1d8+3 | perforante | Arma accurata; Attacco furtivo +1d6 se le condizioni sono soddisfatte |
| Martello da guerra | +6 | 1d8+4 | contundente | Versatile: 1d10+4 a due mani (con ritiro di 1 e 2 sui dadi del danno). |
| Grido | — | — | psichico | Solo un urlo |
| Dardo di fuoco | +5 | 1d10 | fuoco | Trucchetto |

## Spell Slots (if applicable)
"""


class FixedDice:
    """randbelow(n) gives back the next value of a list, minus one."""
    def __init__(self, *values):
        self.values = list(values)

    def randbelow(self, n):
        return self.values.pop(0) - 1


class SheetTests(unittest.TestCase):
    def test_the_table_and_what_the_notes_offer(self):
        rows = attacks.read_attacks(SHEET)
        self.assertEqual([r["name"] for r in rows], ["Stocco", "Martello da guerra", "Grido", "Dardo di fuoco"])
        stocco, martello, grido, dardo = rows
        self.assertEqual((stocco["bonus"], stocco["damage"]["text"], stocco["type"]), (5, "1d8+3", "perforante"))
        self.assertEqual(stocco["extras"], [{"label": "Attacco furtivo", "dice": {"count": 1, "sides": 6, "bonus": 0, "text": "1d6"}}])
        self.assertEqual([(a["label"], a["dice"]["text"]) for a in martello["alts"]], [("Versatile", "1d10+4")])
        self.assertEqual((grido["bonus"], grido["damage"]), (None, None))
        self.assertEqual(dardo["damage"]["text"], "1d10")
        self.assertEqual(attacks.read_attacks("# Nessuno\n"), [])

    def test_an_attack_is_named_by_any_part(self):
        rows = attacks.read_attacks(SHEET)
        self.assertEqual(attacks.find(rows, "martello")["name"], "Martello da guerra")
        with self.assertRaisesRegex(attacks.AttackError, "no attack"):
            attacks.find(rows, "arco")

    def test_to_hit_and_damage_in_one_roll(self):
        stocco = attacks.find(attacks.read_attacks(SHEET), "stocco")
        r = attacks.roll(stocco, rng=FixedDice(12, 5))
        self.assertEqual((r["natural"], r["to_hit"], r["dice"], r["damage"], r["crit"]), (12, 17, [5], 8, False))
        r = attacks.roll(stocco, "advantage", extra=True, rng=FixedDice(3, 20, 8, 1, 6, 2))
        self.assertEqual((r["d20"], r["natural"], r["crit"]), ([3, 20], 20, True))
        self.assertEqual((r["dice"], r["extra_dice"], r["damage"], r["damage_text"]), ([8, 1], [6, 2], 20, "2d8+3 +2d6"))
        r = attacks.roll(stocco, "disadvantage", rng=FixedDice(15, 1))
        self.assertEqual((r["natural"], r["fumble"], r["damage"], r["dice"]), (1, True, None, []))
        self.assertNotIn("danni", attacks.story_line("Lyra", "", r, "it"))
        self.assertNotIn("danni", attacks.action_line("", r, "it"))
        martello = attacks.find(attacks.read_attacks(SHEET), "martello")
        r = attacks.roll(martello, alt=1, rng=FixedDice(10, 10))
        self.assertEqual((r["damage"], r["alt_label"]), (14, "Versatile"))
        with self.assertRaises(attacks.AttackError):
            attacks.roll(attacks.find(attacks.read_attacks(SHEET), "grido"))

    def test_the_lines_for_the_table_and_for_the_dm(self):
        stocco = attacks.find(attacks.read_attacks(SHEET), "stocco")
        r = attacks.roll(stocco, rng=FixedDice(14, 3))
        self.assertEqual(attacks.story_line("Lyra", "Goblin 1", r, "it"),
                         "⚔ Lyra → Goblin 1 · Stocco: 19 per colpire (d20 14 +5) · 6 danni (perforante, 1d8+3: 3)")
        self.assertEqual(attacks.action_line("Goblin 1", r, "it"),
                         "⚔ Stocco → Goblin 1: 19 per colpire (d20 14 +5) · 6 danni (perforante, 1d8+3: 3)")
        crit = attacks.action_line("", attacks.roll(stocco, rng=FixedDice(20, 3, 3)))
        self.assertEqual(crit, "⚔ Stocco: 25 to hit (d20 20 +5) — critical! · 9 damage (perforante, 2d8+3: 3, 3)")
        martello = attacks.find(attacks.read_attacks(SHEET), "martello")
        self.assertTrue(attacks.action_line("", attacks.roll(martello, alt=1, rng=FixedDice(9, 2))).startswith("⚔ Martello da guerra (Versatile):"))


class CommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._old = os.environ.get("DND_CAMPAIGN_ROOT")
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        chars = self.root / "campaigns" / "prova" / "characters"
        chars.mkdir(parents=True)
        (chars / "lyra-ombrafina.md").write_text(SHEET, encoding="utf-8")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("DND_CAMPAIGN_ROOT", None)
        else:
            os.environ["DND_CAMPAIGN_ROOT"] = self._old
        self._tmp.cleanup()

    def test_list_and_roll(self):
        env = dict(os.environ, PYTHONUTF8="1")
        r = subprocess.run([sys.executable, str(SCRIPTS / "attacks.py"), "-c", "prova", "list", "lyra"],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn("Stocco: +5, 1d8+3 perforante | +1d6 Attacco furtivo", r.stdout)
        self.assertIn("Martello da guerra: +6, 1d8+4 contundente | or 1d10+4 (Versatile)", r.stdout)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = attacks.main(["-c", "prova", "roll", "lyra", "stocco", "--target", "Drogo", "--extra"])
        self.assertEqual(code, 0)
        self.assertRegex(out.getvalue(), r"⚔ Lyra Ombrafina → Drogo · Stocco: \d+ to hit")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(attacks.main(["-c", "prova", "roll", "lyra", "arco"]), 1)
        self.assertIn("no attack called", err.getvalue())


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_attacks", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sheet = pathlib.Path(tmp.name) / "lyra.md"
        sheet.write_text(SHEET, encoding="utf-8")
        m = self.mod
        saved = (m._find_sheet_file, m._broadcast, m._persist_log, m._persist_tail, m._check_auto_trigger)
        self.addCleanup(lambda: setattr(m, "_find_sheet_file", saved[0]) or setattr(m, "_broadcast", saved[1])
                        or setattr(m, "_persist_log", saved[2]) or setattr(m, "_persist_tail", saved[3])
                        or setattr(m, "_check_auto_trigger", saved[4]))
        m._find_sheet_file = lambda name: (str(sheet) if m._sheet_key(name) == "lyra" else None, name, "prova")
        self.sent = []
        m._broadcast = self.sent.append
        m._persist_log = m._persist_tail = lambda: None
        self.triggered = []
        m._check_auto_trigger = lambda: self.triggered.append(True)
        with m._stats_lock:
            self._stats = dict(m._current_stats)
            m._current_stats.clear()
            m._current_stats.update({"players": [{"name": "Lyra"}, {"name": "Ajeje"}],
                                     "turn_order": {"order": ["Goblin 1", "Lyra", {"name": "Drogo"}, "Ajeje"], "current": "Lyra"}})
        with m._staged_lock:
            m._staged.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        with self.mod._stats_lock:
            self.mod._current_stats.clear()
            self.mod._current_stats.update(self._stats)
        with self.mod._staged_lock:
            self.mod._staged.clear()

    def test_the_panel_has_the_weapons_and_the_enemies(self):
        self.assertEqual(self.client.get("/attacks").get_json(), [{"character": "Lyra", "attacks": 4}])
        d = self.client.get("/attacks/Lyra").get_json()
        self.assertEqual((d["character"], d["targets"], len(d["attacks"])), ("Lyra", ["Goblin 1", "Drogo"], 4))

    def test_an_attack_goes_in_the_story_and_becomes_the_action(self):
        with self.mod._staged_lock:
            self.mod._staged["Lyra"] = {"text": "mi lancio sul goblin", "ready": False, "timestamp": 0}
        r = self.client.post("/attacks/Lyra/roll", json={"attack": "stocco", "target": "Goblin 1", "advantage": "advantage", "lang": "it"},
                             headers={"X-DND-Device": "tel-lyra"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        body = r.get_json()
        self.assertEqual((body["result"]["attack"], len(body["result"]["d20"])), ("Stocco", 2))
        self.assertTrue(body["line"].startswith("⚔ Lyra → Goblin 1 · Stocco:"))
        self.assertIn({"text": body["line"], "dice": True}, self.sent)
        staged = self.mod._staged["Lyra"]
        self.assertTrue(staged["ready"])
        self.assertTrue(staged["text"].startswith("mi lancio sul goblin — ⚔ Stocco → Goblin 1: "))
        self.assertIn(" per colpire (d20 ", staged["text"])
        self.assertEqual(self.triggered, [True])
        self.assertEqual(self.mod._text_log[-1]["text"], body["line"])

    def test_what_cannot_be_rolled_is_refused(self):
        r = self.client.post("/attacks/Lyra/roll", json={"attack": "grido"}, headers={"X-DND-Device": "tel"})
        self.assertEqual((r.status_code, r.get_json()["code"]), (409, "attack"))
        self.assertEqual(self.client.post("/attacks/Lyra/roll", json={"attack": "stocco"}).status_code, 403)   # no device id
        self.assertEqual(self.mod._staged, {})

    def test_a_player_attacks_only_as_themselves(self):
        src = (DISPLAY / "dnd-display-app.py").read_text(encoding="utf-8")
        body = src[src.index("def _attack_sheet("):]
        self.assertIn("_is_other_character(character)", body[:body.index("\n\n\n")])
        for route in ("attacks_get", "attacks_roll"):
            fn = src[src.index(f"def {route}("):]
            self.assertIn("_attack_sheet(character)", fn[:fn.index("\n\n\n")])
            self.assertNotIn(route, self.mod._DM_ENDPOINTS)


_SLICE = re.compile(r"(function _attackChoices\(attack\).*?)\n// ── end attack choices ──", re.S)


class PageTests(unittest.TestCase):
    def test_the_choices_of_an_attack(self):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the attack block is no longer where this test slices it")
        rows = attacks.read_attacks(SHEET)
        script = m.group(1) + "\nconsole.log(JSON.stringify(%s.map(_attackChoices).concat([_attackChoices(null)])));\n" % json.dumps(rows)
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            stocco, martello, grido, dardo, blank = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        self.assertEqual((stocco["bonusText"], stocco["extra"], stocco["canRoll"]), ("+5", {"label": "Attacco furtivo", "text": "1d6"}, True))
        self.assertEqual(martello["damages"], [{"alt": 0, "label": "", "text": "1d8+4"}, {"alt": 1, "label": "Versatile", "text": "1d10+4"}])
        self.assertEqual((grido["canRoll"], grido["damages"]), (False, []))
        self.assertEqual((dardo["extra"], blank["canRoll"]), (None, False))

    def test_the_page_has_the_panel_and_every_way_in(self):
        src = HTML.read_text(encoding="utf-8")
        for needle in ('id="atk-modal"', 'id="ln-attack"', 'data-ln-proxy="ln-attack"', 'id="ph-attack-opt"',
                       "window._onAttack(payload.attack)", 'id="ln-i-sword"', "'X-DND-Device': _deviceId"):
            self.assertIn(needle, src)

    def test_strings_exist_in_every_language(self):
        keys = ["atk.title", "atk.titleOf", "atk.target", "atk.targetPh", "atk.weapon", "atk.normal", "atk.adv", "atk.dis",
                "atk.go", "atk.toHit", "atk.damage", "atk.crit", "atk.fumble", "atk.sent", "atk.empty", "atk.failed",
                "atk.pending", "atk.toast", "atk.toastNoTarget"]
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
