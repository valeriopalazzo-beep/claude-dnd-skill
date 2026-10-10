"""
test_calendar.py — seasons, moon, weather and coming events on the calendar.

scripts/calendar.py keeps the in-world date and now also the season, the moon
phase, the weather (rolled or set) and the events to come, and sends all of it
to the display with the world clock. The page draws the month from that
payload; its moon maths (`_calMoon`) is run through node against the real
template and must agree with the script's.

Run from repo root:
    python -m pytest tests/test_calendar.py -q
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "dnd" if (ROOT / "skills" / "dnd").is_dir() else ROOT
DISPLAY = SKILL / "display"
HTML = DISPLAY / "templates" / "index.html"
NODE = shutil.which("node")
sys.path.insert(0, str(SKILL / "scripts"))

MONTHS = "Gelida,Fangosa,Germoglio,Fioritura,Verdemaggio,Spiga,Solleone,Mietitura,Vendemmia,Brumosa,Ceneria,Lungabuio"
SEASONS = ("Inverno:Lungabuio,Gelida,Fangosa;Primavera:Germoglio,Fioritura,Verdemaggio;"
           "Estate:Spiga,Solleone,Mietitura;Autunno:Vendemmia,Brumosa,Ceneria")


def _load():
    # Not `import calendar`: that is the standard library's.
    spec = importlib.util.spec_from_file_location("dnd_calendar_under_test", SKILL / "scripts" / "calendar.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CalendarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cal = _load()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._old = os.environ.get("DND_CAMPAIGN_ROOT")
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        (self.root / "campaigns" / "prova").mkdir(parents=True)
        self.pushed = []
        self.cal._push_world = lambda campaign, cal: self.pushed.append(self.cal.world_time(cal))
        self.cal._send_date = lambda cal: None
        self.run_cal("init", "--date", "14 Brumosa 1147", "--time", "dawn", "--months", MONTHS,
                     "--month-length", "30", "--day-names", "Lun,Mar,Mer,Gio,Ven,Sab,Dom", "--era", "E.C.",
                     "--seasons", SEASONS, "--moon", "Vela:29", "--full-moon", "16 Brumosa 1147")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("DND_CAMPAIGN_ROOT", None)
        else:
            os.environ["DND_CAMPAIGN_ROOT"] = self._old
        self._tmp.cleanup()

    def run_cal(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                self.cal.main(["-c", "prova", *argv])
            except SystemExit as e:
                code = e.code or 0
        return code, out.getvalue() + err.getvalue()

    def state(self):
        return self.cal._load("prova")

    # ── season, moon ──────────────────────────────────────────────────────

    def test_the_season_is_the_one_holding_the_month_and_its_kind_is_read_from_the_name(self):
        season = self.cal._season(self.state())
        self.assertEqual((season["name"], season["kind"]), ("Autunno", "autumn"))
        self.run_cal("set", "3 Gelida 1148")
        self.assertEqual(self.cal._season(self.state())["kind"], "winter")

    def test_a_season_with_an_unknown_name_takes_its_kind_after_an_equals_sign(self):
        code, _ = self.run_cal("setup", "--seasons", "Le Piogge=spring:Germoglio,Fioritura;Il Secco=summer:Spiga")
        self.assertEqual(code, 0)
        self.assertEqual([(s["name"], s["kind"], s["months"]) for s in self.state()["seasons"]],
                         [("Le Piogge", "spring", [3, 4]), ("Il Secco", "summer", [6])])
        for bad in ("Niente", "Buio=night:Gelida", "Inverno:Nonesiste", "A:Gelida;B:Gelida"):
            self.assertEqual(self.run_cal("setup", "--seasons", bad)[0], 1, bad)
        self.assertEqual(len(self.state()["seasons"]), 2)

    def test_the_moon_is_full_on_its_night_and_every_cycle_after(self):
        cal = self.state()
        self.assertEqual(self.cal._moon(cal), {"name": "Vela", "phase": "waxing_gibbous", "days_to_full": 2})
        full = self.cal._abs_day(cal, 16, 10, 1147)
        phases = [self.cal._moon(cal, full + n)["phase"] for n in range(30)]
        self.assertEqual(phases[0], "full")
        self.assertEqual(phases[29], "full")
        self.assertEqual(phases.count("full"), 2)
        self.assertEqual(phases[14], "new")
        self.assertEqual(phases.count("new"), 1)
        self.assertEqual(phases[7], "last_quarter")
        self.assertEqual(phases[22], "first_quarter")
        self.assertEqual(self.cal._moon(cal, full - 1)["days_to_full"], 1)

    def test_no_moon_no_phase(self):
        cal = self.state()
        cal.pop("moon")
        self.assertEqual(self.cal._moon(cal), {})
        self.assertNotIn("moon", self.cal.world_time(cal))

    # ── events ────────────────────────────────────────────────────────────

    def test_events_are_listed_soonest_first_with_the_days_to_go(self):
        self.run_cal("event", "add", "Notte delle Lanterne Basse", "30 Brumosa", "--yearly", "--note", "festa di Morwen")
        self.run_cal("event", "add", "Dieta d'Inverno", "21 Lungabuio")
        self.run_cal("event", "add", "Il rito", "16 Brumosa 1147", "--secret")
        self.run_cal("event", "add", "Fiera di primavera", "2 Germoglio")       # already past this year → next year
        events = self.cal._events(self.state())
        self.assertEqual([(e["name"], e["in_days"]) for e in events],
                         [("Il rito", 2), ("Notte delle Lanterne Basse", 16), ("Dieta d'Inverno", 67),
                          ("Fiera di primavera", 138)])
        self.assertEqual(self.state()["events"][-1]["year"], 1148)

    def test_the_players_never_receive_a_secret_event(self):
        self.run_cal("event", "add", "Il rito", "16 Brumosa 1147", "--secret")
        self.run_cal("event", "add", "Mercato", "20 Brumosa")
        names = [e["name"] for e in self.pushed[-1]["calendar"]["events"]]
        self.assertEqual(names, ["Mercato"])
        self.assertNotIn("Il rito", json.dumps(self.pushed[-1], ensure_ascii=False))

    def test_a_yearly_event_comes_back_and_a_dated_one_does_not(self):
        self.run_cal("event", "add", "Lanterne", "30 Brumosa", "--yearly")
        self.run_cal("event", "add", "Dieta", "21 Lungabuio 1147")
        self.run_cal("advance", "10", "weeks")
        events = {e["name"]: e["in_days"] for e in self.cal._events(self.state())}
        self.assertGreater(events["Lanterne"], 250)
        self.assertLess(events["Dieta"], 0)
        self.assertEqual([e["name"] for e in self.pushed[-1]["calendar"]["events"]], ["Lanterne"])

    def test_advancing_says_what_the_day_brings(self):
        self.run_cal("event", "add", "Il rito", "16 Brumosa 1147", "--secret")
        self.run_cal("event", "add", "Lanterne", "30 Brumosa", "--yearly")
        _, out = self.run_cal("advance", "2", "days")
        self.assertIn("TODAY: Il rito", out)
        self.assertIn("Vela is full tonight", out)
        _, out = self.run_cal("advance", "3", "weeks")
        self.assertIn("PASSED while time advanced: Lanterne", out)
        _, out = self.run_cal("advance", "2", "hours")
        self.assertNotIn("PASSED", out)

    def test_event_mistakes_change_nothing(self):
        before = self.state()
        for argv in (("event", "add", "X", "40 Brumosa"), ("event", "add", "X", "3 Nonesiste"),
                     ("event", "add", "X"), ("event", "remove", "Nessuno"), ("event", "add", " ", "3 Brumosa")):
            code, out = self.run_cal(*argv)
            self.assertEqual(code, 1, argv)
            self.assertIn("Nothing was changed", out)
        self.assertEqual(self.state(), before)

    # ── weather ───────────────────────────────────────────────────────────

    def test_weather_is_rolled_from_the_season(self):
        def rolls(month, climate="temperate"):
            cal = self.state()
            cal.update(month=month, climate=climate)
            return [self.cal._roll_weather(cal, random.Random(s)) for s in range(300)]
        winter, summer = rolls(1), rolls(7)
        for w in winter + summer:
            self.assertIn(w["key"], self.cal.WEATHER_KEYS)
            self.assertIn(w["temp"], self.cal.TEMP_KEYS)
            if w["key"] == "snow":
                self.assertIn(w["temp"], ("freezing", "cold"))
        self.assertTrue(any(w["key"] == "snow" for w in winter))
        self.assertFalse(any(w["key"] == "snow" for w in summer))
        self.assertFalse(any(w["temp"] in ("freezing", "cold") for w in summer))
        wet = lambda ws: sum(w["key"] in ("rain", "storm", "fog", "snow") for w in ws)
        self.assertLess(wet(rolls(4, "arid")), wet(rolls(4)) / 2)
        self.assertEqual(self.cal._roll_weather(self.state(), random.Random(5)),
                         self.cal._roll_weather(self.state(), random.Random(5)))

    def test_weather_set_by_hand_keeps_the_dm_words_and_a_key_for_the_icon(self):
        code, _ = self.run_cal("weather", "set", "fog", "cold", "--text", "nebbia che non si alza")
        self.assertEqual(code, 0)
        wt = self.pushed[-1]
        self.assertEqual((wt["weather_key"], wt["temp_key"], wt["weather_text"]), ("fog", "cold", "nebbia che non si alza"))
        self.assertEqual(wt["weather"], "fog, cold")          # what the sky and older pages read
        code, out = self.run_cal("weather", "set", "tornado")
        self.assertEqual(code, 1)
        self.assertIn("Nothing was changed", out)
        self.assertEqual(self.state()["weather"]["key"], "fog")

    def test_automatic_weather_rolls_on_a_new_day_only(self):
        self.run_cal("weather", "auto", "on")
        first = self.state()["weather"]
        self.assertTrue(first["auto"] and first["key"])
        self.run_cal("advance", "3", "hours")
        self.assertEqual(self.state()["weather"], first)
        _, out = self.run_cal("advance", "1", "day")
        self.assertIn("Weather today:", out)
        self.assertEqual(self.state()["weather"]["day"], first["day"] + 1)
        self.run_cal("weather", "auto", "off")
        self.run_cal("weather", "set", "rain")
        _, out = self.run_cal("advance", "1", "day")
        self.assertEqual(self.state()["weather"]["key"], "rain")
        self.assertIn("still set to", out)

    # ── what the display gets ─────────────────────────────────────────────

    def test_every_change_sends_the_clock_and_the_calendar(self):
        n = len(self.pushed)
        for argv in (("advance", "1", "hour"), ("rest", "short"), ("time", "night"), ("set", "20 Brumosa 1147", "midday"),
                     ("weather", "roll", "--seed", "1"), ("event", "add", "Mercato", "25 Brumosa"), ("push",)):
            self.run_cal(*argv)
            n += 1
            self.assertEqual(len(self.pushed), n, argv)
        wt = self.pushed[-1]
        self.assertEqual(wt["date"], "20 Brumosa 1147 E.C.")
        self.assertEqual((wt["time"], wt["day_name"], wt["season"]), ("midday", "Sab", "Autunno"))
        self.assertEqual(wt["calendar"]["months"], MONTHS.split(","))
        self.assertEqual((wt["calendar"]["day"], wt["calendar"]["month"], wt["calendar"]["year"]), (20, 10, 1147))
        self.assertEqual(wt["calendar"]["moon"]["cycle"], 29)

    def test_hours_map_to_the_words_the_sky_understands(self):
        keys = [self.cal._tod_key(h) for h in (0, 4, 5, 6, 7, 12, 15, 18, 19, 21, 23)]
        self.assertEqual(keys, ["night", "night", "dawn", "dawn", "morning", "midday", "afternoon",
                                "dusk", "evening", "night", "night"])

    def test_another_campaign_on_the_display_is_left_alone(self):
        rt = self.root / "rt"
        rt.mkdir()
        old = os.environ.get("DND_RUNTIME_DIR")
        os.environ["DND_RUNTIME_DIR"] = str(rt)
        try:
            self.assertTrue(self.cal._is_active("prova"))            # nothing on the display
            (rt / ".campaign").write_text("altra", encoding="utf-8")
            self.assertFalse(self.cal._is_active("prova"))
            (rt / ".campaign").write_text("prova", encoding="utf-8")
            self.assertTrue(self.cal._is_active("prova"))
        finally:
            os.environ["DND_RUNTIME_DIR"] = old

    def test_a_calendar_from_before_still_works(self):
        (self.root / "campaigns" / "vecchia").mkdir()
        (self.root / "campaigns" / "vecchia" / "calendar.json").write_text(json.dumps({
            "day": 29, "month": 2, "year": 5, "hour": 22, "months": ["Uno", "Due"], "month_length": 30,
            "day_names": [], "events": [{"name": "Festa", "date": "1 Uno 6"}]}), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cal.main(["-c", "vecchia", "advance", "2", "days"])
            self.cal.main(["-c", "vecchia", "events"])
        self.assertIn("1 Uno 6", out.getvalue())
        self.assertIn("TODAY: Festa", out.getvalue())
        cal = self.cal._load("vecchia")
        self.assertEqual((cal["day"], cal["month"], cal["year"]), (1, 1, 6))
        wt = self.cal.world_time(cal)
        self.assertEqual((wt["season"], wt["weather_key"]), ("", ""))


_SLICE = re.compile(r"(function _calAbs\(c, day, month, year\).*?)\n// ── end calendar maths ──", re.S)


class PageTests(unittest.TestCase):
    def test_the_page_computes_the_same_moon_as_the_script(self):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        cal = _load()
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the calendar maths block is no longer where this test slices it")
        for cycle, full in ((29, 413205), (28, 7), (5, 2), (30, 100000)):
            state = {"months": MONTHS.split(","), "month_length": 30, "moon": {"name": "Vela", "cycle": cycle, "full": full}}
            days = list(range(full - 40, full + 45))
            script = (m.group(1) + "\nconst c = %s;\nconsole.log(JSON.stringify(%s.map(d => { const x = _calMoon(c, d); "
                      "return [x.phase, x.toFull]; })));\n" % (json.dumps(state), json.dumps(days)))
            with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
                f.write(script)
                path = f.name
            try:
                r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
                self.assertEqual(r.returncode, 0, r.stderr[:500])
                page = json.loads(r.stdout.strip().splitlines()[-1])
            finally:
                pathlib.Path(path).unlink(missing_ok=True)
            script_side = [[cal._moon(state, d)["phase"], cal._moon(state, d)["days_to_full"]] for d in days]
            self.assertEqual(page, script_side, f"cycle {cycle}")

    def test_the_page_has_the_calendar_and_every_way_to_open_it(self):
        src = HTML.read_text(encoding="utf-8")
        for needle in ('id="cal-modal"', 'id="ln-cal"', 'data-ln-proxy="ln-cal"', 'id="ph-cal-opt"',
                       "window._onWorldTime", "['ln-cal', 'ph-cal-opt', 'world-clock']"):
            self.assertIn(needle, src)

    def test_words_exist_in_every_language(self):
        cal = _load()
        keys = ["cal.title", "cal.upcoming", "cal.todayWord", "cal.tomorrow", "cal.inDays", "cal.fullIn", "cal.fullIn1"]
        keys += ["weather." + k for k in cal.WEATHER_KEYS] + ["temp." + k for k in cal.TEMP_KEYS]
        keys += ["moon." + k for k in cal._MOON_PHASES] + ["time." + k for _, k in cal._TOD_KEYS]
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
