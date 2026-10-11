"""
test_spellbook.py — a caster's spells and slots, read from and written to the sheet.

scripts/spellbook.py reads "Spell Slots" and "Known Spells / Cantrips" from a
character sheet; the display's Spellbook panel shows them and casts from them:
the slot is marked on the sheet and on the display, and the DM is told.

Run from repo root:
    python -m pytest tests/test_spellbook.py -q
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
import spellbook as sb  # noqa: E402

SHEET = """# Vardamir Ma'feyn
**Player:** —  **Campaign:** prova

## Attacks
| Name | Attack Bonus | Damage |
|------|-------------|--------|
| Stocco | +5 | 1d8+3 |

## Spell Slots (if applicable)
| Level | Total | Used |
|-------|-------|------|
| 1st | 3 | 1 |
| 2nd | 2 | 0 |

*Caratteristica da incantatore: CAR — CD dei tiri salvezza 13, attacco con incantesimi +5*

## Known Spells / Cantrips
- **Trucchetti:** Luci danzanti (Magia Drow, senza componenti), Beffa crudele
- **1° livello:** Cura ferite, Sussurri dissonanti, Risata incontenibile di Tasha
- **2° livello:** Frantumare

## Features & Traits
- **Factotum:** | 1st | 9 | 9 | non è una riga di slot
"""
FIGHTER = "# Ajeje\n\n## Spell Slots (if applicable)\n*(nessuno)*\n\n## Known Spells / Cantrips\n*(nessuno)*\n\n## Features\n"


class SheetTests(unittest.TestCase):
    def test_slots_and_the_casting_line_are_read_from_their_section_only(self):
        self.assertEqual(sb.read_slots(SHEET), {1: {"max": 3, "used": 1}, 2: {"max": 2, "used": 0}})
        self.assertEqual(sb.casting_line(SHEET), "Caratteristica da incantatore: CAR — CD dei tiri salvezza 13, attacco con incantesimi +5")
        self.assertEqual((sb.read_slots(FIGHTER), sb.casting_line(FIGHTER), sb.read_spells(FIGHTER)), ({}, "", []))
        self.assertEqual((sb.read_slots("# Nessuna sezione\n"), sb.read_spells("# Nessuna sezione\n")), ({}, []))

    def test_spells_come_with_the_level_their_line_gives(self):
        spells = sb.read_spells(SHEET)
        self.assertEqual([(s["name"], s["level"]) for s in spells],
                         [("Luci danzanti", 0), ("Beffa crudele", 0), ("Cura ferite", 1), ("Sussurri dissonanti", 1),
                          ("Risata incontenibile di Tasha", 1), ("Frantumare", 2)])
        self.assertEqual(spells[0]["note"], "Magia Drow, senza componenti")              # the comma in brackets does not split
        english = "## Known Spells\n- **Cantrips:** Fire Bolt, fire bolt\n- **3rd level:** Fireball\n- Shield\n- **Altro:** —\n"
        self.assertEqual([(s["name"], s["level"]) for s in sb.read_spells(english)], [("Fire Bolt", 0), ("Fireball", 3), ("Shield", None)])

    def test_a_spell_is_named_by_any_part_that_means_one(self):
        spells = sb.read_spells(SHEET)
        self.assertEqual(sb.known(spells, "RISATA")["name"], "Risata incontenibile di Tasha")
        for bad in ("ri", "palla di fuoco", ""):                                         # several, none, nothing
            with self.assertRaises(sb.SpellError):
                sb.known(spells, bad)

    def test_only_the_used_cell_of_that_level_changes(self):
        new = sb.write_used(SHEET, 1, 2)
        changed = [(a, b) for a, b in zip(SHEET.splitlines(), new.splitlines()) if a != b]
        self.assertEqual(changed, [("| 1st | 3 | 1 |", "| 1st | 3 | 2 |")])
        self.assertEqual(sb.write_used(SHEET.replace("\n", "\r\n"), 2, 1).count("\r\n"), SHEET.count("\n"))
        with self.assertRaises(sb.SpellError):
            sb.write_used(SHEET, 3, 1)


class CastingTests(unittest.TestCase):
    def test_a_spell_takes_a_slot_of_its_level_or_higher(self):
        new, done = sb.cast(SHEET, "cura ferite")
        self.assertEqual(done, {"spell": "Cura ferite", "level": 1, "slot": 1, "left": 1, "max": 3, "concentration": False})
        self.assertEqual(sb.read_slots(new)[1], {"max": 3, "used": 2})
        new, done = sb.cast(SHEET, "cura ferite", level="2")
        self.assertEqual((done["slot"], done["left"], sb.read_slots(new)), (2, 1, {1: {"max": 3, "used": 1}, 2: {"max": 2, "used": 1}}))
        for spell, level in (("frantumare", 1), ("cura ferite", 3), ("cura ferite", "x"), ("palla di fuoco", None)):
            with self.assertRaises(sb.SpellError):
                sb.cast(SHEET, spell, level)

    def test_a_cantrip_costs_nothing_and_concentration_is_reported(self):
        new, done = sb.cast(SHEET, "luci danzanti")
        self.assertEqual((new, done["slot"], done["level"], done["concentration"]), (SHEET, 0, 0, True))
        self.assertTrue(sb.cast(SHEET, "risata")[1]["concentration"])
        self.assertFalse(sb.cast(SHEET, "sussurri")[1]["concentration"])                 # not in the SRD: the sheet's level is enough

    def test_when_the_last_slot_is_gone_the_sheet_is_left_alone(self):
        text = SHEET
        text, _ = sb.cast(text, "cura ferite")
        text, done = sb.cast(text, "cura ferite")
        self.assertEqual(done["left"], 0)
        with self.assertRaises(sb.SpellError) as ctx:
            sb.cast(text, "cura ferite")
        self.assertIn("no 1st-level slot left", str(ctx.exception))

    def test_the_displays_count_wins_over_the_sheets(self):
        live = {1: {"max": 3, "used": 3}, 2: {"max": 2, "used": 0}, 3: {"max": 1, "used": 0}}
        with self.assertRaises(sb.SpellError):
            sb.cast(SHEET, "cura ferite", slots=live)                                     # the sheet says 2 left; the table says none
        new, done = sb.cast(SHEET, "cura ferite", level=3, slots=live)                    # a slot the sheet has no row for
        self.assertEqual((new, done["slot"], done["left"]), (SHEET, 3, 0))

    def test_a_rest_gives_slots_back(self):
        used = sb.write_used(SHEET, 2, 2)
        text, count = sb.restore(used, level=2)
        self.assertEqual((count, sb.read_slots(text)[2]["used"]), (1, 1))
        text, count = sb.restore(used)
        self.assertEqual((count, sb.read_slots(text)), (3, {1: {"max": 3, "used": 0}, 2: {"max": 2, "used": 0}}))
        for level in (2, 5, "x"):
            with self.assertRaises(sb.SpellError):
                sb.restore(SHEET, level=level)

    def test_the_book_puts_the_srd_next_to_the_sheet(self):
        data = sb.book(SHEET, "2014", "it")
        self.assertEqual(data["slots"], {"1": {"max": 3, "used": 1}, "2": {"max": 2, "used": 0}})
        by_name = {s["name"]: s for s in data["spells"]}
        self.assertEqual([s["level"] for s in data["spells"]], [0, 0, 1, 1, 1, 2])
        self.assertTrue(by_name["Cura ferite"]["known"])
        self.assertTrue(by_name["Cura ferite"]["range"])
        self.assertEqual((by_name["Sussurri dissonanti"]["known"], by_name["Sussurri dissonanti"]["range"]), (False, ""))
        self.assertTrue(by_name["Risata incontenibile di Tasha"]["concentration"])


class CommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._old = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_RUNTIME_DIR")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        chars = self.root / "campaigns" / "prova" / "characters"
        chars.mkdir(parents=True)
        self.sheet = chars / "vardamir-ma-feyn.md"
        self.sheet.write_text(SHEET, encoding="utf-8")
        (chars / "ajeje.md").write_text(FIGHTER, encoding="utf-8")
        self.pushed = []
        self._saved = (inventory._push, inventory.active_campaign)
        inventory._push = self.pushed.append
        inventory.active_campaign = lambda: "altra"          # the display is showing another campaign

    def tearDown(self):
        inventory._push, inventory.active_campaign = self._saved
        for key, old in self._old.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        self._tmp.cleanup()

    def run_sb(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sb.main(["-c", "prova", *argv])
        return code, out.getvalue() + err.getvalue()

    def test_cast_and_rest_from_the_command_line(self):
        code, out = self.run_sb("show", "vardamir")
        self.assertEqual(code, 0, out)
        self.assertIn("1st level: 2 of 3 slots left", out)
        self.assertIn("· Sussurri dissonanti (level 1) [not in the SRD]", out)
        code, out = self.run_sb("cast", "Vardamir Ma'feyn", "risata")
        self.assertEqual(code, 0, out)
        self.assertIn("1st-level slot used, 1 of 3 left — concentration", out)
        self.assertEqual(sb.read_slots(self.sheet.read_text(encoding="utf-8"))[1]["used"], 2)
        self.assertEqual(self.pushed, [], "another campaign than the display's is not pushed")
        inventory.active_campaign = lambda: "prova"
        self.assertEqual(self.run_sb("cast", "vardamir", "cura", "--level", "2")[0], 0)
        self.assertEqual(self.pushed[-1], ["--player", "Vardamir Ma'feyn", "--spell-slots",
                                           '{"1": {"max": 3, "used": 2}, "2": {"max": 2, "used": 1}}'])
        self.assertEqual(self.run_sb("restore", "vardamir")[0], 0)
        self.assertEqual(sb.read_slots(self.sheet.read_text(encoding="utf-8")), {1: {"max": 3, "used": 0}, 2: {"max": 2, "used": 0}})
        self.assertIn("no spells on the sheet", self.run_sb("show", "ajeje")[1])

    def test_a_mistake_changes_nothing(self):
        before = self.sheet.read_text(encoding="utf-8")
        for argv in (("cast", "vardamir", "palla di fuoco"), ("cast", "vardamir", "frantumare", "--level", "1"),
                     ("cast", "nessuno", "cura"), ("restore", "vardamir", "--level", "2"), ("cast", "ajeje", "cura")):
            code, out = self.run_sb(*argv)
            self.assertEqual(code, 1, argv)
            self.assertIn("Nothing was changed", out)
        self.assertEqual(self.sheet.read_text(encoding="utf-8"), before)

    def test_what_a_player_casts_reaches_the_dm_once(self):
        os.environ["DND_RUNTIME_DIR"] = str(self.root / "rt")
        (self.root / "rt").mkdir()
        sb.casts_add({"character": "Vardamir", "spell": "Cura ferite", "level": 1, "slot": 2, "left": 1, "max": 2, "concentration": False})
        sb.casts_add({"character": "Vardamir", "spell": "Luci danzanti", "level": 0, "slot": 0, "concentration": True})
        sb.casts_add({"character": "Vardamir", "spell": "Luci danzanti", "dropped": True})
        lines = sb.drain_casts()
        self.assertEqual(len(lines), 3)
        self.assertIn("[[SPELL: Vardamir cast Cura ferite from the spellbook with a 2nd-level slot (1 of 2 left)", lines[0])
        self.assertIn("do not mark it again", lines[0])
        self.assertIn("cast the cantrip Luci danzanti", lines[1])
        self.assertIn("It needs concentration", lines[1])
        self.assertEqual(lines[2], "[[SPELL: Vardamir stopped concentrating on Luci danzanti.]]")
        self.assertEqual(sb.drain_casts(), [])
        self.assertIn("from spellbook import drain_casts", (DISPLAY / "check_input.py").read_text(encoding="utf-8"))

    def test_it_runs_as_a_script(self):
        env = dict(os.environ, PYTHONUTF8="1")
        r = subprocess.run([sys.executable, str(SCRIPTS / "spellbook.py"), "-c", "prova", "show", "vardamir"],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn("Frantumare (level 2)", r.stdout)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_spellbook", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = pathlib.Path(tmp.name)
        self.sheet = self.dir / "vardamir-ma-feyn.md"
        self.sheet.write_text(SHEET, encoding="utf-8")
        (self.dir / "ajeje.md").write_text(FIGHTER, encoding="utf-8")
        files = {"vardamirmafeyn": str(self.sheet), "ajeje": str(self.dir / "ajeje.md")}
        saved = (self.mod._find_sheet_file, self.mod._broadcast, self.mod._spellbook.casts_add, self.mod._persist_stats)
        self.addCleanup(lambda: setattr(self.mod, "_find_sheet_file", saved[0]) or setattr(self.mod, "_broadcast", saved[1])
                        or setattr(self.mod._spellbook, "casts_add", saved[2]) or setattr(self.mod, "_persist_stats", saved[3]))
        self.mod._find_sheet_file = lambda name: (files.get(self.mod._sheet_key(name)), name, "prova")
        self.sent, self.notes = [], []
        self.mod._broadcast = self.sent.append
        self.mod._spellbook.casts_add = self.notes.append
        self.mod._persist_stats = lambda: None
        with self.mod._stats_lock:
            self._stats = dict(self.mod._current_stats)
            self.mod._current_stats.clear()
            self.mod._current_stats.update({"ruleset": "2014", "players": [
                {"name": "Vardamir Ma'feyn", "spell_slots": {"1": {"used": 1, "max": 3}}},
                {"name": "Ajeje"}]})
        self.addCleanup(self._restore_stats)

    def _restore_stats(self):
        with self.mod._stats_lock:
            self.mod._current_stats.clear()
            self.mod._current_stats.update(self._stats)

    def test_the_table_lists_its_casters_and_a_book_opens(self):
        self.assertEqual(self.client.get("/spellbook").get_json(), [{"character": "Vardamir Ma'feyn", "spells": 6}])
        book = self.client.get("/spellbook/Vardamir Ma'feyn?lang=it").get_json()
        self.assertEqual((book["character"], book["slots"], book["concentration"]),
                         ("Vardamir Ma'feyn", {"1": {"max": 3, "used": 1}, "2": {"max": 2, "used": 0}}, ""))
        self.assertEqual(len(book["spells"]), 6)
        self.assertIn("CD dei tiri salvezza 13", book["casting"])
        self.assertEqual(self.client.get("/spellbook/Gandalf").status_code, 404)

    def test_casting_marks_the_slot_everywhere_and_tells_the_dm(self):
        r = self.client.post("/spellbook/Vardamir Ma'feyn/cast", json={"spell": "Risata incontenibile di Tasha", "level": 1, "lang": "it"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        body = r.get_json()
        self.assertEqual((body["cast"]["slot"], body["cast"]["left"], body["slots"]["1"], body["concentration"]),
                         (1, 1, {"max": 3, "used": 2}, "Risata incontenibile di Tasha"))
        self.assertEqual(sb.read_slots(self.sheet.read_text(encoding="utf-8"))[1]["used"], 2)
        player = self.mod._current_stats["players"][0]
        self.assertEqual((player["spell_slots"]["1"], player["concentration"]), ({"used": 2, "max": 3}, "Risata incontenibile di Tasha"))
        self.assertEqual(self.sent[-1], {"spell_cast": {"character": "Vardamir Ma'feyn", "spell": "Risata incontenibile di Tasha", "slot": 1}})
        self.assertIn("stats", self.sent[-2])
        self.assertEqual((self.notes[-1]["character"], self.notes[-1]["slot"], self.notes[-1]["concentration"]), ("Vardamir Ma'feyn", 1, True))

    def test_a_slot_that_is_not_there_is_refused_and_nothing_moves(self):
        before = self.sheet.read_text(encoding="utf-8")
        with self.mod._stats_lock:
            self.mod._current_stats["players"][0]["spell_slots"]["1"] = {"used": 3, "max": 3}
        r = self.client.post("/spellbook/Vardamir Ma'feyn/cast", json={"spell": "Cura ferite"})
        self.assertEqual((r.status_code, r.get_json()["code"]), (409, "slots"))
        r = self.client.post("/spellbook/Vardamir Ma'feyn/cast", json={"spell": "Palla di fuoco"})
        self.assertEqual((r.status_code, r.get_json()["code"]), (409, "spell"))
        self.assertEqual((self.sheet.read_text(encoding="utf-8"), self.sent, self.notes), (before, [], []))

    def test_concentration_can_be_let_go(self):
        self.assertEqual(self.client.post("/spellbook/Vardamir Ma'feyn/concentration", json={}).status_code, 409)
        with self.mod._stats_lock:
            self.mod._current_stats["players"][0]["concentration"] = "Luci danzanti"
        r = self.client.post("/spellbook/Vardamir Ma'feyn/concentration", json={})
        self.assertEqual((r.status_code, r.get_json()["concentration"]), (200, ""))
        self.assertIsNone(self.mod._current_stats["players"][0]["concentration"])
        self.assertEqual(self.notes[-1], {"character": "Vardamir Ma'feyn", "spell": "Luci danzanti", "dropped": True})

    def test_a_player_opens_only_their_own_book(self):
        src = (DISPLAY / "dnd-display-app.py").read_text(encoding="utf-8")
        body = src[src.index("def _spell_sheet("):src.index("@app.route(\"/spellbook\", methods=[\"GET\"])")]
        self.assertIn("_is_other_character(character)", body)
        for route in ("spellbook_get", "spellbook_cast", "spellbook_drop"):
            fn = src[src.index(f"def {route}("):]
            self.assertIn("_spell_sheet(character)", fn[:fn.index("\n\n\n")])
            self.assertNotIn(route, self.mod._DM_ENDPOINTS)


_SLICE = re.compile(r"(function _spellRows\(book, spellName\).*?)\n// ── end spell rows ──", re.S)


class PageTests(unittest.TestCase):
    def test_the_panel_groups_the_spells_and_offers_the_slots_that_fit(self):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the spellbook block is no longer where this test slices it")
        book = {"slots": {"2": {"max": 2, "used": 2}, "1": {"max": 3, "used": 1}},
                "spells": [{"name": "Luci", "level": 0}, {"name": "Cura", "level": 1}, {"name": "Boh", "level": None},
                           {"name": "Frantumare", "level": 2}, {"name": "Sonno", "level": 1}]}
        script = m.group(1) + "\nconst b = %s;\nconsole.log(JSON.stringify([_spellRows(b), _spellRows(b, 'Cura'), _spellRows(b, 'Frantumare'), _spellRows(b, 'Luci'), _spellRows(null, 'x')]));\n" % json.dumps(book)
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            whole, cura, frant, luci, blank = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        self.assertEqual(whole["slots"], [{"level": 1, "max": 3, "left": 2}, {"level": 2, "max": 2, "left": 0}])
        self.assertEqual([(g["level"], [s["name"] for s in g["spells"]]) for g in whole["groups"]],
                         [(0, ["Luci"]), (1, ["Cura", "Sonno"]), (2, ["Frantumare"]), (-1, ["Boh"])])
        self.assertEqual([o["level"] for o in cura["options"]], [1, 2])
        self.assertEqual([(o["level"], o["left"]) for o in frant["options"]], [(2, 0)])
        self.assertEqual((luci["spell"]["name"], luci["options"]), ("Luci", []))
        self.assertEqual((blank["slots"], blank["groups"], blank["spell"]), ([], [], None))

    def test_the_page_has_the_panel_and_every_way_in(self):
        src = HTML.read_text(encoding="utf-8")
        for needle in ('id="spell-modal"', 'id="ln-spells"', 'data-ln-proxy="ln-spells"', 'id="ph-spells-opt"',
                       "payload.spell_cast", "window._onSpellStats"):
            self.assertIn(needle, src)

    def test_strings_exist_in_every_language(self):
        keys = ["spell.title", "spell.titleOf", "spell.all", "spell.cantrips", "spell.level", "spell.unknownLevel", "spell.left",
                "spell.conc", "spell.drop", "spell.tag.conc", "spell.tag.ritual", "spell.cast", "spell.confirm", "spell.noSlot",
                "spell.cantripFree", "spell.notSrd", "spell.loading", "spell.failed", "spell.empty", "spell.toast"]
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
