"""
test_shapes.py — Wild Shape and Polymorph: a character in a beast's body.

display/shapes.py puts a character in a creature's form from the SRD: the
druid's Wild Shape with its limits by level and circle and its two uses per
rest (PHB 2014 pp. 66-67, 69), or a spell — Polymorph, Animal Shapes,
Shapechange, True Polymorph. The form has its own hit points: damage comes off
it first, and when it drops to 0 the character is back with the damage left
over taken from their own. The display does that with any HP push for a
character in another shape, and the Attacks panel rolls the form's attacks.

Run from repo root:
    python -m pytest tests/test_shapes.py -q
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
HTML = DISPLAY / "templates" / "index.html"
NODE = shutil.which("node")
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(SKILL / "scripts"))

import media  # noqa: E402
import shapes  # noqa: E402

DRUID = """# Rovo
**Player:** Valerio

## Identity
- **Race:** Umano | **Class:** {cls} | **Level:** {level} | **Background:** Eremita

## Attacks
| Name | Attack Bonus | Damage | Type | Notes |
|------|-------------|--------|------|-------|
| Bastone | +2 | 1d6 | contundente | |
"""


class RuleTests(unittest.TestCase):
    def test_the_druid_on_the_sheet(self):
        self.assertEqual(shapes.druid_level(DRUID.format(cls="Druido", level=4)), (4, False))
        self.assertEqual(shapes.druid_level(DRUID.format(cls="Druido (Circolo della Luna)", level=6)), (6, True))
        self.assertEqual(shapes.druid_level(DRUID.format(cls="Guerriero 3 / Druido 2", level=5)), (2, False))
        self.assertEqual(shapes.druid_level(DRUID.format(cls="Warlock", level=5)), (0, False))

    def test_what_wild_shape_allows(self):
        self.assertEqual(shapes.wild_limits(2), {"cr": 0.25, "fly": False, "swim": False})
        self.assertEqual(shapes.wild_limits(4), {"cr": 0.5, "fly": False, "swim": True})
        self.assertEqual(shapes.wild_limits(8)["cr"], 1.0)
        self.assertEqual([shapes.wild_limits(lv, moon=True)["cr"] for lv in (2, 5, 6, 9, 18)], [1, 1, 2, 3, 6])
        with self.assertRaises(shapes.ShapeError):
            shapes.wild_limits(1)

    def test_forms_from_the_srd(self):
        wolf = shapes.make_form(shapes.srd_creature("lupo"), "it")
        self.assertEqual((wolf["creature"], wolf["cr"], wolf["hp"], wolf["ac"]), ("Lupo", 0.25, {"current": 11, "max": 11}, 13))
        bite, = wolf["attacks"]
        self.assertEqual((bite["name"], bite["bonus"], bite["damage"]["text"], bite["type"]), ("Morso", 4, "2d4+2", "perforanti"))
        bear = shapes.make_form(shapes.srd_creature("brown bear"))
        self.assertEqual([a["name"] for a in bear["attacks"]], ["Bite", "Claws"])
        shapes.check(wolf, "wild", 2)
        with self.assertRaisesRegex(shapes.ShapeError, "CR 1"):
            shapes.check(bear, "wild", 2)
        shapes.check(bear, "wild", 2, moon=True)
        with self.assertRaisesRegex(shapes.ShapeError, "swims"):
            shapes.check(shapes.make_form(shapes.srd_creature("crocodile")), "wild", 2, moon=True)
        with self.assertRaisesRegex(shapes.ShapeError, "flies"):
            shapes.check(shapes.make_form(shapes.srd_creature("giant eagle")), "wild", 6, moon=True)
        with self.assertRaisesRegex(shapes.ShapeError, "not a beast"):
            shapes.check(shapes.make_form(shapes.srd_creature("goblin")), "polymorph", target_cr=5)
        with self.assertRaisesRegex(shapes.ShapeError, "allows CR 1/2"):
            shapes.check(bear, "polymorph", target_cr=0.5)
        shapes.check(shapes.make_form(shapes.srd_creature("goblin")), "shapechange", target_cr=9)

    def test_damage_goes_through_the_form(self):
        wolf = shapes.make_form(shapes.srd_creature("wolf"))
        self.assertEqual(shapes.hit(wolf, 4), {"taken": 4, "left": 7, "excess": 0, "ended": False})
        self.assertEqual(shapes.heal(wolf, 10), 4)
        self.assertEqual(shapes.hit(wolf, 15), {"taken": 11, "left": 0, "excess": 4, "ended": True})

    def test_the_hours_run_out(self):
        data = shapes.blank()
        data["forms"].append({"character": "Rovo", "creature": "Lupo", "until_h": 100})
        self.assertEqual(shapes.expired(data, 99), [])
        self.assertEqual([f["character"] for f in shapes.expired(data, 100)], ["Rovo"])
        self.assertEqual(data["forms"], [])


class CommandTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        chars = self.root / "campaigns" / "prova" / "characters"
        chars.mkdir(parents=True)
        (chars / "rovo.md").write_text(DRUID.format(cls="Druido", level=4), encoding="utf-8")
        (chars / "lyra.md").write_text(DRUID.format(cls="Ladra", level=5).replace("# Rovo", "# Lyra"), encoding="utf-8")
        (self.root / "rt").mkdir()
        for var, value in (("DND_CAMPAIGN_ROOT", str(self.root)), ("DND_RUNTIME_DIR", str(self.root / "rt"))):
            old = os.environ.get(var)
            os.environ[var] = value
            self.addCleanup(lambda var=var, old=old: os.environ.pop(var, None) if old is None else os.environ.__setitem__(var, old))
        self.calls = []
        saved = (shapes._announce, shapes._push_hp, shapes._live_hp, media.active_campaign)
        shapes._announce = lambda event=None: self.calls.append(("announce", event))
        shapes._push_hp = lambda name, cur, mx: self.calls.append(("hp", name, cur, mx))
        shapes._live_hp = lambda name: {"current": 20, "max": 27}
        media.active_campaign = lambda: "prova"
        self.addCleanup(lambda: (setattr(shapes, "_announce", saved[0]), setattr(shapes, "_push_hp", saved[1]),
                                 setattr(shapes, "_live_hp", saved[2]), setattr(media, "active_campaign", saved[3])))

    def run_s(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = shapes.main(["--campaign", "prova", *argv])
        return code, out.getvalue() + err.getvalue()

    def test_two_wild_shapes_and_a_rest(self):
        code, out = self.run_s("wild", "rovo", "lupo")
        self.assertEqual(code, 0, out)
        self.assertIn("Rovo as Wolf (Wild Shape): HP 11/11", out)
        self.assertIn("1 use(s) left; up to 2 hour(s)", out)
        self.assertEqual(self.calls[-1], ("announce", {"what": "start", "name": "Rovo", "creature": "Wolf"}))
        self.assertEqual(self.run_s("wild", "rovo", "lupo")[0], 1)                  # already in another shape
        self.run_s("end", "rovo")
        self.assertEqual(self.run_s("wild", "rovo", "coccodrillo")[0], 0)          # swims: fine at the 4th level
        self.run_s("end", "rovo")
        code, out = self.run_s("wild", "rovo", "lupo")
        self.assertIn("used both Wild Shapes", out)
        self.assertEqual(self.run_s("wild", "lyra", "lupo")[0], 1)                 # not a druid
        self.assertTrue(shapes.rested("prova", "Rovo"))
        self.assertEqual(self.run_s("wild", "rovo", "lupo")[0], 0)

    def test_the_form_falls_and_the_damage_carries_over(self):
        self.run_s("spell", "lyra", "orso bruno", "--spell", "metamorfosi", "--by", "Vardamir")
        self.calls.clear()
        code, out = self.run_s("hit", "lyra", "30")
        self.assertIn("-30 → 4/34", out)
        code, out = self.run_s("hit", "lyra", "10")
        self.assertIn("the Brown Bear drops to 0", out)
        self.assertIn("takes the 6 damage left over: 14/27 HP", out)
        # The form leaves the display before the HP push, or the push would be taken as damage to the form.
        self.assertEqual([c[0] for c in self.calls], ["announce", "announce", "hp"])
        self.assertEqual(self.calls[-1], ("hp", "Lyra", 14, 27))
        self.assertEqual(self.run_s("hit", "lyra", "3")[0], 1)                     # her own shape: the sheet takes it

    def test_polymorph_is_capped_by_the_targets_level(self):
        self.assertEqual(self.run_s("spell", "lyra", "elefante", "--spell", "polymorph")[0], 0)      # CR 4, level 5
        self.run_s("end", "lyra")
        code, out = self.run_s("spell", "lyra", "tirannosauro", "--spell", "polymorph")
        self.assertEqual(code, 1)
        self.assertIn("CR 8: Polymorph allows CR 5 at most", out)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_shapes", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        m = self.mod
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = str(pathlib.Path(tmp.name) / "shapes.json")
        data = shapes.blank()
        wolf = shapes.make_form(shapes.srd_creature("wolf"), "it")
        wolf.update({"character": "Rovo", "how": "wild", "by": "", "until_h": None, "until_text": "2 h"})
        data["forms"].append(wolf)
        shapes.save(self.path, data)
        self.sent = []
        saved = (m._shapes_file, m._broadcast, m._persist_stats, m._concentration_follow)
        self.addCleanup(lambda: [setattr(m, k, v) for k, v in zip(("_shapes_file", "_broadcast", "_persist_stats",
                                                                    "_concentration_follow"), saved)])
        m._shapes_file = lambda: self.path
        m._broadcast = self.sent.append
        m._persist_stats = lambda: None
        self.conc = []
        m._concentration_follow = self.conc.append
        notes = pathlib.Path(tmp.name) / "notes.json"
        saved_notes = shapes._notes_file
        shapes._notes_file = lambda: str(notes)
        self.addCleanup(setattr, shapes, "_notes_file", saved_notes)
        with m._stats_lock:
            self._stats = json.loads(json.dumps(m._current_stats))
            m._current_stats.clear()
            m._current_stats.update({"players": [{"name": "Rovo", "hp": {"current": 20, "max": 27}}]})
        self.addCleanup(self._restore)

    def _restore(self):
        with self.mod._stats_lock:
            self.mod._current_stats.clear()
            self.mod._current_stats.update(self._stats)

    def player(self):
        return self.mod._current_stats["players"][0]

    def test_the_form_goes_on_the_card(self):
        self.assertEqual(self.client.post("/shapes", json={"event": {"what": "start", "name": "Rovo", "creature": "Lupo"}}).status_code, 204)
        self.assertEqual(self.player()["form"]["creature"], "Lupo")
        self.assertEqual(self.sent[-1]["shape_event"], {"what": "start", "name": "Rovo", "creature": "Lupo"})

    def test_an_hp_push_is_damage_to_the_form(self):
        self.client.post("/shapes", json={})
        self.player()["concentration"] = "Intralciare"
        self.client.post("/stats", json={"players": [{"name": "Rovo", "hp": {"current": 15, "max": 27}}]})
        self.assertEqual((self.player()["hp"]["current"], self.player()["form"]["hp"]["current"]), (20, 6))
        self.assertEqual(self.conc[-1], {"name": "Rovo", "spell": "Intralciare", "damage": 5, "down": False})
        self.client.post("/stats", json={"players": [{"name": "Rovo", "hp": {"current": 11, "max": 27}}]})   # 9 more: 3 carry over
        self.assertEqual((self.player()["hp"]["current"], self.player()["form"]), (17, None))
        self.assertEqual(shapes.load(self.path)["forms"], [])
        self.assertEqual(shapes.drain_notes(), ["[[SHAPE: Rovo is back in their own shape — the Lupo dropped to 0 and 3 damage carried over: 17 HP.]]"])
        self.assertIn({"shape_event": {"what": "ended", "name": "Rovo"}}, self.sent)
        self.client.post("/stats", json={"players": [{"name": "Rovo", "hp": {"current": 12, "max": 27}}]})   # own shape: as usual
        self.assertEqual(self.player()["hp"]["current"], 12)

    def test_the_form_attacks(self):
        self.client.post("/shapes", json={})
        rows = self.mod._form_attacks("Rovo")
        self.assertEqual([(r["name"], r["bonus"]) for r in rows], [("Morso", 4)])
        self.assertIsNone(self.mod._form_attacks("Lyra"))
        src = (DISPLAY / "dnd-display-app.py").read_text(encoding="utf-8")
        self.assertIn("beast = _form_attacks(name)", src)
        self.assertIn("_shapes.rested(_active_campaign(), name)", src)
        self.assertIn("shapes_changed", self.mod._DM_ENDPOINTS)


_SLICE = re.compile(r"(function _formChip\(form\).*?)\n// ── end form chip ──", re.S)


class PageTests(unittest.TestCase):
    def test_the_chip(self):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the form block is no longer where this test slices it")
        script = m.group(1) + "\nconsole.log(JSON.stringify([_formChip({creature: 'Lupo', hp: {current: 4, max: 11}}), _formChip(null)]));\n"
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            chip, none = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        self.assertEqual((chip, none), ({"text": "🐾 Lupo", "hp": "4/11", "state": "hurt"}, None))

    def test_hooks_and_strings(self):
        html = HTML.read_text(encoding="utf-8")
        for needle in ("window._onFormCards(stats)", "window._onShapeEvent(payload.shape_event)"):
            self.assertIn(needle, html)
        self.assertIn("_shapes(campaign, cal)", (SKILL / "scripts" / "calendar.py").read_text(encoding="utf-8"))
        self.assertIn("drain_shapes", (DISPLAY / "check_input.py").read_text(encoding="utf-8"))
        self.assertIn("_wild_shape_back(camp, name)", (SKILL / "scripts" / "rest.py").read_text(encoding="utf-8"))
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("shape.form", "shape.start", "shape.ended"):
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
