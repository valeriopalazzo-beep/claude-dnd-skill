"""
test_rest.py — short and long rests from the players' own screens.

scripts/rest.py reads HP, Hit Dice and CON from a character sheet and writes
them back: a Hit Die spent on a short rest, everything back on a long one.
The display opens a rest on the resting players' screens and the DM is told.

Run from repo root:
    python -m pytest tests/test_rest.py -q
"""
import contextlib
import importlib.util
import io
import json
import os
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
SCRIPTS = SKILL / "scripts"
HTML = DISPLAY / "templates" / "index.html"
NODE = shutil.which("node")
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(SCRIPTS))

import inventory  # noqa: E402
import rest  # noqa: E402
import spellbook  # noqa: E402

FIGHTER = """# Ajeje
**Player:** Valerio  **Campaign:** prova

## Identity
- **Race:** Mezzorco | **Class:** Guerriero | **Level:** 2 | **Background:** Criminale

## Ability Scores
| STR | DEX | CON | INT | WIS | CHA |
|-----|-----|-----|-----|-----|-----|
| 19 (+4) | 14 (+2) | 16 (+3) | 12 (+1) | 12 (+1) | 10 (+0) |

## Combat Stats
- **HP:** 6 / 17 | **Temp HP:** 4
- **AC:** 16 | **Initiative:** +2 | **Speed:** 30 ft
- **Hit Dice:** 2d10 (remaining: 2)
- **Death Saves:** Successes: 1 | Failures: 2

## Spell Slots (if applicable)
*(nessuno)*
"""

WARLOCK = """# Morgana
## Identity
- **Race:** Tiefling | **Class:** Warlock | **Level:** 3

## Ability Scores
| STR | DEX | CON | INT | WIS | CHA |
|-----|-----|-----|-----|-----|-----|
| 8 (-1) | 14 (+2) | 9 (-1) | 12 (+1) | 10 (+0) | 17 (+3) |

## Combat Stats
- **HP:** 20 / 20 | **Temp HP:** 0
- **Hit Dice:** 3d8 (remaining: 1)
- **Death Saves:** Successes: 0 | Failures: 0

## Spell Slots (if applicable)
| Level | Total | Used |
|-------|-------|------|
| 2nd | 2 | 2 |
"""


class SheetTests(unittest.TestCase):
    def test_what_is_read(self):
        self.assertEqual(rest.read_hp(FIGHTER), {"current": 6, "max": 17, "temp": 4})
        self.assertEqual(rest.read_hit_dice(FIGHTER), {"die": 10, "max": 2, "remaining": 2})
        self.assertEqual((rest.con_mod(FIGHTER), rest.con_mod(WARLOCK), rest.con_mod("# x\n")), (3, -1, 0))
        self.assertEqual((rest.is_warlock(FIGHTER), rest.is_warlock(WARLOCK)), (False, True))
        self.assertEqual(rest.read_hit_dice("- **Hit Dice:** 3d8\n"), {"die": 8, "max": 3, "remaining": 3})

    def test_the_displays_count_wins(self):
        st = rest.state(FIGHTER, {"hp": {"current": 2, "max": 17}, "hit_dice": {"remaining": 1, "max": 2}})
        self.assertEqual((st["hp"], st["hit_dice"]["remaining"]), ({"current": 2, "max": 17, "temp": 0}, 1))

    def test_a_hit_die_heals_die_plus_con_up_to_the_maximum(self):
        text, done = rest.spend_hit_die(FIGHTER, roll=4)
        self.assertEqual((done["gained"], done["hp"], done["left"]), (7, 13, 1))
        self.assertIn("- **HP:** 13 / 17 | **Temp HP:** 4", text)
        self.assertIn("- **Hit Dice:** 2d10 (remaining: 1)", text)
        text, done = rest.spend_hit_die(text, roll=10)
        self.assertEqual((done["gained"], done["hp"], done["left"]), (4, 17, 0))
        with self.assertRaisesRegex(rest.RestError, "full"):
            rest.spend_hit_die(rest.write_hit_dice(text, 1))
        with self.assertRaisesRegex(rest.RestError, "no Hit Dice"):
            rest.spend_hit_die(rest.write_hp(text, 3))

    def test_a_bad_con_never_takes_hp_away(self):
        text, done = rest.spend_hit_die(rest.write_hp(WARLOCK, 10), roll=1)
        self.assertEqual((done["gained"], done["hp"]), (0, 10))

    def test_a_long_rest_brings_everything_back(self):
        hurt = rest.write_hit_dice(FIGHTER, 0)
        text, done = rest.long_rest(hurt, "2014")
        self.assertIn("- **HP:** 17 / 17 | **Temp HP:** 0", text)
        self.assertIn("- **Hit Dice:** 2d10 (remaining: 1)", text)        # half of 2
        self.assertIn("Successes: 0 | Failures: 0", text)
        self.assertEqual((done["hd_back"], done["slots_back"]), (1, 0))
        text, done = rest.long_rest(hurt, "2024")
        self.assertIn("(remaining: 2)", text)                               # all of them
        text, done = rest.long_rest(WARLOCK, "2014")
        self.assertEqual((done["slots_back"], spellbook.read_slots(text)), (2, {2: {"max": 2, "used": 0}}))
        self.assertIn("(remaining: 2)", text)                               # 3 // 2 = 1 back

    def test_a_short_rest_gives_a_warlock_the_pact_slots(self):
        self.assertEqual(rest.finish_short(FIGHTER), (FIGHTER, {"slots_back": 0}))
        text, done = rest.finish_short(WARLOCK)
        self.assertEqual((done["slots_back"], spellbook.read_slots(text)[2]["used"]), (2, 0))


class CommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._old = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_RUNTIME_DIR")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        os.environ["DND_RUNTIME_DIR"] = str(self.root / "rt")
        chars = self.root / "campaigns" / "prova" / "characters"
        chars.mkdir(parents=True)
        self.sheet = chars / "ajeje.md"
        self.sheet.write_text(FIGHTER, encoding="utf-8")
        (chars / "morgana.md").write_text(WARLOCK, encoding="utf-8")
        self.pushed = []
        self._saved = (inventory._push, inventory.active_campaign)
        inventory._push = self.pushed.append
        inventory.active_campaign = lambda: "prova"

    def tearDown(self):
        inventory._push, inventory.active_campaign = self._saved
        for key, old in self._old.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        self._tmp.cleanup()

    def run_rest(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = rest.main(["-c", "prova", *argv])
        return code, out.getvalue() + err.getvalue()

    def test_hit_dice_and_rests_from_the_command_line(self):
        code, out = self.run_rest("show", "ajeje")
        self.assertEqual(code, 0, out)
        self.assertIn("HP 6/17 (+4 temp)", out)
        self.assertIn("Hit Dice 2 of 2 (d10 +3)", out)
        code, out = self.run_rest("hd", "ajeje", "--count", "5")
        self.assertEqual(code, 0, out)
        self.assertIn("Hit Dice left", out)
        after = rest.read_hit_dice(self.sheet.read_text(encoding="utf-8"))
        self.assertLess(after["remaining"], 2)
        player = json.loads(self.pushed[-1][1])["players"][0]
        self.assertEqual((player["name"], player["hit_dice"]["remaining"]), ("Ajeje", after["remaining"]))
        self.assertEqual(self.run_rest("long", "ajeje")[0], 0)
        self.assertEqual(rest.read_hp(self.sheet.read_text(encoding="utf-8")), {"current": 17, "max": 17, "temp": 0})
        self.assertEqual(self.run_rest("short", "morgana")[0], 0)
        self.assertEqual(spellbook.read_slots((self.sheet.parent / "morgana.md").read_text(encoding="utf-8"))[2]["used"], 0)

    def test_the_displays_hp_is_the_one_that_counts(self):
        (self.root / "rt").mkdir(exist_ok=True)
        (self.root / "rt" / "stats.json").write_text(json.dumps({"players": [{"name": "Ajeje", "hp": {"current": 1, "max": 17}}]}), encoding="utf-8")
        code, out = self.run_rest("show", "ajeje")
        self.assertIn("HP 1/17", out)

    def test_a_mistake_changes_nothing(self):
        full = self.sheet.parent / "morgana.md"
        before = full.read_text(encoding="utf-8")
        for argv in (("hd", "morgana"), ("hd", "nessuno"), ("hd", "ajeje", "--count", "0")):
            code, out = self.run_rest(*argv)
            self.assertEqual(code, 1, argv)
            self.assertIn("Nothing was changed", out)
        self.assertEqual(full.read_text(encoding="utf-8"), before)

    def test_what_a_player_did_reaches_the_dm_once(self):
        rest.notes_add({"kind": "hd", "character": "Ajeje", "die": 10, "roll": 4, "con": 3, "gained": 7, "hp": 13, "max": 17, "left": 1, "hd_max": 2})
        rest.notes_add({"kind": "short", "character": "Morgana", "slots_back": 2})
        rest.notes_add({"kind": "long", "character": "Ajeje", "hp": 17, "max": 17, "hd_back": 1, "left": 2, "hd_max": 2, "slots_back": 0})
        rest.notes_add({"kind": "over", "rest": "long"})
        lines = rest.drain_notes()
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[0], "[[REST: Ajeje spent a Hit Die: d10 rolled 4 +3 CON = 7 HP — now 13/17 HP, "
                                   "1 of 2 Hit Dice left. Already on the sheet and the display.]]")
        self.assertIn("2 pact slot(s) back", lines[1])
        self.assertIn("finished the long rest: HP 17/17, 1 Hit Dice back (2 of 2)", lines[2])
        self.assertIn("calendar.py rest long, and tracker.py clear --all", lines[3])
        self.assertEqual(rest.drain_notes(), [])
        self.assertIn("from rest import drain_notes", (DISPLAY / "check_input.py").read_text(encoding="utf-8"))

    def test_it_runs_as_a_script(self):
        env = dict(os.environ, PYTHONUTF8="1")
        r = subprocess.run([sys.executable, str(SCRIPTS / "rest.py"), "-c", "prova", "show", "ajeje"],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn("Hit Dice 2 of 2", r.stdout)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_rest", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = pathlib.Path(tmp.name)
        self.sheet = self.dir / "ajeje.md"
        self.sheet.write_text(FIGHTER, encoding="utf-8")
        (self.dir / "morgana.md").write_text(WARLOCK, encoding="utf-8")
        files = {"ajeje": str(self.sheet), "morgana": str(self.dir / "morgana.md")}
        m = self.mod
        saved = (m._find_sheet_file, m._broadcast, m._rest_lib.notes_add, m._persist_stats, m._persist_rest, m._active_campaign, m._rest)
        def restore():
            (m._find_sheet_file, m._broadcast, m._rest_lib.notes_add, m._persist_stats, m._persist_rest, m._active_campaign, m._rest) = saved
        self.addCleanup(restore)
        m._find_sheet_file = lambda name: (files.get(m._sheet_key(name)), name, "prova")
        self.sent, self.notes = [], []
        m._broadcast = self.sent.append
        m._rest_lib.notes_add = self.notes.append
        m._persist_stats = lambda: None
        m._persist_rest = lambda: None
        m._active_campaign = lambda: "prova"
        m._rest = None
        with m._stats_lock:
            self._stats = dict(m._current_stats)
            m._current_stats.clear()
            m._current_stats.update({"ruleset": "2014", "players": [
                {"name": "Ajeje", "hp": {"current": 6, "max": 17, "temp": 4}, "hit_dice": {"remaining": 2, "max": 2}, "second_wind": False},
                {"name": "Morgana", "hp": {"current": 20, "max": 20}}]})
        self.addCleanup(self._restore_stats)

    def _restore_stats(self):
        with self.mod._stats_lock:
            self.mod._current_stats.clear()
            self.mod._current_stats.update(self._stats)

    def player(self, name="Ajeje"):
        return next(p for p in self.mod._current_stats["players"] if p["name"] == name)

    def test_the_dm_opens_a_rest_for_the_party_or_for_some(self):
        self.assertEqual(self.client.post("/rest", json={"kind": "nap"}).status_code, 400)
        self.assertEqual(self.client.post("/rest", json={"kind": "short", "characters": ["Gandalf"]}).status_code, 400)
        r = self.client.post("/rest", json={"kind": "short"})
        self.assertEqual(r.get_json()["rest"]["characters"], ["Ajeje", "Morgana"])
        self.assertTrue(self.sent[-1]["rest_opened"])
        r = self.client.post("/rest", json={"kind": "long", "characters": ["morgana"]})
        self.assertEqual(r.get_json()["rest"]["characters"], ["Morgana"])
        self.assertEqual(self.client.get("/rest").get_json()["rest"]["kind"], "long")
        self.assertEqual(self.client.post("/rest", json={"close": True}).get_json(), {"rest": None})
        self.assertIn("rest_set", self.mod._DM_ENDPOINTS)

    def test_a_short_rest_spends_hit_dice_and_ends_when_everybody_is_done(self):
        self.assertEqual(self.client.post("/rest/Ajeje/hit-die").status_code, 409)       # no rest open
        self.client.post("/rest", json={"kind": "short"})
        view = self.client.get("/rest/Ajeje").get_json()
        self.assertEqual((view["hp"]["current"], view["hit_dice"]["remaining"], view["con"], view["resting"]), (6, 2, 3, True))
        r = self.client.post("/rest/Ajeje/hit-die")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        spent = r.get_json()["spent"]
        self.assertEqual(spent["hp"], min(17, 6 + spent["roll"] + 3))
        on_sheet = rest.read_hp(self.sheet.read_text(encoding="utf-8"))
        self.assertEqual((on_sheet["current"], self.player()["hp"]["current"], self.player()["hit_dice"]["remaining"]),
                         (spent["hp"], spent["hp"], 1))
        self.assertEqual(self.notes[-1]["kind"], "hd")
        self.assertEqual(self.client.post("/rest/Morgana/hit-die").get_json()["code"], "full")
        r = self.client.post("/rest/Ajeje/finish")
        self.assertEqual((r.status_code, r.get_json()["done"]), (200, True))
        self.assertTrue(self.player()["second_wind"])
        self.assertEqual(self.client.post("/rest/Ajeje/hit-die").get_json()["code"], "done")
        self.client.post("/rest/Morgana/finish")
        self.assertIsNone(self.client.get("/rest").get_json()["rest"])
        self.assertEqual([n["kind"] for n in self.notes[-3:]], ["short", "short", "over"])
        self.assertEqual(spellbook.read_slots((self.dir / "morgana.md").read_text(encoding="utf-8"))[2]["used"], 0)

    def test_a_long_rest_is_one_tap(self):
        self.client.post("/rest", json={"kind": "long", "characters": ["Ajeje"]})
        self.assertEqual(self.client.post("/rest/Ajeje/hit-die").get_json()["code"], "kind")
        self.assertEqual(self.client.post("/rest/Morgana/finish").get_json()["code"], "resting")
        r = self.client.post("/rest/Ajeje/finish")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(rest.read_hp(self.sheet.read_text(encoding="utf-8")), {"current": 17, "max": 17, "temp": 0})
        self.assertEqual((self.player()["hp"]["current"], self.player()["hp"]["temp"]), (17, 0))
        self.assertEqual(self.notes[-2]["kind"], "long")
        self.assertEqual(self.notes[-1], {"kind": "over", "rest": "long"})

    def test_a_player_rests_only_as_themselves(self):
        src = (DISPLAY / "dnd-display-app.py").read_text(encoding="utf-8")
        body = src[src.index("def _rest_sheet("):]
        self.assertIn("_spell_sheet(character)", body[:body.index("\n\n\n")])
        for route in ("rest_character", "rest_hit_die", "rest_finish"):
            fn = src[src.index(f"def {route}("):]
            self.assertRegex(fn[:fn.index("\n\n\n")], r"_(rest|spell)_sheet\(character\)")
            self.assertNotIn(route, self.mod._DM_ENDPOINTS)


_SLICE = re.compile(r"(function _restPlan\(rest, view\).*?)\n// ── end rest plan ──", re.S)


class PageTests(unittest.TestCase):
    def test_the_panel_offers_what_fits(self):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the rest block is no longer where this test slices it")
        short = {"kind": "short", "characters": ["A", "B", "C"], "done": ["B"]}
        views = [{"resting": True, "hp": {"current": 5, "max": 10}, "hit_dice": {"remaining": 1, "max": 2, "die": 8}, "con": -1},
                 {"resting": True, "hp": {"current": 10, "max": 10}, "hit_dice": {"remaining": 1, "max": 2, "die": 8}, "con": 2},
                 {"resting": True, "hp": {"current": 5, "max": 10}, "hit_dice": {"remaining": 0, "max": 2, "die": 8}},
                 {"resting": True, "done": True, "hp": {"current": 5, "max": 10}, "hit_dice": {"remaining": 1, "max": 2, "die": 8}}]
        script = m.group(1) + "\nconst r = %s, v = %s;\nconsole.log(JSON.stringify([...v.map(x => _restPlan(r, x)), _restPlan(Object.assign({}, r, {kind: 'long'}), v[0]), _restPlan(null, null)]));\n" % (json.dumps(short), json.dumps(views))
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            hurt, full, empty, done, longr, none = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        self.assertEqual((hurt["canSpend"], hurt["conText"], hurt["waiting"]), (True, "-1", ["A", "C"]))
        self.assertEqual((full["canSpend"], full["why"], full["conText"]), (False, "full", "+2"))
        self.assertEqual((empty["canSpend"], empty["why"]), (False, "none"))
        self.assertEqual((done["canSpend"], done["done"]), (False, True))
        self.assertEqual((longr["canSpend"], longr["why"], longr["kind"]), (False, "", "long"))
        self.assertEqual((none["open"], none["waiting"], none["canSpend"]), (False, [], False))

    def test_the_page_has_the_panel_and_every_way_in(self):
        src = HTML.read_text(encoding="utf-8")
        for needle in ('id="rest-modal"', 'id="ln-rest"', 'data-ln-proxy="ln-rest"', 'id="ph-rest-opt"',
                       "window._onRest(payload)", 'id="ln-i-rest"'):
            self.assertIn(needle, src)

    def test_strings_exist_in_every_language(self):
        keys = ["rest.title", "rest.short", "rest.long", "rest.opened", "rest.hp", "rest.hd", "rest.spend", "rest.spent",
                "rest.full", "rest.noHd", "rest.finishShort", "rest.finishLong", "rest.longWill", "rest.confirm", "rest.done",
                "rest.waiting", "rest.notResting", "rest.failed", "rest.toastHd", "rest.toastDone", "rest.over"]
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
