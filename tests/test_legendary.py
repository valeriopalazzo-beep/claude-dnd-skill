"""
test_legendary.py — legendary actions, Legendary Resistance and lair actions.

tracker.py keeps them per creature (the SRD gives the options and the 3/3);
when the turn moves (push_stats.py --turn-current) the DM is told when the
creature's actions come back, when another creature's turn ends and a
legendary action may follow, and when initiative count 20 brings a lair action.

Run from repo root:
    python -m pytest tests/test_legendary.py -q
"""
import contextlib
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
sys.path.insert(0, str(SKILL / "display"))
sys.path.insert(0, str(SKILL / "scripts"))

import tracker  # noqa: E402

ORDER = {"order": ["Lyra", "Drago", "Hernio", "Goblin"], "current": "Lyra", "round": 1,
         "init": {"Lyra": 22, "Drago": 20, "Hernio": 14, "Goblin": 9}}


class Campaign(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        (self.root / "campaigns" / "prova").mkdir(parents=True)
        old = os.environ.get("DND_CAMPAIGN_ROOT")
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        self.addCleanup(lambda: os.environ.pop("DND_CAMPAIGN_ROOT", None) if old is None else os.environ.__setitem__("DND_CAMPAIGN_ROOT", old))

    def run_t(self, fn, *args, **kw):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            res = fn("prova", *args, **kw)
        return res, out.getvalue()

    def leg(self, name="Drago"):
        return tracker._load("prova")[name.lower()]["legendary"]


class CountTests(Campaign):
    def test_set_up_from_the_srd(self):
        leg, out = self.run_t(tracker.cmd_legendary, "Drago", "set", lair=True, srd="Adult Red Dragon")
        self.assertEqual((leg["actions"], leg["left"], leg["resist"], leg["lair"]), (3, 3, 3, True))
        self.assertEqual([o["name"] for o in leg["options"]], ["Detect", "Tail Attack", "Wing Attack"])
        self.assertIn("legendary actions 3/3", out)
        with self.assertRaises(SystemExit):
            self.run_t(tracker.cmd_legendary, "Goblin", "set", srd="Goblin")

    def test_spending(self):
        self.run_t(tracker.cmd_legendary, "Drago", "set", actions=3, resist=1)
        self.run_t(tracker.cmd_legendary, "Drago", "use", cost=2)
        self.assertEqual(self.leg()["left"], 1)
        with self.assertRaises(SystemExit):
            self.run_t(tracker.cmd_legendary, "Drago", "use", cost=2)
        self.run_t(tracker.cmd_legendary, "Drago", "resist")
        with self.assertRaises(SystemExit):
            self.run_t(tracker.cmd_legendary, "Drago", "resist")
        self.run_t(tracker.cmd_legendary, "Drago", "reset")
        self.assertEqual((self.leg()["left"], self.leg()["resist_left"]), (3, 1))

    def test_a_named_option_costs_what_the_book_says(self):
        self.run_t(tracker.cmd_legendary, "Drago", "set", srd="Adult Red Dragon")
        self.run_t(tracker.cmd_legendary, "Drago", "use", value="wing attack")
        self.assertEqual(self.leg()["left"], 1)

    def test_lair_actions_do_not_repeat(self):
        self.run_t(tracker.cmd_legendary, "Drago", "set", actions=3, lair=True)
        self.run_t(tracker.cmd_legendary, "Drago", "lair", value="Eruzione")
        with self.assertRaises(SystemExit):
            self.run_t(tracker.cmd_legendary, "Drago", "lair", value="eruzione")
        self.run_t(tracker.cmd_legendary, "Drago", "lair", value="Gas tossici")
        self.assertEqual(self.leg()["lair_last"], "Gas tossici")
        _, out = self.run_t(tracker.cmd_status)
        self.assertIn("Legendary: legendary actions 3/3", out)
        self.run_t(tracker.cmd_clear)
        self.assertEqual(self.leg()["lair_last"], "")


class TurnTests(Campaign):
    def setUp(self):
        super().setUp()
        self.run_t(tracker.cmd_legendary, "Drago", "set", actions=3, resist=3, lair=True)

    def test_the_turn_comes_round(self):
        self.run_t(tracker.cmd_legendary, "Drago", "use", cost=3)
        lines = tracker.turn_news("prova", ORDER, {"current": "Drago"})
        self.assertFalse(any("may take a legendary action" in l for l in lines), "none left to take")
        self.assertTrue(any("legendary actions back to 3/3" in l for l in lines))
        self.assertEqual(self.leg()["left"], 3)

    def test_after_another_creatures_turn(self):
        lines = tracker.turn_news("prova", dict(ORDER, current="Drago"), {"current": "Hernio"})
        lines = tracker.turn_news("prova", dict(ORDER, current="Hernio"), {"current": "Goblin"})
        self.assertEqual(len(lines), 1)
        self.assertIn("Hernio's turn is over: Drago may take a legendary action (3/3 left)", lines[0])

    def test_lair_on_twenty_losing_ties(self):
        # Drago (20) acts, then initiative 20 comes before Hernio (14).
        lines = tracker.turn_news("prova", dict(ORDER, current="Drago"), {"current": "Hernio"})
        self.assertTrue(any("LAIR ACTION before Hernio's turn" in l for l in lines), lines)
        lines = tracker.turn_news("prova", dict(ORDER, current="Lyra"), {"current": "Drago"})
        self.assertFalse(any("LAIR" in l for l in lines), "20 ties: the creature goes first")
        lines = tracker.turn_news("prova", dict(ORDER, current="Goblin"), {"current": "Lyra", "round": 2})
        self.assertFalse(any("LAIR" in l for l in lines), "Lyra is above 20: it comes later this round")
        low = dict(ORDER, init={"Lyra": 12, "Drago": 11, "Hernio": 9, "Goblin": 4}, current="Goblin")
        lines = tracker.turn_news("prova", low, {"current": "Lyra", "round": 2})
        self.assertTrue(any("LAIR" in l for l in lines), "nobody above 20: at the top of the round")
        self.assertEqual(tracker.turn_news("prova", dict(ORDER, init={}), {"current": "Hernio"})[:0], [])

    def test_nothing_without_a_move_or_a_legend(self):
        self.assertEqual(tracker.turn_news("prova", ORDER, {"current": "Lyra"}), [])
        self.run_t(tracker.cmd_legendary, "Drago", "off")
        self.assertEqual(tracker.turn_news("prova", ORDER, {"current": "Drago"}), [])


class WiringTests(unittest.TestCase):
    def test_initiative_numbers_and_the_push(self):
        src = (SKILL / "scripts" / "combat.py").read_text(encoding="utf-8")
        self.assertIn('"init": {c["name"]: c["initiative"]', src)
        push = (SKILL / "display" / "push_stats.py").read_text(encoding="utf-8")
        self.assertIn("tracker.turn_news(camp, prev, new)", push)
        dm = (SKILL / "display" / "templates" / "dm.html").read_text(encoding="utf-8")
        self.assertIn("e.legendary", dm)
        for path in sorted((SKILL / "display" / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("dms.leg.actions", "dms.leg.resist", "dms.leg.lair"):
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))

    def test_it_runs_as_a_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            (pathlib.Path(tmp) / "campaigns" / "prova").mkdir(parents=True)
            env = dict(os.environ, PYTHONUTF8="1", DND_CAMPAIGN_ROOT=tmp)
            r = subprocess.run([sys.executable, str(SKILL / "scripts" / "tracker.py"), "-c", "prova", "legendary", "Lich",
                                "set", "--as", "Lich", "--lair"], capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr[-400:])
            self.assertIn("legendary actions 3/3, Legendary Resistance 3/3, lair actions on 20", r.stdout)


if __name__ == "__main__":
    unittest.main()
