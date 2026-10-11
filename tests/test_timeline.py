"""
test_timeline.py — what has happened in the campaign, by the world's dates.

display/timeline.py keeps the events of the story in the order of the
campaign's own calendar, so every player can look up what happened when.

Run from repo root:
    python -m pytest tests/test_timeline.py -q
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

import media  # noqa: E402
import timeline as tl  # noqa: E402

CALENDAR = {"day": 14, "month": 10, "year": 1147, "hour": 17, "month_length": 30, "era": "E.C.",
            "months": ["Gelida", "Fangosa", "Germoglio", "Fioritura", "Verdemaggio", "Spiga", "Solleone", "Mietitura",
                       "Vendemmia", "Brumosa", "Ceneria", "Lungabuio"]}


def _free(date="", at=""):
    return tl.when(date, at)                     # a campaign with no calendar


class EventTests(unittest.TestCase):
    def test_without_a_calendar_the_date_is_free_text_and_the_order_is_the_order_of_arrival(self):
        rows = []
        tl.add(rows, "Secondo", _free("la seconda sera"))
        tl.add(rows, "Primo", _free("la prima sera", "sera"))
        self.assertEqual([e["title"] for e in tl.ordered(rows)], ["Secondo", "Primo"])
        self.assertEqual((rows[1]["date"], rows[1]["abs"], rows[1]["time"]), ("la prima sera", None, "evening"))

    def test_an_event_needs_a_title_and_is_not_written_twice_on_the_same_day(self):
        rows = []
        tl.add(rows, "  Il rogo   fermato ", _free("14 Brumosa"), note="Corradi disarmato", session="3")
        self.assertEqual((rows[0]["title"], rows[0]["note"], rows[0]["session"], rows[0]["secret"]),
                         ("Il rogo fermato", "Corradi disarmato", 3, False))
        tl.add(rows, "Il rogo fermato", _free("15 Brumosa"))                    # another day: another event
        for bad in (("", {}), ("il ROGO fermato", {"stamp": _free("14 Brumosa")}), ("Altro", {"session": "tre"}),
                    ("Altro", {"session": 0})):
            with self.assertRaises(tl.TimelineError):
                tl.add(rows, bad[0], bad[1].get("stamp", _free()), session=bad[1].get("session"))
        with self.assertRaises(tl.TimelineError):
            tl.time_key("dopo cena")
        self.assertEqual(len(rows), 2)

    def test_events_are_found_by_a_part_of_the_title_that_means_one(self):
        rows = []
        tl.add(rows, "Il rogo", _free("14"))
        tl.add(rows, "Il rogo della strega", _free("15"))
        self.assertEqual(tl.find(rows, "strega")["date"], "15")
        self.assertEqual(tl.find(rows, "il rogo")["date"], "14")               # the exact title wins
        for bad in ("rog", "drago", ""):
            with self.assertRaises(tl.TimelineError):
                tl.find(rows, bad)

    def test_the_players_get_what_is_not_secret_oldest_first(self):
        rows = []
        tl.add(rows, "Sera", {"date": "14 Brumosa", "abs": 100, "time": "evening"})
        tl.add(rows, "Segreto", {"date": "13 Brumosa", "abs": 99, "time": ""}, secret=True)
        tl.add(rows, "Mattina", {"date": "14 Brumosa", "abs": 100, "time": "morning"})
        tl.add(rows, "Quel giorno", {"date": "14 Brumosa", "abs": 100, "time": ""})
        tl.add(rows, "Prima", {"date": "2 Brumosa", "abs": 88, "time": "night"})
        tl.add(rows, "Senza data", _free())
        view = tl.public_view(rows)
        self.assertEqual([e["title"] for e in view], ["Prima", "Quel giorno", "Mattina", "Sera", "Senza data"])
        self.assertEqual(set(view[0]), {"id", "title", "note", "date", "abs", "time", "session"})
        self.assertEqual([e["title"] for e in tl.ordered(rows)][1], "Segreto")


class CalendarTests(unittest.TestCase):
    """With calendar.json the dates are the calendar's own."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._old = os.environ.get("DND_CAMPAIGN_ROOT")
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        for name in ("prova", "senza"):
            (self.root / "campaigns" / name).mkdir(parents=True)
        (self.root / "campaigns" / "prova" / "calendar.json").write_text(json.dumps(CALENDAR), encoding="utf-8")
        self.events = []
        self._saved = (tl._announce, media.active_campaign)
        tl._announce = lambda event=None: self.events.append(event)
        media.active_campaign = lambda: "prova"

    def tearDown(self):
        tl._announce, media.active_campaign = self._saved
        if self._old is None:
            os.environ.pop("DND_CAMPAIGN_ROOT", None)
        else:
            os.environ["DND_CAMPAIGN_ROOT"] = self._old
        self._tmp.cleanup()

    def run_tl(self, *argv, campaign="prova"):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = tl.main(["--campaign", campaign, *argv])
        return code, out.getvalue() + err.getvalue()

    def saved(self, campaign="prova"):
        return tl.load(str(self.root / "campaigns" / campaign / "timeline.json"))

    def test_no_date_means_today_at_this_time_of_day(self):
        lib, cal = tl.calendar_of("prova")
        self.assertIsNotNone(lib)
        now = tl.when("", "", lib, cal)
        self.assertEqual((now["date"], now["day"], now["month"], now["year"], now["time"]),
                         ("14 Brumosa 1147 E.C.", 14, 10, 1147, "afternoon"))
        before = tl.when("2 brumosa", "alba", lib, cal)                        # the year is this year's
        self.assertEqual((before["date"], before["time"], now["abs"] - before["abs"]), ("2 Brumosa 1147 E.C.", "dawn", 12))
        self.assertEqual(now["abs"] - tl.when("14 Brumosa 1146", "", lib, cal)["abs"], 360)
        for bad in ("32 Brumosa", "14 Nevoso", "ieri"):
            with self.assertRaises(tl.TimelineError):
                tl.when(bad, "", lib, cal)
        self.assertEqual(tl.calendar_of("senza"), (None, {}))

    def test_the_standard_calendar_module_is_left_alone(self):
        tl.calendar_of("prova")
        import calendar
        self.assertTrue(hasattr(calendar, "monthrange"), "scripts/calendar.py must not take the place of the stdlib's")

    def test_a_story_told_out_of_order_comes_out_in_order(self):
        self.assertEqual(self.run_tl("add", "Il rogo è fermato", "--note", "Corradi disarmato", "--session", "3")[0], 0)
        self.assertEqual(self.events[-1], {"title": "Il rogo è fermato"})
        self.assertEqual(self.run_tl("add", "Arrivo a Vallombra", "--date", "12 Brumosa 1147", "--time", "sera", "--session", "1")[0], 0)
        self.assertEqual(self.run_tl("add", "La Custode scrive al Duca", "--date", "9 Brumosa", "--secret")[0], 0)
        self.assertIsNone(self.events[-1])                                     # a secret is not announced
        code, out = self.run_tl("list")
        self.assertEqual([ln.split("] ")[1] for ln in out.splitlines() if ln.startswith("[")],
                         ["La Custode scrive al Duca  (secret)", "Arrivo a Vallombra", "Il rogo è fermato"])
        self.assertIn("[12 Brumosa 1147 E.C. · evening · session 1]", out)
        self.assertEqual(self.run_tl("reveal", "custode")[0], 0)
        self.assertEqual(self.events[-1], {"title": "La Custode scrive al Duca"})
        self.assertEqual(self.run_tl("set", "rogo", "--date", "13 Brumosa", "--time", "mattina", "--title", "Il rogo")[0], 0)
        rogo = tl.find(self.saved(), "rogo")
        self.assertEqual((rogo["title"], rogo["date"], rogo["time"], rogo["note"]),
                         ("Il rogo", "13 Brumosa 1147 E.C.", "morning", "Corradi disarmato"))
        self.assertEqual(self.run_tl("set", "rogo", "--time", "")[0], 0)
        self.assertEqual(tl.find(self.saved(), "rogo")["time"], "")
        self.assertEqual(self.run_tl("hide", "custode")[0], 0)
        self.assertEqual([e["title"] for e in tl.public_view(self.saved())], ["Arrivo a Vallombra", "Il rogo"])
        self.assertEqual(self.run_tl("remove", "arrivo")[0], 0)
        self.assertEqual(len(self.saved()), 2)

    def test_a_mistake_changes_nothing(self):
        self.run_tl("add", "Il rogo")
        before, announced = self.saved(), len(self.events)
        for argv in (("add", "il rogo"), ("add", "Altro", "--date", "40 Brumosa"), ("add", "Altro", "--time", "tardi"),
                     ("set", "rogo"), ("set", "rogo", "--date", ""), ("set", "rogo", "--title", " "),
                     ("set", "nessuno", "--note", "x"), ("remove", "nessuno"), ("add", "Altro", "--session", "x")):
            code, out = self.run_tl(*argv)
            self.assertEqual(code, 1, argv)
            self.assertIn("Nothing was changed", out)
        self.assertEqual(self.saved(), before)
        self.assertEqual(len(self.events), announced)

    def test_another_campaign_is_written_without_telling_the_display(self):
        self.assertEqual(self.run_tl("add", "Altrove", "--date", "un giorno d'estate", campaign="senza")[0], 0)
        self.assertEqual((self.saved("senza")[0]["date"], self.events), ("un giorno d'estate", []))

    def test_it_runs_as_a_script(self):
        env = dict(os.environ, PYTHONUTF8="1")
        script = str(DISPLAY / "timeline.py")
        r = subprocess.run([sys.executable, script, "--campaign", "senza", "add", "Da riga di comando", "--date", "allora"],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        r = subprocess.run([sys.executable, script, "--campaign", "senza", "list"],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "[allora] Da riga di comando"), r.stderr[-400:])


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_timeline", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def test_every_screen_reads_what_is_not_secret(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        camp = pathlib.Path(tmp.name)
        rows = []
        tl.add(rows, "Il rogo", {"date": "14 Brumosa", "abs": 100, "time": "morning"})
        tl.add(rows, "Segreto", {"date": "13 Brumosa", "abs": 99, "time": ""}, secret=True)
        tl.save(str(camp / "timeline.json"), rows)
        saved = (self.mod._active_campaign, self.mod._find_campaign, self.mod._broadcast)
        self.addCleanup(lambda: setattr(self.mod, "_active_campaign", saved[0]) or setattr(self.mod, "_find_campaign", saved[1])
                        or setattr(self.mod, "_broadcast", saved[2]))
        sent = []
        self.mod._active_campaign, self.mod._find_campaign, self.mod._broadcast = (lambda: "prova"), (lambda n: camp), sent.append
        self.assertEqual([e["title"] for e in self.client.get("/timeline").get_json()], ["Il rogo"])
        self.assertEqual(self.client.post("/timeline", json={}).status_code, 204)
        self.assertEqual([e["title"] for e in sent[-1]["timeline"]], ["Il rogo"])
        self.assertIn("timeline_changed", self.mod._DM_ENDPOINTS)
        self.assertNotIn("timeline_get", self.mod._DM_ENDPOINTS)
        self.mod._active_campaign = lambda: ""
        self.assertEqual(self.client.get("/timeline").get_json(), [])


_SLICE = re.compile(r"(function _timelineDays\(rows, todayAbs, newestFirst\).*?)\n// ── end timeline days ──", re.S)


class PageTests(unittest.TestCase):
    def test_events_are_grouped_by_day_and_say_how_long_ago(self):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the timeline block is no longer where this test slices it")
        rows = [{"id": "a", "title": "Arrivo", "date": "12 Brumosa", "abs": 98}, {"id": "b", "title": "Mattina", "date": "14 Brumosa", "abs": 100},
                {"id": "c", "title": "Sera", "date": "14 Brumosa", "abs": 100}, {"id": "d", "title": "Boh", "date": "", "abs": None}]
        script = m.group(1) + "\nconst r = %s;\nconsole.log(JSON.stringify([_timelineDays(r, 100, false), _timelineDays(r, null, true), _timelineDays(null, 1, true)]));\n" % json.dumps(rows)
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            oldest, newest, none = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        self.assertEqual([(d["date"], d["ago"], [e["title"] for e in d["events"]]) for d in oldest],
                         [("12 Brumosa", 2, ["Arrivo"]), ("14 Brumosa", 0, ["Mattina", "Sera"]), ("", None, ["Boh"])])
        self.assertEqual([(d["date"], d["ago"]) for d in newest], [("", None), ("14 Brumosa", None), ("12 Brumosa", None)])
        self.assertEqual(none, [])

    def test_the_page_has_the_panel_and_every_way_in(self):
        src = HTML.read_text(encoding="utf-8")
        for needle in ('id="tl-modal"', 'id="ln-timeline"', 'data-ln-proxy="ln-timeline"', 'id="ph-timeline-opt"',
                       "payload.timeline &&"):
            self.assertIn(needle, src)

    def test_strings_exist_in_every_language(self):
        keys = ["tl.title", "tl.new", "tl.empty", "tl.noDate", "tl.yesterday", "tl.daysAgo", "tl.session",
                "tl.order.newest", "tl.order.oldest"] + ["time." + k for k in tl.TIMES]
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
