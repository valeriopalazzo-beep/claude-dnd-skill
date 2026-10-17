"""
test_cover.py — cover on the battle map (PHB 2014 p. 196, DMG 2014 p. 251).

From the corner of the attacker's square that sees best, a line to each
corner of the target's square: one or two lines blocked is half cover (+2 to
AC and Dexterity saves), three or four three-quarters (+5), all of them by
walls total cover. A creature, a tree or furniture gives half cover at most.
map_render.py works it out for the DM after every send and on request; the
Attacks panel reads the last map sent and puts the cover in the roll.

Run from repo root:
    python -m pytest tests/test_cover.py -q
"""
import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(SCRIPTS))

import attacks  # noqa: E402
import map_render  # noqa: E402

ROOM = """title: Sala del pozzo
---
##########
#A.......#
#...#....#
#...#.g..#
#...#....#
#..B.*.k.#
#........#
##########
---
A: Lyra Ombrafina | pc
B: Hernio | pc
g: Goblin 1 | foe
k: Goblin 2 | foe
"""


def cover(grid: str, frm: tuple, to: tuple) -> str:
    return map_render.cover(map_render.parse(grid), frm, to)


class GridTests(unittest.TestCase):
    def test_open_ground_and_neighbours(self):
        self.assertEqual(cover("A...g\n", (0, 0), (4, 0)), "none")
        self.assertEqual(cover("Ag\n", (0, 0), (1, 0)), "none")
        self.assertEqual(cover("#######\nA.....g\n#######\n", (0, 1), (6, 1)), "none")   # a corridor: lines along the walls are clear

    def test_what_stands_in_the_way(self):
        self.assertEqual(cover(".....\n.A#g.\n.....\n", (1, 1), (3, 1)), "half")             # a pillar of wall, open around it
        self.assertEqual(cover("A.b.g\n", (0, 0), (4, 0)), "half")                           # a creature: half at most
        self.assertEqual(cover("A..=..g\n", (0, 0), (6, 0)), "half")                         # furniture: half at most
        self.assertEqual(cover("A..*..g\n", (0, 0), (6, 0)), "half")                         # a tree

    def test_walls(self):
        self.assertEqual(cover("...#...\nA..#..g\n...#...\n", (0, 1), (6, 1)), "total")
        self.assertEqual(cover("####\n#A.#\n#.##\n#..g\n", (1, 1), (3, 3)), "three-quarters")   # a sliver past the corner
        self.assertEqual(cover("#######\n#..A..#\n###.###\n#..g..#\n#######\n", (3, 1), (3, 3)), "none")   # straight through the gap
        self.assertEqual(cover("#######\n#..A..#\n###.###\n#..g..#\n#######\n", (1, 1), (4, 3)), "three-quarters")  # an arrow slit
        self.assertEqual(cover("A#\n#g\n", (0, 0), (1, 1)), "none")                          # the corner they share

    def test_the_room(self):
        spec = map_render.parse(ROOM)
        self.assertEqual(map_render.cover_between(spec, "Lyra", "Goblin 1"),
                         {"cover": "total", "bonus": None, "from": "Lyra Ombrafina", "to": "Goblin 1"})
        self.assertEqual(map_render.cover_between(spec, "hernio", "goblin 2")["cover"], "half")       # behind the tree
        self.assertEqual(map_render.cover_between(spec, "B", "g")["cover"], "three-quarters")
        self.assertIsNone(map_render.cover_between(spec, "Lyra", "Goblin"))                             # two goblins: which?
        self.assertIsNone(map_render.cover_between(spec, "Lyra", "Drago"))
        report = map_render.cover_report(spec)
        self.assertIn("Goblin 2 has half cover from Hernio", report)
        self.assertIn("Lyra Ombrafina has total cover from Goblin 1", report)


class CommandTests(unittest.TestCase):
    def test_cover_from_the_command_line(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(ROOM)
            path = f.name
        try:
            env = dict(os.environ, PYTHONUTF8="1")
            r = subprocess.run([sys.executable, str(DISPLAY / "map_render.py"), "--file", path, "--cover", "Hernio", "Goblin 2"],
                               capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr[-400:])
            self.assertEqual(r.stdout.strip(), "Goblin 2 has half cover from Hernio: +2 to AC and Dexterity saves")
            r = subprocess.run([sys.executable, str(DISPLAY / "map_render.py"), "--file", path, "--cover", "Lyra", "Drago"],
                               capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
            self.assertEqual(r.returncode, 2)
            out = os.path.join(tempfile.gettempdir(), "cover-test.svg")
            r = subprocess.run([sys.executable, str(DISPLAY / "map_render.py"), "--file", path, "--out", out, "--art", "off"],
                               capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
            self.assertIn("map_render: cover — Goblin 2 has half cover from Hernio", r.stderr)
        finally:
            pathlib.Path(path).unlink(missing_ok=True)

    def test_the_last_map_is_kept_for_the_attacks(self):
        src = (DISPLAY / "map_render.py").read_text(encoding="utf-8")
        body = src[src.index("    map_id = media.slug(args.name"):]
        self.assertIn("(d / LAST_MAP).write_text(text", body[:600])


SHEET = """# Lyra Ombrafina

## Attacks
| Name | Attack Bonus | Damage | Type | Notes |
|------|-------------|--------|------|-------|
| Stocco | +5 | 1d8+3 | perforante | Arma accurata |
| Soffio | CD 12 | 2d6 | acido | TS Destrezza; metà |
"""


class FixedDice:
    def __init__(self, *values):
        self.values = list(values)

    def randbelow(self, n):
        return self.values.pop(0) - 1


class LineTests(unittest.TestCase):
    def test_the_cover_is_in_the_roll(self):
        stocco, soffio = attacks.read_attacks(SHEET)
        r = attacks.with_cover(attacks.roll(stocco, rng=FixedDice(12, 5)), {"cover": "half", "bonus": 2})
        self.assertEqual(r["vs_ac"], 15)
        self.assertEqual(attacks.action_line("Goblin 2", r, "it"),
                         "⚔ Stocco → Goblin 2: 17 per colpire (d20 12 +5) · 8 danni (perforante, 1d8+3: 5)"
                         " · mezza copertura: +2 alla CA del bersaglio (il tiro vale 15)")
        r = attacks.with_cover(attacks.roll(soffio, rng=FixedDice(3, 3)), {"cover": "three-quarters", "bonus": 5})
        self.assertTrue(attacks.action_line("", r).endswith(" · three-quarters cover: +5 to the targets' Dexterity saves"))
        r = attacks.with_cover(attacks.roll(stocco, rng=FixedDice(12, 5)), {"cover": "total", "bonus": None})
        self.assertTrue(attacks.action_line("", r, "it").endswith(" · copertura totale: secondo la mappa non si può bersagliare direttamente"))
        r = attacks.with_cover(attacks.roll(stocco, rng=FixedDice(12, 5)), {"cover": "none", "bonus": 0})
        self.assertNotIn("cover", r)

    def test_the_cover_comes_from_the_last_map_of_the_campaign(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        import media
        saved = media.media_dir
        media.media_dir = lambda campaign=None: pathlib.Path(tmp.name)
        self.addCleanup(setattr, media, "media_dir", saved)
        self.assertIsNone(attacks.map_cover("prova", "Hernio", "Goblin 2"))
        (pathlib.Path(tmp.name) / map_render.LAST_MAP).write_text(ROOM, encoding="utf-8")
        self.assertEqual(attacks.map_cover("prova", "Hernio", "Goblin 2")["cover"], "half")
        self.assertIsNone(attacks.map_cover("prova", "Hernio", ""))


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_cover", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def test_the_panel_puts_the_cover_in_the_line(self):
        m = self.mod
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sheet = pathlib.Path(tmp.name) / "lyra.md"
        sheet.write_text(SHEET, encoding="utf-8")
        saved = (m._find_sheet_file, m._broadcast, m._persist_log, m._persist_tail, m._check_auto_trigger, m._attacks.map_cover)
        self.addCleanup(lambda: [setattr(m, k, v) for k, v in zip(("_find_sheet_file", "_broadcast", "_persist_log", "_persist_tail",
                                                                    "_check_auto_trigger"), saved[:5])]
                        and setattr(m._attacks, "map_cover", saved[5]))
        m._find_sheet_file = lambda name: (str(sheet), name, "")
        m._broadcast = lambda payload: None
        m._persist_log = m._persist_tail = m._check_auto_trigger = lambda: None
        asked = []
        m._attacks.map_cover = lambda camp, who, target: asked.append((who, target)) or {"cover": "half", "bonus": 2}
        with m._stats_lock:
            before = dict(m._current_stats)
            m._current_stats.clear()
            m._current_stats.update({"players": [{"name": "Lyra"}]})
        self.addCleanup(lambda: m._current_stats.clear() or m._current_stats.update(before))
        self.addCleanup(lambda: m._staged.clear())
        r = self.client.post("/attacks/Lyra/roll", json={"attack": "stocco", "target": "Goblin 2", "lang": "it"},
                             headers={"X-DND-Device": "tel"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual((r.get_json()["result"]["cover"], asked), ("half", [("Lyra", "Goblin 2")]))
        self.assertIn(" · mezza copertura: +2 alla CA del bersaglio (il tiro vale ", m._staged["Lyra"]["text"])


class PageTests(unittest.TestCase):
    def test_strings_exist_in_every_language(self):
        import json
        keys = ["atk.cover.half", "atk.cover.three-quarters", "atk.cover.total", "atk.coverSave.half", "atk.coverSave.three-quarters"]
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
