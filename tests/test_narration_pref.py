"""
test_narration_pref.py — a player's screen does not send the narration length.

/narration-pref is the DM's (it is in _DM_ENDPOINTS). The page used to post the
stored length on every load, so each player's phone got a 403 at start-up.

Run from repo root:
    python -m pytest tests/test_narration_pref.py -q
"""
import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"


class NarrationPrefTests(unittest.TestCase):
    def test_the_endpoint_is_the_dms(self):
        src = (DISPLAY / "dnd-display-app.py").read_text(encoding="utf-8")
        block = src[src.index("_DM_ENDPOINTS = {"):]
        self.assertIn('"narration_pref"', block[:block.index("}")])

    def test_a_players_screen_stops_before_sending_it(self):
        html = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")
        m = re.search(r"\(function _initVerbosity\(\) \{(.*?)\n\}\)\(\);", html, re.S)
        self.assertTrue(m, "the verbosity block moved")
        body = m.group(1)
        guard = body.index("login === 'player') return;")
        self.assertIn("window._IS_PLAYER", body[:guard])
        self.assertLess(guard, body.index("fetch('/narration-pref'"))


if __name__ == "__main__":
    unittest.main()
