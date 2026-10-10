"""
test_clocks.py — progress clocks the whole table can watch.

display/clock.py keeps clocks (a name, a number of segments, how many are
filled) in <campaign>/clocks.json. The display shows the ones that are not
secret, small in the top bar and large in the Clocks panel, and is told when
one moves.

Run from repo root:
    python -m pytest tests/test_clocks.py -q
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

import clock as ck  # noqa: E402


def _some() -> list:
    clocks = []
    ck.add(clocks, "Il rito della Confraternita", 6, filled=3, kind="pericolo", note="alla luna piena")
    ck.add(clocks, "Scassinare il caveau", 4, kind="goal")
    ck.add(clocks, "I rinforzi arrivano", 8, secret=True)
    return clocks


class ClockTests(unittest.TestCase):
    def test_a_clock_has_a_name_segments_and_a_kind(self):
        clocks = _some()
        self.assertEqual([(c["name"], c["segments"], c["filled"], c["kind"], c["secret"]) for c in clocks],
                         [("Il rito della Confraternita", 6, 3, "danger", False),
                          ("Scassinare il caveau", 4, 0, "goal", False),
                          ("I rinforzi arrivano", 8, 0, "time", True)])
        self.assertEqual(len({c["id"] for c in clocks}), 3)

    def test_what_does_not_make_a_clock_is_refused(self):
        clocks = _some()
        for args, kwargs in ((("", 4), {}), (("Corto", 1), {}), (("Lungo", 13), {}), (("Pieno", 4), {"filled": 5}),
                             (("Strano", 4), {"kind": "banana"}), (("il rito della confraternita", 4), {})):
            with self.assertRaises(ck.ClockError):
                ck.add(clocks, *args, **kwargs)
        self.assertEqual(len(clocks), 3)
        full = [{"name": f"c{i}", "segments": 4, "filled": 0} for i in range(ck.MAX_CLOCKS)]
        with self.assertRaises(ck.ClockError):
            ck.add(full, "uno di troppo", 4)

    def test_ticking_fills_and_stops_at_the_rim(self):
        clocks = _some()
        clock, delta = ck.move(clocks, "rito", by=2)
        self.assertEqual((clock["filled"], delta, ck.is_full(clock)), (5, 2, False))
        clock, delta = ck.move(clocks, "RITO", by=4)
        self.assertEqual((clock["filled"], delta, ck.is_full(clock)), (6, 1, True))
        clock, delta = ck.move(clocks, "rito", by=1)
        self.assertEqual(delta, 0)
        clock, delta = ck.move(clocks, "rito", by=-9)
        self.assertEqual((clock["filled"], delta), (0, -6))
        clock, delta = ck.move(clocks, "caveau", to=3)
        self.assertEqual((clock["filled"], delta), (3, 3))
        with self.assertRaises(ck.ClockError):
            ck.move(clocks, "caveau", to=9)

    def test_a_short_name_must_mean_one_clock(self):
        clocks = _some()
        ck.add(clocks, "Il rito del sangue", 4)
        self.assertEqual(ck.find(clocks, "confraternita")["name"], "Il rito della Confraternita")
        for bad in ("rito", "drago", " "):
            with self.assertRaises(ck.ClockError):
                ck.find(clocks, bad)

    def test_the_players_never_get_a_secret_clock(self):
        view = ck.public_view(_some())
        self.assertEqual([c["name"] for c in view], ["Il rito della Confraternita", "Scassinare il caveau"])
        self.assertEqual(view[0], {"id": view[0]["id"], "name": "Il rito della Confraternita", "segments": 6,
                                   "filled": 3, "kind": "danger", "note": "alla luna piena", "full": False})
        self.assertNotIn("rinforzi", json.dumps(view))
        self.assertIsNone(ck._event(_some()[2], delta=1))


class CommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._old = os.environ.get("DND_CAMPAIGN_ROOT")
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        (self.root / "campaigns" / "prova").mkdir(parents=True)
        self.events = []
        self._announce = ck._announce
        ck._announce = self.events.append

    def tearDown(self):
        ck._announce = self._announce
        if self._old is None:
            os.environ.pop("DND_CAMPAIGN_ROOT", None)
        else:
            os.environ["DND_CAMPAIGN_ROOT"] = self._old
        self._tmp.cleanup()

    def run_ck(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ck.main(["--campaign", "prova", *argv])
        return code, out.getvalue() + err.getvalue()

    def saved(self):
        return ck.load(str(self.root / "campaigns" / "prova" / "clocks.json"))

    def test_clocks_are_kept_with_the_campaign_and_every_change_is_announced(self):
        self.assertEqual(self.run_ck("add", "Il rito", "6", "--kind", "danger", "--note", "alla luna piena")[0], 0)
        self.assertEqual(self.run_ck("add", "I rinforzi", "4", "--secret")[0], 0)
        code, out = self.run_ck("tick", "rito", "2")
        self.assertEqual(code, 0)
        self.assertIn("Il rito: ●●○○○○ 2/6", out)
        self.assertEqual([(c["name"], c["filled"]) for c in self.saved()], [("Il rito", 2), ("I rinforzi", 0)])
        first, hidden, moved = self.events
        self.assertEqual((first["name"], first["new"], first["filled"]), ("Il rito", True, 0))
        self.assertIsNone(hidden)                           # a secret clock announces nothing
        self.assertEqual((moved["filled"], moved["segments"], moved["delta"], moved["full"]), (2, 6, 2, False))

    def test_a_full_clock_tells_the_dm_it_happens_now(self):
        self.run_ck("add", "Il rito", "3", "--filled", "2")
        code, out = self.run_ck("tick", "rito")
        self.assertIn("FULL", out)
        self.assertTrue(self.events[-1]["full"])
        code, out = self.run_ck("tick", "rito")
        self.assertIn("nothing moved", out)
        self.assertIsNone(self.events[-1])

    def test_reveal_hide_remove_and_clear(self):
        self.run_ck("add", "I rinforzi", "4", "--secret")
        self.run_ck("add", "La marea", "5")
        self.run_ck("reveal", "rinforzi")
        self.assertFalse(self.saved()[0]["secret"])
        self.assertTrue(self.events[-1]["new"])
        self.run_ck("hide", "rinforzi")
        self.assertTrue(self.saved()[0]["secret"])
        self.run_ck("back", "marea")
        self.run_ck("set", "marea", "4")
        self.assertEqual(self.saved()[1]["filled"], 4)
        self.assertIn("I rinforzi: ○○○○ 0/4  [time, secret]", self.run_ck("list")[1])
        self.run_ck("remove", "marea")
        self.assertEqual([c["name"] for c in self.saved()], ["I rinforzi"])
        self.run_ck("clear")
        self.assertEqual(self.saved(), [])

    def test_a_mistake_changes_nothing(self):
        self.run_ck("add", "Il rito", "6")
        before, announced = self.saved(), len(self.events)
        for argv in (("tick", "drago"), ("tick", "rito", "0"), ("set", "rito", "9"), ("add", "Il rito", "4"),
                     ("add", "Altro", "40"), ("remove", "drago"), ("reveal", "drago")):
            code, out = self.run_ck(*argv)
            self.assertEqual(code, 1, argv)
            self.assertIn("Nothing was changed", out)
        self.assertEqual(self.saved(), before)
        self.assertEqual(len(self.events), announced)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_clocks", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.camp = pathlib.Path(self._tmp.name) / "prova"
        self.camp.mkdir()
        self._saved = (self.mod._active_campaign, self.mod._find_campaign, self.mod._broadcast)
        self.mod._active_campaign = lambda: "prova"
        self.mod._find_campaign = lambda name: self.camp
        self.sent = []
        self.mod._broadcast = self.sent.append
        ck.save(str(self.camp / "clocks.json"), _some())

    def tearDown(self):
        self.mod._active_campaign, self.mod._find_campaign, self.mod._broadcast = self._saved
        self._tmp.cleanup()

    def test_every_screen_gets_the_public_clocks(self):
        got = self.client.get("/clocks").get_json()
        self.assertEqual([c["name"] for c in got], ["Il rito della Confraternita", "Scassinare il caveau"])

    def test_a_change_is_broadcast_with_what_moved(self):
        event = {"id": "ab", "name": "Il rito della Confraternita", "filled": 4, "segments": 6, "delta": 1,
                 "full": False, "new": False, "secret_note": "x"}
        self.assertEqual(self.client.post("/clocks", json={"event": event}).status_code, 204)
        payload = self.sent[-1]
        self.assertEqual([c["name"] for c in payload["clocks"]], ["Il rito della Confraternita", "Scassinare il caveau"])
        self.assertEqual(payload["clock_event"]["filled"], 4)
        self.assertNotIn("secret_note", payload["clock_event"])
        self.client.post("/clocks", json={})
        self.assertNotIn("clock_event", self.sent[-1])

    def test_no_campaign_or_a_broken_file_is_no_clocks(self):
        (self.camp / "clocks.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(self.client.get("/clocks").get_json(), [])
        self.mod._active_campaign = lambda: ""
        self.assertEqual(self.client.get("/clocks").get_json(), [])

    def test_loading_another_campaign_sends_its_panels_to_the_pages_already_open(self):
        """A page asks for clocks, bestiary, companions and the region map once, when it
        loads. push_stats.py --set-campaign comes later, at /dm:dnd load: without a
        broadcast those panels kept showing the previous campaign's (usually nothing)."""
        m = self.mod
        saved = (m.CAMP_FILE, m.STATS_FILE, m._load_tail, m._apply_campaign_sfx_languages)
        m.CAMP_FILE = str(pathlib.Path(self._tmp.name) / ".campaign")
        m.STATS_FILE = str(pathlib.Path(self._tmp.name) / "stats.json")
        m._load_tail = m._apply_campaign_sfx_languages = lambda: None
        try:
            self.assertEqual(self.client.post("/stats", json={"campaign": "prova"}).status_code, 204)
        finally:
            m.CAMP_FILE, m.STATS_FILE, m._load_tail, m._apply_campaign_sfx_languages = saved
        panels = next(p for p in self.sent if "clocks" in p)
        self.assertEqual([c["name"] for c in panels["clocks"]], ["Il rito della Confraternita", "Scassinare il caveau"])
        self.assertEqual(panels["bestiary"], [])
        self.assertEqual(panels["companions"], [])
        self.assertIn("region_changed", panels)

    def test_only_the_dm_moves_them(self):
        self.assertIn("clocks_changed", self.mod._DM_ENDPOINTS)
        self.assertNotIn("clocks_get", self.mod._DM_ENDPOINTS)


_SLICE = re.compile(r"(function _clockPie\(segments, filled, size\).*?)\n// ── end clock drawing ──", re.S)


class PageTests(unittest.TestCase):
    def test_the_pie_has_one_wedge_per_segment_and_paints_the_filled_ones(self):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the clock-drawing block is no longer where this test slices it")
        script = m.group(1) + "\nconsole.log(JSON.stringify([[6,4],[4,0],[8,8],[5,9],[1,0],[3,-2]].map(a => _clockPie(a[0], a[1], 40))));\n"
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            pies = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        counts = [(p.count('class="on"'), p.count('class="off"')) for p in pies]
        self.assertEqual(counts, [(4, 2), (0, 4), (8, 0), (5, 0), (0, 2), (0, 3)])
        self.assertTrue(all(p.startswith("<svg") and 'width="40"' in p and "NaN" not in p for p in pies))

    def test_the_page_has_the_chip_the_panel_and_every_way_in(self):
        src = HTML.read_text(encoding="utf-8")
        for needle in ('id="clock-chip"', 'id="clock-modal"', 'id="ln-clocks"', 'data-ln-proxy="ln-clocks"',
                       'id="ph-clock-opt"', "['clock-chip', $id('ln-clocks-slot')]",
                       "(payload.clocks || payload.clock_event)"):
            self.assertIn(needle, src)

    def test_strings_exist_in_every_language(self):
        keys = ["clock.title", "clock.empty", "clock.count", "clock.fullWord", "clock.tick", "clock.new", "clock.full"]
        keys += ["clock.kind." + k for k in ck.KINDS]
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
