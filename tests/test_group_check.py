"""
test_group_check.py — group checks and the Help action (PHB 2014 pp. 175, 192).

group_check.py asks each phone for the same check with its own bonus from
the sheet (disadvantage from exhaustion or noisy armour), and the group
succeeds when at least half succeed. On any roll the DM asks of a teammate,
another player's phone offers "Help": the server gives that roll advantage,
and a helper asked for the same check gives up their own roll.

Run from repo root:
    python -m pytest tests/test_group_check.py -q
"""
import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"
HTML = DISPLAY / "templates" / "index.html"
NODE = shutil.which("node")
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(SKILL / "scripts"))

import group_check as gc  # noqa: E402

HERNIO = """# Hernio
| STR | DEX | CON | INT | WIS | CHA |
|-----|-----|-----|-----|-----|-----|
| 8 (-1) | 17 (+3) | 13 (+1) | 10 (+0) | 12 (+1) | 15 (+2) |
- **AC:** 14 (armatura di cuoio 11 + DES 3) | **Speed:** 25 ft
- **Conditions:** Indebolimento 1 — svantaggio alle prove di caratteristica
| Stealth | DEX | +5 | ✓ |
| Survival | WIS | +1 | |
"""
AJEJE = """# Ajeje
| FOR | DES | COS | INT | SAG | CAR |
|-----|-----|-----|-----|-----|-----|
| 16 (+3) | 14 (+2) | 14 (+2) | 8 (-1) | 10 (+0) | 12 (+1) |
- **AC:** 16 (cotta di maglia 16 (nessun bonus di DES; svantaggio alle prove di Furtività))
- **Conditions:** none
| Athletics | STR | +5 | ✓ |
"""


class RuleTests(unittest.TestCase):
    def test_what_is_checked(self):
        self.assertEqual(gc.what_check("Furtività"), ("Stealth", "DEX"))
        self.assertEqual(gc.what_check("sleight of hand"), ("Sleight of Hand", "DEX"))
        self.assertEqual(gc.what_check("Forza"), ("", "STR"))
        self.assertEqual(gc.what_check("SAG"), ("", "WIS"))
        with self.assertRaises(gc.GroupCheckError):
            gc.what_check("Cucina")

    def test_bonuses_from_the_sheet(self):
        self.assertEqual(gc.bonus(HERNIO, "Stealth", "DEX"), 5)
        self.assertEqual(gc.bonus(HERNIO, "Athletics", "STR"), -1, "no row: the ability")
        self.assertEqual(gc.bonus(AJEJE, "Athletics", "STR"), 5)
        self.assertEqual(gc.bonus(AJEJE, "", "STR"), 3, "Italian ability headers")
        self.assertEqual(gc.bonus(AJEJE, "Stealth", "DEX"), 2)

    def test_disadvantage_from_the_sheet(self):
        self.assertEqual(gc.disadvantage(HERNIO, "Survival"), "exhaustion")
        self.assertEqual(gc.disadvantage(AJEJE, "Stealth"), "armour")
        self.assertEqual(gc.disadvantage(AJEJE, "Athletics"), "")

    def test_half_or_more(self):
        self.assertTrue(gc.outcome({"A": 15, "B": 9, "C": 14, "D": 3}, 13)["success"])
        self.assertFalse(gc.outcome({"A": 15, "B": 9, "C": 12}, 13)["success"])
        out = gc.outcome({"A": 15, "B": None, "C": 2}, 13)
        self.assertEqual((out["success"], out["missing"], out["need"]), (True, ["B"], 1))
        self.assertFalse(gc.outcome({"A": None}, 13)["success"])
        self.assertEqual(gc.story_line("Furtività", 13, gc.outcome({"A": 15, "B": 9}, 13), "it"),
                         "🎲 Prova di gruppo — Furtività (CD 13): 1 su 2 ci riesce — RIUSCITA")


class HelpServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_help", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.mod._rate_ok = lambda ip: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        m = self.mod
        saved = (m._broadcast, m._persist_log, m._persist_tail, m._active_campaign)
        def restore():
            (m._broadcast, m._persist_log, m._persist_tail, m._active_campaign) = saved
        self.addCleanup(restore)
        self.sent = []
        m._broadcast = self.sent.append
        m._persist_log = m._persist_tail = lambda: None
        m._active_campaign = lambda: ""
        with m._dice_pending_lock:
            m._dice_pending.clear()
        with m._stats_lock:
            self._stats = dict(m._current_stats)
            m._current_stats.clear()
            m._current_stats.update({"players": [{"name": "Lyra"}, {"name": "Hernio"}, {"name": "Ajeje"}]})
        self.addCleanup(self._restore_stats)

    def _restore_stats(self):
        with self.mod._stats_lock:
            self.mod._current_stats.clear()
            self.mod._current_stats.update(self._stats)

    def ask(self, who, adv="normal", label="Furtività"):
        r = self.client.post("/dice-request", json={"characters": [who], "spec": "1d20", "modifier": 3,
                                                    "advantage": adv, "label": label, "dc": 13})
        return r.get_json()["request_id"]

    def help(self, rid, helper, target):
        return self.client.post(f"/dice-request/{rid}/help", json={"character": helper, "target": target})

    def roll(self, rid, who, adv="normal"):
        return self.client.post("/player-input/dice", json={"character": who, "spec": "1d20", "modifier": 3,
                                                           "advantage": adv, "request_id": rid}).get_json()

    def test_help_gives_advantage_and_the_dm_sees_it(self):
        rid = self.ask("Hernio")
        r = self.help(rid, "lyra", "hernio")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json(), {"target": "Hernio", "helper": "Lyra", "advantage": "advantage"})
        again = next(m["dice_request"] for m in self.sent if "dice_request" in m and m["dice_request"]["request_id"] == rid
                     and m["dice_request"]["characters"] == ["Hernio"] and m["dice_request"]["advantage"] == "advantage")
        self.assertIn("🤝 Lyra", again["label"])
        self.assertEqual(self.mod._dice_pending_snapshot()[0]["helped"], ["hernio"])
        out = self.roll(rid, "Hernio", "normal")                  # the phone may not know yet: the server knows
        self.assertEqual(out["advantage"], "advantage")
        self.assertEqual(len(out["both"]), 2)
        res = self.client.get(f"/dice-request/{rid}").get_json()["results"][0]
        self.assertEqual(res["helped_by"], "Lyra")
        import send
        self.assertIn("helped by Lyra", send._format_roll_result(res, 13, "Furtività"))

    def test_help_cancels_disadvantage_and_cannot_be_doubled(self):
        rid = self.ask("Ajeje", "disadvantage")
        self.assertEqual(self.help(rid, "Hernio", "Ajeje").get_json()["advantage"], "normal")
        self.assertEqual(self.help(rid, "Lyra", "Ajeje").get_json()["code"], "taken")
        self.assertEqual(self.help(rid, "Ajeje", "Ajeje").get_json()["code"], "self")
        rid2 = self.ask("Lyra")
        self.assertEqual(self.help(rid2, "Lyra", "Lyra").get_json()["code"], "self")
        self.assertEqual(self.help(rid2, "Gandalf", "Lyra").status_code, 403)
        self.assertEqual(self.help("nope", "Hernio", "Lyra").get_json()["code"], "gone")
        self.assertEqual(self.roll(rid, "Ajeje", "disadvantage")["advantage"], "normal")

    def test_in_a_group_check_the_helper_gives_up_their_own_roll(self):
        lyra, hernio = self.ask("Lyra", label="Prova di gruppo: Furtività"), self.ask("Hernio", label="Prova di gruppo: Furtività")
        other = self.ask("Lyra", label="Percezione")
        self.help(hernio, "Lyra", "Hernio")
        st = self.client.get(f"/dice-request/{lyra}").get_json()
        self.assertEqual((st["complete"], st["helping"]), (True, ["Lyra"]))
        self.assertIn({"dice_request_cancelled": lyra}, self.sent)
        self.assertFalse(self.client.get(f"/dice-request/{other}").get_json()["complete"], "another check is still hers")
        self.assertIn("dice_request_help", self.mod.app.view_functions)
        self.assertNotIn("dice_request_help", self.mod._DM_ENDPOINTS)


_SLICE = re.compile(r"(function _helpOffers\(snapshot, me\).*?)\n// ── end help offers ──", re.S)


class PageTests(unittest.TestCase):
    def test_what_a_phone_offers(self):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the help block is no longer where this test slices it")
        snap = [{"request_id": "a", "pending": ["Hernio", "Lyra"], "label": "Furtività", "helped": ["hernio"]},
                {"request_id": "b", "pending": ["Ajeje"], "label": "", "helped": []}]
        script = m.group(1) + "\nconsole.log(JSON.stringify([_helpOffers(%s, 'lyra'), _helpOffers(%s, ''), _helpOffers(null, 'x')]));\n" % (
            json.dumps(snap), json.dumps(snap))
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            lyra, nobody, none = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        self.assertEqual(lyra, [{"request_id": "b", "target": "Ajeje", "label": ""}])
        self.assertEqual((nobody, none), ([], []))

    def test_the_page_and_the_strings(self):
        src = HTML.read_text(encoding="utf-8")
        self.assertIn('id="help-bar"', src)
        self.assertIn("window._onHelpOffers(payload.dice_pending)", src)
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("help.bar", "help.offer", "help.done", "help.failed"):
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
