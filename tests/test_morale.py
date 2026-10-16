"""
test_morale.py — monster morale (DMG 2014 p. 273, optional rule).

With `morale: on` in the campaign's Session Flags, tracker.py hit / hp say when
a morale check is due: a creature at half its HP for the first time, a group
whose leader falls, a group half down. morale ... roll makes the Wisdom save
(DC 10; the leader rolls for the group, else the best Charisma) and says who
flees. Without the flag nothing is prompted.

Run from repo root:
    python -m pytest tests/test_morale.py -q
"""
import contextlib
import io
import json
import os
import pathlib
import random
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
sys.path.insert(0, str(SKILL / "display"))
sys.path.insert(0, str(SKILL / "scripts"))

import tracker  # noqa: E402


class Fixed(random.Random):
    def __init__(self, *values):
        super().__init__(1)
        self.values = list(values)

    def randint(self, a, b):
        return self.values.pop(0) if self.values else super().randint(a, b)


class MoraleTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        self.camp = self.root / "campaigns" / "prova"
        self.camp.mkdir(parents=True)
        self.flag(True)
        old = os.environ.get("DND_CAMPAIGN_ROOT")
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        self.addCleanup(lambda: os.environ.pop("DND_CAMPAIGN_ROOT", None) if old is None else os.environ.__setitem__("DND_CAMPAIGN_ROOT", old))
        self.quiet(tracker.cmd_morale, "Capitano", "set", group="Banditi", leader=True, wis=1, cha=2)
        for n in (1, 2, 3):
            self.quiet(tracker.cmd_morale, f"Bandito {n}", "set", group="Banditi", wis=0, cha=n - 2)
        self.quiet(tracker.cmd_hp, "Capitano", 20, 20)
        for n in (1, 2, 3):
            self.quiet(tracker.cmd_hp, f"Bandito {n}", 11, 11)

    def flag(self, on):
        (self.camp / "state.md").write_text("## Session Flags\n- language: it\n" + ("- morale: on\n" if on else ""), encoding="utf-8")

    def quiet(self, fn, *args, **kw):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            res = fn("prova", *args, **kw)
        self.out = out.getvalue()
        return res

    def ent(self, name):
        return tracker._load("prova")[name.lower()]

    def test_the_flag(self):
        self.assertTrue(tracker.morale_on("prova"))
        self.flag(False)
        self.assertFalse(tracker.morale_on("prova"))
        self.quiet(tracker.cmd_hit, "Bandito 1", 7)
        self.assertNotIn("Morale", self.out)

    def test_half_hp_once(self):
        self.quiet(tracker.cmd_hit, "Bandito 1", 5)
        self.assertNotIn("Morale", self.out)
        self.quiet(tracker.cmd_hit, "Bandito 1", 1)
        self.assertIn("🏳 Morale: Bandito 1 is down to half its HP for the first time", self.out)
        self.assertEqual(self.ent("Bandito 1")["morale"]["due"], "half")
        self.quiet(tracker.cmd_hit, "Bandito 1", 1)
        self.assertNotIn("half its HP", self.out, "only the first time")

    def test_the_leader_falls(self):
        self.quiet(tracker.cmd_hit, "Capitano", 20)
        self.assertIn("the leader Capitano is down — the group Banditi may break", self.out)
        self.assertEqual([self.ent(f"Bandito {n}")["morale"]["due"] for n in (1, 2, 3)], ["leader"] * 3)
        res = self.quiet(tracker.cmd_morale, "Banditi", "roll", rng=Fixed(15))
        self.assertIn("(Bandito 3 rolls)", self.out, "no leader standing: the best Charisma")
        self.assertTrue(res["held"])
        self.assertEqual(self.ent("Bandito 1")["morale"]["due"], "")

    def test_half_the_group_down(self):
        self.quiet(tracker.cmd_hit, "Bandito 1", 11)
        self.assertNotIn("half of Banditi", self.out)
        self.quiet(tracker.cmd_hit, "Bandito 2", 11)
        self.assertIn("half of Banditi is down (2 of 4)", self.out)
        self.quiet(tracker.cmd_hit, "Bandito 3", 2)
        self.assertNotIn("half of Banditi", self.out, "said once")

    def test_rolling(self):
        res = self.quiet(tracker.cmd_morale, "Bandito 2", "roll", rng=Fixed(9))
        self.assertEqual((res["held"], res["total"]), (False, 9))
        self.assertIn("they BREAK", self.out)
        self.assertTrue(self.ent("Bandito 2")["morale"]["broken"])
        res = self.quiet(tracker.cmd_morale, "Banditi", "roll", mode="disadvantage", rng=Fixed(18, 4))
        self.assertIn("(Capitano rolls)", self.out)
        self.assertEqual((res["held"], res["total"]), (False, 5))
        res = self.quiet(tracker.cmd_morale, "Capitano", "roll", mode="fail")
        self.assertFalse(res["held"])
        with self.assertRaises(SystemExit):
            self.quiet(tracker.cmd_morale, "Nessuno", "roll")

    def test_fearless_and_the_srd(self):
        self.quiet(tracker.cmd_morale, "Zombie", "set", fearless=True)
        self.quiet(tracker.cmd_hp, "Zombie", 22, 22)
        self.quiet(tracker.cmd_hit, "Zombie", 15)
        self.assertNotIn("Morale", self.out)
        m = self.quiet(tracker.cmd_morale, "Capo", "set", srd="Bandit Captain")
        self.assertEqual((m["wis"], m["cha"]), (0, 2))
        self.quiet(tracker.cmd_status, "Capitano")
        self.assertIn("Morale: Banditi (leader)", self.out)

    def test_the_dm_screen(self):
        app = (SKILL / "display" / "dnd-display-app.py").read_text(encoding="utf-8")
        self.assertIn('"morale": ({k: ent["morale"].get(k)', app)
        self.assertIn("e.morale", (SKILL / "display" / "templates" / "dm.html").read_text(encoding="utf-8"))
        for path in sorted((SKILL / "display" / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("dms.morale.due", "dms.morale.check", "dms.morale.broken"):
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
