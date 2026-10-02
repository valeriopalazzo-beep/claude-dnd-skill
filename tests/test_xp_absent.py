"""A player who misses a session still levels with the table.

WHY
===
When a player can't make a session, their PC sits it out but keeps pace on XP —
otherwise one missed evening leaves them a level behind for the rest of the
campaign. Passing the absent PC in `--characters` got that wrong two ways: a
CR-based award was split across everyone, so the players who actually fought
got less, and the display push added an empty card for a PC who was
deliberately left off the sidebar. `--absent` gives them the same award as the
table without counting them in the split, the party level, or the sidebar.
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "dnd" / "scripts"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location("xp", SCRIPTS / "xp.py")
xp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(xp)


def _sheet(level: int, cur: int, nxt: int) -> str:
    return f"# PC\n**Level:** {level}\n**XP:** {cur} / {nxt}\n"


class AbsentAwardTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._d.name)
        self._orig_dir, self._orig_push = xp.CAMPAIGNS_DIR, xp._push_xp_display
        xp.CAMPAIGNS_DIR = self.base
        self.pushed: list[str] = []
        xp._push_xp_display = lambda name, *a: self.pushed.append(name)
        chars = self.base / "c" / "characters"
        chars.mkdir(parents=True)
        (chars / "aldric.md").write_text(_sheet(3, 1000, 2700), encoding="utf-8")
        (chars / "mira.md").write_text(_sheet(3, 1000, 2700), encoding="utf-8")
        (chars / "thorne.md").write_text(_sheet(3, 1000, 2700), encoding="utf-8")

    def tearDown(self):
        xp.CAMPAIGNS_DIR, xp._push_xp_display = self._orig_dir, self._orig_push
        self._d.cleanup()

    def _award(self, characters, absent=None, monsters=None, difficulty=None):
        ns = argparse.Namespace(campaign="c", characters=characters, absent=absent,
                                monsters=monsters, difficulty=difficulty,
                                type=None, note="")
        with redirect_stdout(io.StringIO()):
            xp.cmd_award(ns)

    def _xp(self, name):
        return xp._read_char_state(self.base / "c" / "characters" / f"{name}.md")[0]

    def test_absent_pc_gets_the_same_award_as_the_table(self):
        # 3 goblins: 50 × 3 × 2 = 300 adjusted, split between the 2 present.
        self._award("Aldric,Mira", absent="Thorne", monsters="goblin:1/4:3")
        self.assertEqual(self._xp("aldric"), 1150)
        self.assertEqual(self._xp("mira"), 1150)
        self.assertEqual(self._xp("thorne"), 1150)

    def test_absent_pc_does_not_dilute_the_split(self):
        self._award("Aldric,Mira,Thorne", absent="thorne", monsters="goblin:1/4:3")
        self.assertEqual(self._xp("aldric"), 1150)
        self.assertEqual(self._xp("thorne"), 1150)

    def test_absent_pc_is_not_pushed_to_the_sidebar(self):
        self._award("Aldric,Mira", absent="Thorne", difficulty="medium")
        self.assertEqual(sorted(self.pushed), ["Aldric", "Mira"])

    def test_without_absent_nothing_changes(self):
        self._award("Aldric,Mira,Thorne", monsters="goblin:1/4:3")
        self.assertEqual(self._xp("aldric"), 1100)
        self.assertEqual(sorted(self.pushed), ["Aldric", "Mira", "Thorne"])

    def test_an_award_with_nobody_present_is_refused(self):
        with self.assertRaises(SystemExit):
            self._award("Thorne", absent="Thorne", difficulty="easy")
        self.assertEqual(self._xp("thorne"), 1000)


if __name__ == "__main__":
    unittest.main()
