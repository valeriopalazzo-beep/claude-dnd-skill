"""
test_stronghold.py — the party's own place (DMG 2014 pp. 127-129).

stronghold.py keeps a property the party owns or builds: building cost and
days, daily upkeep with its staff, improvements, a bill every 30 days paid from
the common fund (or a debt), and the "running a business" roll. The calendar
moves it on; downtime's "business" activity rolls it; every screen reads it.

Run from repo root:
    python -m pytest tests/test_stronghold.py -q
"""
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

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"
HTML = DISPLAY / "templates" / "index.html"
NODE = shutil.which("node")
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(SKILL / "scripts"))

import stronghold as sh  # noqa: E402
import treasury  # noqa: E402


def fund_with(gp):
    f = treasury.new_fund()
    if gp:
        treasury.put(f, {"gp": gp})
    return f


class TableTests(unittest.TestCase):
    def test_the_dmg_table(self):
        self.assertEqual((sh.KINDS["tower"]["upkeep"], sh.KINDS["tower"]["build"]), (2500, (15000, 100)))
        self.assertEqual((sh.KINDS["keep"]["skilled"], sh.KINDS["keep"]["untrained"]), (50, 50))
        self.assertEqual((sh.KINDS["farm"]["upkeep"], sh.KINDS["farm"]["build"]), (50, None))
        self.assertEqual(sh.kind_of("Torre"), "tower")
        self.assertEqual(sh.kind_of("tempio piccolo"), "temple_small")
        with self.assertRaises(sh.StrongholdError):
            sh.kind_of("grotta")

    def test_founding(self):
        t = sh.found("Torre di Vallombra", "torre", "Vallombra", build=True)
        self.assertEqual((t["upkeep_cp"], t["skilled"], t["build"]["cost_gp"], t["build"]["days_needed"]), (2500, 10, 15000, 100))
        self.assertFalse(sh.ready(t))
        inn = sh.found("La Rana Ubriaca", "inn_town")
        self.assertTrue(sh.ready(inn))
        with self.assertRaises(sh.StrongholdError):
            sh.found("Baita", "hunting_lodge", build=True)           # not in the building table
        cabin = sh.found("Baita", "hunting_lodge", build=True, cost_gp=800, days=20)
        self.assertEqual(cabin["build"]["days_needed"], 20)
        self.assertEqual(sh.found("Mulino", "custom", upkeep_cp=300)["upkeep_cp"], 300)


class TimeTests(unittest.TestCase):
    def test_works_go_at_a_third_when_nobody_is_there(self):
        t = sh.found("Torre", "tower", build=True)
        sh.pass_days(t, 30)
        self.assertAlmostEqual(t["build"]["days_done"], 10.0)
        t["present"] = True
        news = sh.pass_days(t, 95)
        self.assertEqual(t["build"]["days_done"], 100)
        self.assertEqual(news, ["Torre is finished"])
        self.assertTrue(sh.ready(t))
        self.assertAlmostEqual(t["owed_days"], 5, msg="upkeep counts from the day it stands")

    def test_a_bill_every_thirty_days_from_the_fund_or_a_debt(self):
        inn = sh.found("La Rana", "inn_town")                       # 5 gp a day
        fund = fund_with(200)
        news = sh.pass_days(inn, 31, fund)
        self.assertEqual(news, ["upkeep for 30 days: 150gp paid from the common fund"])
        self.assertEqual((treasury.total_cp(fund), inn["owed_days"]), (5000, 1))
        self.assertEqual(fund["history"][-1]["kind"], "upkeep")
        news = sh.pass_days(inn, 29, fund)
        self.assertIn("NOT paid", news[0])
        self.assertEqual((inn["debt_cp"], inn["unpaid"], treasury.total_cp(fund)), (15000, 1, 5000))
        self.assertEqual(inn["log"][-1]["key"], "bill.unpaid")

    def test_staff_and_improvements_add_to_the_daily_cost(self):
        inn = sh.found("La Rana", "inn_town")
        inn["staff"].append({"name": "Berta", "role": "amministratrice", "wage_cp": 200})
        inn["improvements"].append({"name": "Stalla", "cost_cp": 10000, "days_needed": 10, "days_done": 0.0,
                                    "upkeep_cp": 100, "done": False})
        self.assertEqual(sh.daily_cp(inn), 700)
        inn["present"] = True
        self.assertEqual(sh.pass_days(inn, 10), ["Stalla is finished"])
        self.assertEqual(sh.daily_cp(inn), 800)

    def test_paying_what_is_owed(self):
        inn = sh.found("La Rana", "inn_town")
        sh.pass_days(inn, 40, None)                                  # no fund: the first bill is a debt
        self.assertEqual((inn["debt_cp"], inn["owed_days"]), (15000, 10))
        with self.assertRaises(sh.StrongholdError):
            sh.pay_now(inn, fund_with(100))
        fund = fund_with(300)
        self.assertEqual(sh.pay_now(inn, fund), ["paid 200gp of upkeep from the common fund"])
        self.assertEqual((inn["debt_cp"], inn["unpaid"], inn["owed_days"], treasury.total_cp(fund)), (0, 0, 0, 10000))


class BusinessTests(unittest.TestCase):
    def test_the_table(self):
        cases = [(5, 10, "cost", 7500), (15, 10, "cost", 5000), (25, 10, "cost", 2500), (45, 10, "even", 0),
                 (55, 10, "earn", None), (75, 10, "earn", None), (85, 10, "earn", None)]
        for roll, days, what, cost in cases:
            with self.subTest(roll=roll):
                inn = sh.found("La Rana", "inn_town")
                fund = fund_with(500)
                out = sh.business(inn, days, fund, "Lyra", roll=roll, rng=random.Random(1))
                self.assertEqual(out["total"], roll + days)
                self.assertEqual(out["cost_cp"], cost or 0)
                if what == "earn":
                    self.assertGreater(out["income_gp"], 0)
                    self.assertEqual(out["income_gp"] % 5, 0)
                    self.assertEqual(fund["history"][-1]["kind"], "income")
                if what == "even":
                    self.assertEqual(inn["log"][-1]["key"], "business.even")

    def test_days_count_up_to_thirty_and_debts_weigh(self):
        inn = sh.found("La Rana", "inn_town")
        inn["unpaid"] = 2
        out = sh.business(inn, 45, None, roll=50, rng=random.Random(1))
        self.assertEqual((out["total"], out["malus"]), (60, 20))
        self.assertIn("− 20 (unpaid debts)", out["lines"][0])
        tower = sh.found("Torre", "tower", build=True)
        with self.assertRaises(sh.StrongholdError):
            sh.business(tower, 5, None, roll=50)

    def test_the_business_days_settle_their_upkeep(self):
        inn = sh.found("La Rana", "inn_town")
        sh.pass_days(inn, 12)
        sh.business(inn, 10, fund_with(10), roll=50)
        self.assertEqual(inn["owed_days"], 2)


class CommandTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        self.camp = self.root / "campaigns" / "prova"
        self.camp.mkdir(parents=True)
        (self.root / "rt").mkdir()
        for var, value in (("DND_CAMPAIGN_ROOT", str(self.root)), ("DND_RUNTIME_DIR", str(self.root / "rt"))):
            old = os.environ.get(var)
            os.environ[var] = value
            self.addCleanup(lambda var=var, old=old: os.environ.pop(var, None) if old is None else os.environ.__setitem__(var, old))
        saved = sh._announce
        sh._announce = lambda news=None: None
        self.addCleanup(lambda: setattr(sh, "_announce", saved))

    def run_sh(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sh.main(["--campaign", "prova", *argv])
        return code, out.getvalue() + err.getvalue()

    def test_from_founding_to_the_first_bill(self):
        treasury.save(str(self.camp / "treasury.json"), fund_with(20000))
        code, out = self.run_sh("found", "Torre di Vallombra", "--kind", "tower", "--place", "Vallombra", "--build", "--pay", "fund")
        self.assertEqual(code, 0, out)
        self.assertEqual(treasury.load(str(self.camp / "treasury.json"))["coins"]["gp"], 5000)
        self.assertEqual(self.run_sh("found", "Altra", "--kind", "farm")[0], 1)
        self.assertEqual(self.run_sh("present", "on")[0], 0)
        self.assertEqual(self.run_sh("hire", "Berta", "--role", "castellana", "--wage", "2gp")[0], 0)
        self.assertEqual(self.run_sh("hire", "berta")[0], 1)
        code, out = self.run_sh("improve", "Biblioteca", "--cost", "500gp", "--days", "20", "--pay", "fund")
        self.assertEqual(code, 0, out)
        news = sh.tick("prova", 130)                                  # what calendar.py calls
        self.assertIn("Torre di Vallombra is finished", news)
        self.assertIn("Biblioteca is finished", news)
        data = sh.load(str(self.camp / "stronghold.json"))
        self.assertTrue(sh.ready(data))
        self.assertEqual([e["kind"] for e in treasury.load(str(self.camp / "treasury.json"))["history"]][-1], "upkeep")
        code, out = self.run_sh("show")
        self.assertIn("upkeep 27gp/day", out)
        self.assertIn("Berta (castellana)", out)
        view = sh.public_view(data)
        self.assertEqual((view["ready"], view["daily"], view["staff"][0]["name"]), (True, "27gp", "Berta"))
        self.assertEqual(self.run_sh("close")[0], 0)
        self.assertFalse((self.camp / "stronghold.json").exists())

    def test_a_mistake_changes_nothing(self):
        self.assertEqual(self.run_sh("show")[0], 1)
        code, out = self.run_sh("found", "Torre", "--kind", "tower", "--build", "--pay", "fund")
        self.assertEqual(code, 1)
        self.assertIn("no common fund", out)
        self.assertFalse((self.camp / "stronghold.json").exists())

    def test_the_calendar_and_downtime_reach_it(self):
        cal = importlib.util.spec_from_file_location("calendar_under_test_sh", str(SKILL / "scripts" / "calendar.py"))
        mod = importlib.util.module_from_spec(cal)
        cal.loader.exec_module(mod)
        self.run_sh("found", "La Rana", "--kind", "inn_town")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            mod._keep_stronghold("prova", 30)
        self.assertIn("⚠ Stronghold: upkeep for 30 days: 150gp NOT paid", out.getvalue())
        import downtime
        line = downtime._run_business("prova", {"character": "Lyra", "days": 10, "text": "fallback"})
        self.assertTrue(line.startswith("running La Rana: business for 10 days (Lyra): d100 "), line)
        self.assertIn("business", downtime.ACTIVITIES)

    def test_it_runs_as_a_script(self):
        env = dict(os.environ, PYTHONUTF8="1")
        r = subprocess.run([sys.executable, str(DISPLAY / "stronghold.py"), "kinds"],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn("tower", r.stdout)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_stronghold", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def test_every_screen_reads_it(self):
        m = self.mod
        view = sh.public_view(sh.found("La Rana", "inn_town", "Brezza"))
        saved = (m._public_stronghold, m._broadcast)
        self.addCleanup(lambda: (setattr(m, "_public_stronghold", saved[0]), setattr(m, "_broadcast", saved[1])))
        sent = []
        m._public_stronghold = lambda: view
        m._broadcast = sent.append
        self.assertEqual(self.client.get("/stronghold").get_json()["stronghold"]["place"], "Brezza")
        self.client.post("/stronghold", json={"news": ["La Rana is finished"]})
        self.assertEqual(sent[-1]["stronghold_news"], ["La Rana is finished"])
        self.assertIn("stronghold_changed", m._DM_ENDPOINTS)
        self.assertNotIn("stronghold_get", m._DM_ENDPOINTS)


_SLICE = re.compile(r"(function _strongholdPlan\(sh\).*?)\n// ── end stronghold plan ──", re.S)


class PageTests(unittest.TestCase):
    def test_the_plan_in_italian(self):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the stronghold block is no longer where this test slices it")
        it = json.loads((DISPLAY / "i18n" / "it.json").read_text(encoding="utf-8"))
        tr = ("const window = {_LANG: 'it'};\nconst L = %s;\nfunction _t(k, v, f) { let s = L[k]; if (s === undefined) return f;"
              " for (const n in (v || {})) s = s.split('{' + n + '}').join(v[n]); return s; }\n"
              "function _coinsLocal(t) { return String(t).replace(/(\\d+)(gp|sp)/g, (x, n, u) => n + ' ' + L['coin.' + u]); }\n") % json.dumps(it)
        data = sh.found("Torre di Vallombra", "tower", "Vallombra", build=True)
        sh.pass_days(data, 15)
        data["staff"].append({"name": "Berta", "role": "castellana", "wage_cp": 0})
        sh.note(data, "Berta joins the staff as castellana", "hire.role", name="Berta", role="castellana")
        script = tr + m.group(1) + "\nconsole.log(JSON.stringify(_strongholdPlan(%s)));\n" % json.dumps(sh.public_view(data))
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            plan = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        self.assertEqual(plan["sub"], "Torre fortificata · Vallombra")
        self.assertEqual((plan["build"]["pct"], plan["build"]["text"]), (5, "In costruzione: 5 giorni di lavoro su 100"))
        self.assertIn("un terzo", plan["build"]["away"])
        self.assertEqual(plan["upkeep"], "Mantenimento: 25 mo al giorno")
        self.assertEqual(plan["due"], "", "no bill while it is being built")
        self.assertEqual(plan["people"], ["Berta (castellana)"])
        self.assertEqual(plan["log"], ["Berta entra al servizio come castellana",
                                       "Iniziano i lavori: 15000 mo, 100 giorni con qualcuno del gruppo sul posto"])

    def test_the_page_has_the_panel_and_every_way_in(self):
        src = HTML.read_text(encoding="utf-8")
        for needle in ('id="sh-modal"', 'id="ln-stronghold"', 'data-ln-proxy="ln-stronghold"', 'id="ph-stronghold-opt"',
                       "window._onStronghold(payload)", 'id="ln-i-castle"'):
            self.assertIn(needle, src)

    def test_strings_exist_in_every_language(self):
        src = (DISPLAY / "stronghold.py").read_text(encoding="utf-8")
        log_keys = set(re.findall(r'"((?:begin|possess|finished|paynow(?:\.fund)?|hire(?:\.role)?|fire|improve|business\.even|business\.earn))"', src))
        keys = {"sh.log." + k for k in log_keys} | {"sh.log.bill.paid", "sh.log.bill.unpaid", "sh.log.business.cost.paid",
                                                    "sh.log.business.cost.unpaid", "dt.act.business", "dt.desc.business",
                                                    "dt.detail.business"}
        keys |= {"sh.kind." + k for k in sh.KINDS}
        keys |= set(re.findall(r"_t\('(sh\.[\w.]*\w)'", HTML.read_text(encoding="utf-8")))
        self.assertGreater(len(keys), 40)
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in sorted(keys):
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
