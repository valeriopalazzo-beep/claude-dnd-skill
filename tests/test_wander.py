"""
test_wander.py — random encounters, a table for each zone of the region.

wander.py keeps zones (their places on the region map, a table on d12+d8 by
default, a d20 check every N hours, DMG 2014 pp. 85-87); calendar.py rolls the
checks that fall due in the party's zone; the DM's screen shows the zone and
its table. The players see nothing.

Run from repo root:
    python -m pytest tests/test_wander.py -q
"""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import random
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(SKILL / "scripts"))

import wander  # noqa: E402

TABLE = """# Bosco di Cenere
2-4   | Un orso bruno affamato            | brown bear
5-8   | 2d4 lupi a caccia                 | wolf:2d4
9-13  | Carbonai che tornano al villaggio
14-16 | Tracce fresche di qualcosa di grosso
17-20 | Una pattuglia di 1d4+1 cultisti   | cultist:1d4+1
"""


class TableTests(unittest.TestCase):
    def test_dice_and_ranges(self):
        self.assertEqual(wander.die_range("d12+d8"), (2, 20))
        self.assertEqual(wander.die_range("d20"), (1, 20))
        self.assertEqual(wander.die_range("2d6"), (2, 12))
        self.assertEqual(wander.die_range("d100"), (1, 100))
        with self.assertRaises(wander.WanderError):
            wander.die_range("tanti")
        rng = random.Random(3)
        rolls = [wander.roll_die("d12+d8", rng) for _ in range(2000)]
        self.assertEqual((min(rolls), max(rolls)), (2, 20))
        self.assertGreater(rolls.count(11), rolls.count(3) * 3, "d12+d8 favours the middle rows")

    def test_a_table_must_cover_the_die(self):
        rows = wander.parse_rows(TABLE, "d12+d8")
        self.assertEqual([(r["lo"], r["hi"]) for r in rows], [(2, 4), (5, 8), (9, 13), (14, 16), (17, 20)])
        self.assertEqual(rows[1]["monsters"], "wolf:2d4")
        for text, part in (("2-4 | a\n6-20 | b", "missing 5"), ("2-10 | a\n10-20 | b", "twice 10"),
                           ("1-20 | a", "outside"), ("2-20", "range | what"), ("x | a", "not a range"),
                           ("", "empty table")):
            with self.subTest(text=text), self.assertRaises(wander.WanderError) as cm:
                wander.parse_rows(text, "d12+d8")
            self.assertIn(part, str(cm.exception))

    def test_the_same_dice_are_the_same_wolves(self):
        zone = {"name": "Bosco", "die": "d12+d8", "rows": wander.parse_rows(TABLE, "d12+d8")}
        memo = {}
        text = wander.roll_text("2d4 lupi, 2d4 ululati, 1d6 corvi", random.Random(5), memo)
        n = memo["2d4"]
        self.assertTrue(text.startswith(f"{n} lupi, {n} ululati, "))
        hit = wander.roll_table(zone, value=6, rng=random.Random(9))
        self.assertRegex(hit["text"], r"^\d lupi a caccia$")
        self.assertEqual(hit["rating"], "", "no campaign, no rating")


class Campaign(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        self.camp = self.root / "campaigns" / "prova"
        (self.camp / "characters").mkdir(parents=True)
        (self.root / "rt").mkdir()
        for name, lv in (("Lyra", 3), ("Lio", 3), ("Hernio", 3), ("Ajeje", 3)):
            (self.camp / "characters" / f"{name.lower()}.md").write_text(f"# {name}\n**Level:** {lv}\n", encoding="utf-8")
        (self.camp / "region.json").write_text(json.dumps({"party": "Carbonaia", "places": []}), encoding="utf-8")
        for var, value in (("DND_CAMPAIGN_ROOT", str(self.root)), ("DND_RUNTIME_DIR", str(self.root / "rt"))):
            old = os.environ.get(var)
            os.environ[var] = value
            self.addCleanup(lambda var=var, old=old: os.environ.pop(var, None) if old is None else os.environ.__setitem__(var, old))

    def run_w(self, *argv, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        old = sys.stdin
        sys.stdin = io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = wander.main(["--campaign", "prova", *argv])
        finally:
            sys.stdin = old
        return code, out.getvalue() + err.getvalue()

    def saved(self):
        return wander.load(str(self.camp / "wander.json"))


class CommandTests(Campaign):
    def test_the_zone_follows_the_region_map(self):
        code, out = self.run_w("zone", "Bosco di Cenere", "--places", "Carbonaia, La Rocca Muta", "--every", "4", stdin=TABLE)
        self.assertEqual(code, 0, out)
        data = self.saved()
        self.assertEqual(wander.current_zone(data, "prova")["name"], "Bosco di Cenere")
        self.assertEqual(self.run_w("zone", "Palude", "--places", "Carbonaia", "--die", "d20", stdin="1-20 | Nebbia")[0], 0)
        data = self.saved()
        self.assertEqual(wander.current_zone(data, "prova")["name"], "Palude", "a place belongs to one zone")
        self.assertEqual(data["zones"][0]["places"], ["La Rocca Muta"])
        self.assertEqual(self.run_w("here", "Bosco")[0], 0)
        self.assertEqual(wander.current_zone(self.saved(), "prova")["name"], "Bosco di Cenere")
        self.run_w("here", "--auto")
        self.assertEqual(wander.current_zone(self.saved(), "prova")["name"], "Palude")

    def test_checks_fall_due_with_the_hours(self):
        self.run_w("zone", "Bosco", "--places", "Carbonaia", "--every", "4", "--on", "2", stdin=TABLE)
        data = self.saved()
        self.assertEqual(wander.pass_hours(data, "prova", 3, random.Random(1)), [])
        hits = wander.pass_hours(data, "prova", 9, random.Random(1))
        self.assertEqual(len(hits), 3)                     # 12 hours: three checks
        self.assertEqual(data["zones"][0]["hours"], 0)
        real = [h for h in hits if not h.get("miss")]
        self.assertTrue(real, "with --on 2 almost every check is an encounter")
        self.assertEqual(len(data["history"]), len(real))
        line = wander.format_hit(real[0])
        self.assertTrue(line.startswith("ENCOUNTER in Bosco (d20 "), line)

    def test_monsters_are_rated_for_the_party(self):
        self.run_w("zone", "Bosco", "--places", "Carbonaia", stdin=TABLE)
        zone = self.saved()["zones"][0]
        hit = wander.roll_table(zone, "prova", random.Random(2), value=3)
        self.assertIn("Brown Bear: ", hit["rating"])
        self.assertIn('encounter.py rate --monsters "Brown Bear:1"', hit["rating"])
        hit = wander.roll_table(zone, "prova", random.Random(2), value=6)
        wolves = int(hit["text"].split()[0])
        self.assertIn(f'"Wolf:{wolves}"', hit["rating"])

    def test_paused_or_nowhere_means_no_checks(self):
        self.run_w("zone", "Bosco", "--places", "Altrove", "--every", "1", "--on", "2", stdin=TABLE)
        self.assertEqual(wander.tick("prova", 10), [])
        self.run_w("here", "Bosco")
        self.run_w("pause", "Bosco")
        self.assertEqual(wander.tick("prova", 10), [])
        self.run_w("resume", "Bosco")
        self.assertTrue(wander.tick("prova", 3))

    def test_mistakes_change_nothing(self):
        self.run_w("zone", "Bosco", stdin=TABLE)
        before = self.saved()
        for argv, stdin in ((("zone", "Bosco", "--every", "0"), TABLE), (("zone", "Bosco"), "2-3 | a"),
                            (("zone", "Bosco", "--on", "25"), TABLE), (("here", "Mare"), ""), (("roll",), "")):
            code, out = self.run_w(*argv, stdin=stdin)
            self.assertEqual(code, 1, argv)
            self.assertIn("Nothing was changed", out)
        self.assertEqual(self.saved(), before)

    def test_the_calendar_rolls_them(self):
        cal = importlib.util.spec_from_file_location("calendar_under_test_wander", str(SKILL / "scripts" / "calendar.py"))
        mod = importlib.util.module_from_spec(cal)
        cal.loader.exec_module(mod)
        self.run_w("zone", "Bosco", "--places", "Carbonaia", "--every", "1", "--on", "2", stdin=TABLE)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            mod._wander("prova", 5, "14 Brumosa")
        self.assertIn("⚔ ENCOUNTER in Bosco", out.getvalue())
        self.assertEqual(self.saved()["history"][-1]["at"], "14 Brumosa")

    def test_it_runs_as_a_script(self):
        self.run_w("zone", "Bosco", "--places", "Carbonaia", stdin=TABLE)
        env = dict(os.environ, PYTHONUTF8="1")
        r = subprocess.run([sys.executable, str(SKILL / "scripts" / "wander.py"), "--campaign", "prova", "show"],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn("5-8  2d4 lupi a caccia  [wolf:2d4]", r.stdout)


class DMScreenTests(Campaign):
    def test_the_dm_sees_the_zone_and_its_table(self):
        self.run_w("zone", "Bosco", "--places", "Carbonaia", "--every", "6", stdin=TABLE)
        view = wander.dm_view(self.saved(), "prova")
        self.assertEqual((view["zone"], view["every"], view["on"], view["next_in"]), ("Bosco", 6, 18, 6))
        self.assertEqual(view["rows"][1], {"range": "5-8", "text": "2d4 lupi a caccia", "monsters": "wolf:2d4"})
        src = (DISPLAY / "templates" / "dm.html").read_text(encoding="utf-8")
        self.assertIn('id="s-wander"', src)
        app = (DISPLAY / "dnd-display-app.py").read_text(encoding="utf-8")
        self.assertIn('"wander": wander', app)
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("dms.wander", "dms.wander.paused", "dms.wander.check", "dms.wander.last", "dms.wander.nozone"):
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
