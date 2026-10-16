"""
test_supplies.py — food, water and light, spent as the hours go by.

scripts/supplies.py keeps the party's stock in <campaign>/supplies.json.
Lit torches and lanterns burn down as calendar.py moves the clock; while the
party is on the march, each new day eats a ration and a day of water per
mouth, and a shortage is reported with the rule that applies. Every screen
has a Supplies panel.

Run from repo root:
    python -m pytest tests/test_supplies.py -q
"""
import contextlib
import importlib.util
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
DISPLAY = SKILL / "display"
sys.path.insert(0, str(SKILL / "scripts"))

import supplies as sp  # noqa: E402


def _stock(**kw):
    data = sp.blank()
    data["stock"].update(kw)
    return data


class StockTests(unittest.TestCase):
    def test_words_and_spans_are_read_in_english_and_italian(self):
        self.assertEqual(sp.pairs(["razioni", "30", "acqua", "20", "torches", "4"]),
                         [("rations", 30), ("water", 20), ("torches", 4)])
        self.assertEqual([sp.parse_span(t) for t in ("90m", "3h", "2d", "1,5 ore", "2 giorni")], [90, 180, 2880, 90, 2880])
        for bad in (["razioni"], ["oro", "3"], ["acqua", "tanta"], ["acqua", "-1"]):
            with self.subTest(bad=bad), self.assertRaises(sp.SupplyError):
                sp.pairs(bad)
        with self.assertRaises(sp.SupplyError):
            sp.parse_span("un po'")

    def test_a_torch_burns_an_hour_and_the_next_one_is_lit(self):
        data = _stock(torches=2)
        sp.light(data, "torcia", keep=True)
        self.assertEqual((data["stock"]["torches"], data["lights"][0]["left"]), (1, 60))
        self.assertEqual(sp.burn(data, 40), [])
        news = sp.burn(data, 40)                                  # 80 minutes: the second torch is lit
        self.assertEqual((data["stock"]["torches"], data["lights"][0]["left"]), (0, 40))
        self.assertIn("another one is lit", news[0])
        news = sp.burn(data, 60)
        self.assertEqual(data["lights"], [])
        self.assertTrue(any(n.startswith("NO LIGHT") for n in news))
        with self.assertRaises(sp.SupplyError):
            sp.light(data, "torch")

    def test_a_lantern_burns_six_hours_on_a_flask(self):
        data = _stock(oil=1, torches=1)
        sp.light(data, "lanterna")
        sp.light(data, "torch")
        sp.burn(data, 120)
        self.assertEqual([(l["kind"], l["left"]) for l in data["lights"]], [("lantern", 240)])
        self.assertEqual(sp.douse(data, "all"), 1)

    def test_each_day_eats_per_mouth_and_hot_weather_drinks_double(self):
        data = _stock(rations=12, water=12)
        data["mouths"] = 4
        self.assertEqual(sp.days_left(data), 3)
        self.assertEqual(sp.eat(data, 1), [])
        data["hot"] = True
        self.assertEqual(sp.days_left(data), 1)
        self.assertEqual(sp.eat(data, 1), ["supplies for 0.0 more day(s) at this pace"])
        self.assertEqual((data["stock"]["rations"], data["stock"]["water"]), (4, 0))

    def test_a_shortage_gives_the_rule(self):
        data = _stock(rations=3, water=1)
        data["mouths"] = 4
        news = sp.eat(data, 1)
        self.assertTrue(news[0].startswith("FOOD: only 3 of 4"))
        self.assertIn("3 + CON modifier days", news[0])
        self.assertTrue(news[1].startswith("WATER: only 1 of 4"))
        self.assertIn("DC 15 Constitution save", news[1])
        news = sp.eat(data, 1)
        self.assertIn("day 2 without enough food", news[0])
        self.assertIn("exhaustion", news[1])
        self.assertEqual(data["hungry_days"], 2)


class CommandTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        (self.root / "campaigns" / "prova").mkdir(parents=True)
        (self.root / "rt").mkdir()
        (self.root / "rt" / ".campaign").write_text("prova", encoding="utf-8")
        for var, value in (("DND_CAMPAIGN_ROOT", str(self.root)), ("DND_RUNTIME_DIR", str(self.root / "rt"))):
            old = os.environ.get(var)
            os.environ[var] = value
            self.addCleanup(lambda var=var, old=old: os.environ.pop(var, None) if old is None else os.environ.__setitem__(var, old))
        self.sent = []
        saved = sp._announce
        sp._announce = lambda data, news=None: self.sent.append((sp.public_view(data), news))
        self.addCleanup(lambda: setattr(sp, "_announce", saved))

    def run_sp(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sp.main(["-c", "prova", *argv])
        return code, out.getvalue() + err.getvalue()

    def saved(self):
        return sp.load(str(self.root / "campaigns" / "prova" / "supplies.json"))

    def test_from_the_first_count_to_the_dark(self):
        self.assertEqual(self.run_sp("set", "razioni", "20", "acqua", "10", "torce", "1")[0], 0)
        self.assertEqual(self.run_sp("mouths", "5")[0], 0)
        self.assertEqual(self.run_sp("add", "torches", "1")[0], 0)
        self.assertEqual(self.run_sp("use", "rations", "5")[0], 0)
        self.assertEqual(self.run_sp("light", "torch", "--keep")[0], 0)
        code, out = self.run_sp("pass", "3h")
        self.assertEqual(code, 0, out)
        self.assertIn("⚠ NO LIGHT", out)
        data = self.saved()
        self.assertEqual((data["stock"], data["mouths"], data["lights"]),
                         ({"rations": 15, "water": 10, "torches": 0, "oil": 0, "candles": 0}, 5, []))
        self.assertEqual(self.sent[-1][0]["stock"]["torches"], 0)
        self.assertTrue(self.sent[-1][1])

    def test_a_mistake_changes_nothing(self):
        self.run_sp("set", "rations", "2")
        before = self.saved()
        for argv in (("use", "rations", "3"), ("light", "torch"), ("light", "falò"), ("douse", "torch"),
                     ("mouths", "99"), ("day", "0"), ("set", "oro", "3"), ("pass", "boh")):
            code, out = self.run_sp(*argv)
            self.assertEqual(code, 1, argv)
            self.assertIn("Nothing was changed", out)
        self.assertEqual(self.saved(), before)

    def test_the_calendar_spends_them_only_on_the_march(self):
        self.assertEqual(sp.tick("prova", 600, 1), [])           # no supplies.json: nothing at all
        self.assertFalse((self.root / "campaigns" / "prova" / "supplies.json").exists())
        self.run_sp("set", "rations", "10", "water", "10", "torches", "1")
        self.run_sp("mouths", "4")
        self.run_sp("light", "torch")
        news = sp.tick("prova", 30, 1)
        self.assertEqual(news, [])
        self.assertEqual((self.saved()["stock"]["rations"], self.saved()["lights"][0]["left"]), (10, 30))
        self.run_sp("march", "on")
        news = sp.tick("prova", 24 * 60, 2)
        data = self.saved()
        self.assertEqual((data["stock"]["rations"], data["stock"]["water"]), (2, 2))
        self.assertIn("the torch goes out", news)

    def test_calendar_advance_calls_it(self):
        cal = importlib.util.spec_from_file_location("calendar_under_test_supplies", str(SKILL / "scripts" / "calendar.py"))
        mod = importlib.util.module_from_spec(cal)
        cal.loader.exec_module(mod)
        self.run_sp("set", "torches", "1")
        self.run_sp("light", "torch")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            mod._spend_supplies("prova", 2, 0)
        self.assertIn("Supplies:", out.getvalue())
        self.assertEqual(self.saved()["lights"], [])

    def test_it_runs_as_a_script(self):
        env = dict(os.environ, PYTHONUTF8="1")
        r = subprocess.run([sys.executable, str(SKILL / "scripts" / "supplies.py"), "-c", "prova", "show"],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn("rations 0", r.stdout)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_supplies", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.camp = pathlib.Path(tmp.name)
        m = self.mod
        saved = (m._active_campaign, m._find_campaign, m._broadcast)
        self.addCleanup(lambda: (setattr(m, "_active_campaign", saved[0]), setattr(m, "_find_campaign", saved[1]),
                                 setattr(m, "_broadcast", saved[2])))
        self.sent = []
        m._active_campaign, m._find_campaign, m._broadcast = (lambda: "prova"), (lambda n: self.camp), self.sent.append

    def test_every_screen_gets_the_stock_and_the_news(self):
        self.assertIsNone(self.client.get("/supplies").get_json())
        data = _stock(rations=8, water=8)
        data["mouths"] = 4
        sp.save(str(self.camp / "supplies.json"), data)
        self.assertEqual(self.client.get("/supplies").get_json()["days_left"], 2)
        r = self.client.post("/supplies", json={"news": ["FOOD: only 3 of 4"], "x": 1})
        self.assertEqual(r.status_code, 204)
        self.assertEqual((self.sent[-1]["supplies"]["stock"]["rations"], self.sent[-1]["supplies_news"]), (8, ["FOOD: only 3 of 4"]))
        self.assertIn("supplies_changed", self.mod._DM_ENDPOINTS)


class PageTests(unittest.TestCase):
    def test_the_page_has_the_panel(self):
        html = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")
        for needle in ('id="sup-modal"', 'id="ln-supplies"', 'id="ph-supplies-opt"', "window._onSupplies("):
            self.assertIn(needle, html)

    def test_strings_exist_in_every_language(self):
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("sup.title", "sup.days", "sup.rations", "sup.water", "sup.torches", "sup.toast.food", "sup.toast.dark"):
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
