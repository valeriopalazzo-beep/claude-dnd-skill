"""
test_ammo.py — arrows and bolts counted on the sheet (PHB 2014 p. 146).

A weapon with the Ammunition property spends one piece with every attack;
after the fight half of what was spent is found again. scripts/ammo.py reads
the count however the sheet writes it, the Attacks panel spends it by itself
and refuses when nothing is left, and tracker.py clear gives half of it back.

Run from repo root:
    python -m pytest tests/test_ammo.py -q
"""
import contextlib
import importlib.util
import io
import os
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(SCRIPTS))

import ammo  # noqa: E402
import attacks  # noqa: E402

SHEET = """# Lyra Ombrafina

## Attacks
| Name | Attack Bonus | Damage | Type | Notes |
|------|-------------|--------|------|-------|
| Stocco | +5 | 1d8+3 | perforante | Arma accurata |
| Arco corto | +5 | 1d6+3 | perforante | Munizioni (24/96), due mani |

## Equipment & Inventory
**Weapons:**
- Stocco
- Arco corto con faretra e 3 frecce

**Adventuring Gear:**
- Frecce +1 ×2
- Corda di canapa (15 m)

**Currency:** 4 gp
"""

GEAR = """# Prova

## Equipment & Inventory
**Weapons:**
- Balestra leggera + 20 quadrelli

**Adventuring Gear:**
- Faretra con 20 frecce
- Frecce ×3
- Proiettili da fionda (10)
- Aghi: 5
- 20 quadrelli
- Faretra con frecce

**Currency:** 10 gp
"""


class CountTests(unittest.TestCase):
    def test_every_way_a_sheet_writes_it(self):
        self.assertEqual([(s["count"], s["alone"]) for s in ammo.stacks(GEAR, "arrow")], [(20, False), (3, True), (None, False)])
        self.assertEqual([(s["count"], s["alone"]) for s in ammo.stacks(GEAR, "bolt")], [(20, False), (20, True)])
        self.assertEqual((ammo.count(GEAR, "arrow"), ammo.count(GEAR, "bullet"), ammo.count(GEAR, "needle")), (23, 10, 5))
        self.assertIsNone(ammo.count("# Nessuno\n\n## Equipment & Inventory\n- Spada\n", "arrow"))
        self.assertEqual(ammo.stacks("# Senza equipaggiamento\n\n## Attacks\n", "arrow"), [])
        no_gear = "# X\n\n## Attacks\n| Name | Attack Bonus | Damage | Type | Notes |\n|--|--|--|--|--|\n| Arco lungo | +6 | 1d8 | p | |\n"
        self.assertEqual(attacks.read_attacks(no_gear)[0]["ammo"], {"kind": "arrow", "left": None})

    def test_the_weapon_says_what_it_shoots(self):
        for name, kind in (("Arco corto", "arrow"), ("Arco lungo", "arrow"), ("Longbow", "arrow"), ("Balestra pesante", "bolt"),
                           ("Light crossbow", "bolt"), ("Fionda", "bullet"), ("Cerbottana", "needle"),
                           ("Spada lunga", ""), ("Arcobaleno di lame", "")):
            with self.subTest(name=name):
                self.assertEqual(ammo.weapon_kind(name), kind)

    def test_only_the_number_changes(self):
        text, left = ammo.spend(GEAR, "arrow", 4)
        self.assertEqual(left, 19)
        self.assertIn("- Faretra con 16 frecce\n", text)
        self.assertIn("- Frecce ×3\n", text)
        text, left = ammo.spend(GEAR, "arrow", 21)       # the quiver empties, then the loose ones
        self.assertEqual(left, 2)
        self.assertIn("- Faretra con 0 frecce\n", text)
        self.assertIn("- Frecce ×2\n", text)
        text, _ = ammo.spend(GEAR, "bullet", 10)         # a line that is only the ammunition goes
        self.assertNotIn("Proiettili", text)
        with self.assertRaisesRegex(ammo.AmmoError, "only 5 needles"):
            ammo.spend(GEAR, "needle", 6)

    def test_magic_arrows_are_shot_last_and_given_back_to_the_plain_ones(self):
        text, left = ammo.spend(SHEET, "arrow", 4)
        self.assertEqual(left, 1)
        self.assertIn("- Arco corto con faretra e 0 frecce\n", text)
        self.assertIn("- Frecce +1 ×1\n", text)
        self.assertIn("- Arco corto con faretra e 2 frecce\n", ammo.give_back(text, "arrow", 2))
        bare = "# X\n\n## Equipment & Inventory\n**Adventuring Gear:**\n- Corda\n\n**Currency:** 1 gp\n"
        self.assertIn("- Frecce ×3\n", ammo.give_back(bare, "arrow", 3))


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._old = os.environ.get("DND_CAMPAIGN_ROOT")
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        chars = self.root / "campaigns" / "prova" / "characters"
        chars.mkdir(parents=True)
        self.sheet = chars / "lyra-ombrafina.md"
        self.sheet.write_text(SHEET, encoding="utf-8")
        saved = ammo._refresh                      # the load goes to a display that is not running: skip it
        ammo._refresh = lambda path, campaign: None
        self.addCleanup(setattr, ammo, "_refresh", saved)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("DND_CAMPAIGN_ROOT", None)
        else:
            os.environ["DND_CAMPAIGN_ROOT"] = self._old
        self._tmp.cleanup()

    def test_a_shot_spends_one_and_the_line_says_what_is_left(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(attacks.main(["-c", "prova", "roll", "lyra", "arco"]), 0)
        self.assertRegex(out.getvalue(), r"· 4 arrows left\n$")       # the two magic ones count too
        self.assertIn("faretra e 2 frecce", self.sheet.read_text(encoding="utf-8"))
        self.assertEqual(ammo.load("prova")["spent"], {"Lyra Ombrafina": {"arrow": 1}})
        row = attacks.find(attacks.read_attacks(self.sheet.read_text(encoding="utf-8")), "arco")
        self.assertEqual(row["ammo"], {"kind": "arrow", "left": 4})                # the magic ones count too
        self.assertIsNone(attacks.find(attacks.read_attacks(SHEET), "stocco")["ammo"])

    def test_nothing_left_nothing_to_shoot(self):
        row = attacks.find(attacks.read_attacks(SHEET), "arco")
        with self.assertRaisesRegex(attacks.AttackError, "no arrows left"):
            attacks.roll(dict(row, ammo={"kind": "arrow", "left": 0}))
        unknown = dict(row, ammo={"kind": "arrow", "left": None})              # no number written: shoot, spend nothing
        r = attacks.roll(unknown)
        attacks.spend_ammo(unknown, r, self.sheet, "prova")
        self.assertNotIn("ammo_left", r)
        self.assertEqual(self.sheet.read_text(encoding="utf-8"), SHEET)

    def test_half_comes_back_when_the_fight_is_cleared(self):
        for _ in range(3):
            ammo.op_spend("lyra", "arrow", 1, "prova")
        self.assertIn("faretra e 0 frecce", self.sheet.read_text(encoding="utf-8"))
        import tracker
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            tracker.cmd_clear("prova")
        self.assertIn("🏹 Lyra Ombrafina finds again 1 of 3 arrows", out.getvalue())
        self.assertIn("faretra e 1 frecce", self.sheet.read_text(encoding="utf-8"))
        self.assertEqual(ammo.load("prova")["spent"], {})
        ammo.op_spend("lyra", "arrow", 1, "prova")
        self.assertEqual(ammo.recover("prova", none=True), ["Lyra Ombrafina finds again 0 of 1 arrows"])

    def test_the_command_line(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(ammo.main(["-c", "prova", "show", "lyra"]), 0)
            self.assertEqual(ammo.main(["-c", "prova", "spend", "lyra", "--n", "2"]), 0)
            self.assertEqual(ammo.main(["-c", "prova", "spent"]), 0)
        self.assertIn("arrow: 3  — Arco corto con faretra e 3 frecce", out.getvalue())
        self.assertIn("Lyra Ombrafina: 3 arrows left", out.getvalue())
        self.assertIn("Lyra Ombrafina: 2 arrows", out.getvalue())
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(ammo.main(["-c", "prova", "spend", "lyra", "--kind", "bolt"]), 1)
        self.assertIn("no bolts on the sheet", err.getvalue())


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_ammo", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.sheet = pathlib.Path(tmp.name) / "lyra.md"
        self.sheet.write_text(SHEET.replace("3 frecce", "1 frecce").replace("- Frecce +1 ×2\n", ""), encoding="utf-8")
        saved_refresh = ammo._refresh
        ammo._refresh = lambda path, campaign: None
        self.addCleanup(setattr, ammo, "_refresh", saved_refresh)
        m = self.mod
        saved = (m._find_sheet_file, m._broadcast, m._persist_log, m._persist_tail, m._check_auto_trigger)
        self.addCleanup(lambda: setattr(m, "_find_sheet_file", saved[0]) or setattr(m, "_broadcast", saved[1])
                        or setattr(m, "_persist_log", saved[2]) or setattr(m, "_persist_tail", saved[3])
                        or setattr(m, "_check_auto_trigger", saved[4]))
        m._find_sheet_file = lambda name: (str(self.sheet), name, "")
        m._broadcast = lambda payload: None
        m._persist_log = m._persist_tail = lambda: None
        m._check_auto_trigger = lambda: None
        with m._stats_lock:
            self._stats = dict(m._current_stats)
            m._current_stats.clear()
            m._current_stats.update({"players": [{"name": "Lyra"}]})
        self.addCleanup(self._restore)

    def _restore(self):
        with self.mod._stats_lock:
            self.mod._current_stats.clear()
            self.mod._current_stats.update(self._stats)
        with self.mod._staged_lock:
            self.mod._staged.clear()

    def test_the_last_arrow_and_then_none(self):
        d = self.client.get("/attacks/Lyra").get_json()
        self.assertEqual(attacks.find(d["attacks"], "arco")["ammo"], {"kind": "arrow", "left": 1})
        r = self.client.post("/attacks/Lyra/roll", json={"attack": "arco", "lang": "it"}, headers={"X-DND-Device": "tel"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["result"]["ammo_left"], 0)
        self.assertTrue(r.get_json()["line"].endswith(" · frecce rimaste: 0"))
        self.assertIn("faretra e 0 frecce", self.sheet.read_text(encoding="utf-8"))
        r = self.client.post("/attacks/Lyra/roll", json={"attack": "arco"}, headers={"X-DND-Device": "tel"})
        self.assertEqual((r.status_code, r.get_json()["code"]), (409, "ammo"))


class PageTests(unittest.TestCase):
    def test_strings_exist_in_every_language(self):
        import json
        keys = [f"atk.ammo.{k}" for k in ammo.KINDS] + [f"atk.ammoOut.{k}" for k in ammo.KINDS] + ["atk.noAmmo"]
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))
        html = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("d.code === 'ammo'", html)
        self.assertIn("atk-ammo", html)


if __name__ == "__main__":
    unittest.main()
