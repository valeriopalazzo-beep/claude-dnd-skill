"""
test_turn_alert.py — a player's phone is told when the tracker reaches them.

The display buzzes the browser bound to a character when the initiative
tracker lands on that character ("now") and one step before ("next"). The
decision lives in `_turnAlertFor(me, prev, cur)` in templates/index.html;
it is run through node against the real source, not re-implemented here.

Run from repo root:
    python -m pytest tests/test_turn_alert.py -q
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = ROOT / "skills" / "dnd" / "display"
HTML = DISPLAY / "templates" / "index.html"
NODE = shutil.which("node")

_SLICE = re.compile(
    r"(function _turnSame\(a, b\).*?)\n// ── end \"Your turn\" decision ──", re.S)

ORDER = ["Goblin 1", "Aldric", "Goblin 2", "Mira"]


def _alert(me, prev, cur):
    if NODE is None:
        raise unittest.SkipTest("node not available")
    m = _SLICE.search(HTML.read_text(encoding="utf-8"))
    if not m:
        raise AssertionError("the turn-alert block is no longer where this test slices it; "
                             "update _SLICE rather than deleting the test")
    script = (m.group(1) + "\nconsole.log(JSON.stringify(_turnAlertFor(%s, %s, %s)));\n"
              % (json.dumps(me), json.dumps(prev), json.dumps(cur)))
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
        f.write(script)
        path = f.name
    try:
        r = subprocess.run([NODE, path], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
        if r.returncode != 0:
            raise AssertionError(f"node failed: {(r.stderr or '').strip()[:800]}")
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        pathlib.Path(path).unlink(missing_ok=True)


def _turn(current, rnd=1, order=ORDER):
    return {"order": order, "current": current, "round": rnd}


class TurnAlertTests(unittest.TestCase):
    def test_alert_when_the_tracker_reaches_me(self):
        self.assertEqual(_alert("Aldric", _turn("Goblin 1"), _turn("Aldric")), "now")

    def test_name_match_ignores_case_and_spaces(self):
        self.assertEqual(_alert(" aldric ", _turn("Goblin 1"), _turn("Aldric")), "now")

    def test_heads_up_one_turn_before(self):
        self.assertEqual(_alert("Aldric", _turn("Mira"), _turn("Goblin 1")), "next")

    def test_heads_up_wraps_to_the_top_of_the_order(self):
        self.assertEqual(_alert("Goblin 1", _turn("Goblin 2"), _turn("Mira")), "next")

    def test_nothing_for_somebody_else(self):
        self.assertEqual(_alert("Aldric", _turn("Aldric"), _turn("Goblin 2")), "")

    def test_resending_the_same_turn_is_not_news(self):
        self.assertEqual(_alert("Aldric", _turn("Aldric"), _turn("Aldric")), "")

    def test_my_turn_again_in_a_new_round_alerts_again(self):
        solo = ["Aldric"]
        self.assertEqual(_alert("Aldric", _turn("Aldric", 1, solo), _turn("Aldric", 2, solo)), "now")

    def test_first_stats_after_a_page_load_alert_if_it_is_my_turn(self):
        self.assertEqual(_alert("Aldric", None, _turn("Aldric")), "now")

    def test_no_heads_up_in_a_duel(self):
        duel = ["Goblin 1", "Aldric"]
        self.assertEqual(_alert("Aldric", _turn("Aldric", 1, duel), _turn("Goblin 1", 1, duel)), "")

    def test_a_screen_bound_to_nobody_is_never_alerted(self):
        self.assertEqual(_alert("", _turn("Goblin 1"), _turn("Aldric")), "")

    def test_no_alert_once_combat_is_over(self):
        self.assertEqual(_alert("Aldric", _turn("Aldric"), None), "")


class TurnAlertWiringTests(unittest.TestCase):
    """The decision is only useful if the stats handler calls it and the strings exist."""

    def test_stats_handler_feeds_the_alert_before_overwriting_the_state(self):
        src = HTML.read_text(encoding="utf-8")
        self.assertRegex(src, r"_turnUpdate\(_currentTurnOrder, merged\);\s*_currentTurnOrder = merged;")
        self.assertIn('id="turn-banner"', src)

    def test_strings_exist_in_every_language(self):
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("turn.yours", "turn.next"):
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
