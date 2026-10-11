"""
test_people.py — the people the party has met, as the party knows them.

display/people.py is the players' notebook of the people in the story: who
someone is, where they met, what the party found out and what was said. It
holds only what the DM writes there as the characters learn it.

Run from repo root:
    python -m pytest tests/test_people.py -q
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
import people as pp  # noqa: E402


class NotebookTests(unittest.TestCase):
    def test_a_first_meeting_opens_a_page_and_a_second_one_says_where(self):
        rows = []
        page, fresh = pp.meet(rows, "  Padre   Oderico ", role="prete di Vallombra", where="Cappella", attitude="amico")
        self.assertTrue(fresh)
        self.assertEqual((page["name"], page["role"], page["where"], page["seen"], page["attitude"], page["status"]),
                         ("Padre Oderico", "prete di Vallombra", "Cappella", "", "friendly", "alive"))
        again, fresh = pp.meet(rows, "oderico", where="Cimitero della Collina")
        self.assertFalse(fresh)
        self.assertIs(again, page)
        self.assertEqual((page["where"], page["seen"], len(rows)), ("Cappella", "Cimitero della Collina", 1))
        for bad in ({"name": ""}, {"name": "Nuovo", "attitude": "tiepido"}):
            with self.assertRaises(pp.PeopleError):
                pp.meet(rows, **bad)
        self.assertEqual(len(rows), 1)

    def test_facts_and_words_are_added_once(self):
        rows = []
        page, _ = pp.meet(rows, "Nando")
        self.assertTrue(pp.learn(page, "C'era nel 1136"))
        self.assertFalse(pp.learn(page, "c'era  nel 1136"))
        self.assertTrue(pp.said(page, "Il sangue preso nutre poco."))
        self.assertFalse(pp.said(page, "il sangue preso nutre poco."))
        self.assertEqual((page["facts"], page["said"]), (["C'era nel 1136"], ["Il sangue preso nutre poco."]))
        with self.assertRaises(pp.PeopleError):
            pp.learn(page, "  ")
        self.assertEqual(pp.drop(page, fact="1136"), "C'era nel 1136")
        self.assertEqual(pp.drop(page, words="sangue"), "Il sangue preso nutre poco.")
        for kwargs in ({}, {"fact": "x", "words": "y"}, {"fact": "niente"}):
            with self.assertRaises(pp.PeopleError):
                pp.drop(page, **kwargs)

    def test_who_they_are_and_what_became_of_them(self):
        rows = []
        page, _ = pp.meet(rows, "Drogo", role="carbonaio")
        self.assertEqual(pp.change(page, attitude="ostile", status="morto", faction="Confraternita", role=None),
                         ["faction", "attitude", "status"])
        self.assertEqual((page["attitude"], page["status"], page["role"]), ("hostile", "dead", "carbonaio"))
        self.assertEqual(pp.change(page, attitude="hostile", status="dead"), [])
        self.assertEqual(pp.change(page, attitude="", status="", role=""), ["role", "attitude", "status"])
        self.assertEqual((page["attitude"], page["status"], page["role"]), ("", "alive", ""))
        for kwargs in ({"attitude": "boh"}, {"status": "svanito nel nulla"}):
            with self.assertRaises(pp.PeopleError):
                pp.change(page, **kwargs)

    def test_a_short_name_must_mean_one_page(self):
        rows = []
        pp.meet(rows, "Bertrand")
        pp.meet(rows, "Ser Bertrand Valtrenna")
        self.assertEqual(pp.find(rows, "valtrenna")["name"], "Ser Bertrand Valtrenna")
        self.assertEqual(pp.find(rows, "bertrand")["name"], "Bertrand")
        for bad in ("bert", "drago", ""):
            with self.assertRaises(pp.PeopleError):
                pp.find(rows, bad)

    def test_the_players_get_the_pages_in_order(self):
        rows = []
        pp.meet(rows, "Oderico", role="prete")
        page, _ = pp.meet(rows, "Ada Brenn")
        pp.said(page, "Vi ho dato Tomas.")
        page["attitude"], page["status"] = "strana", "boh"                    # a file edited by hand
        view = pp.public_view(rows)
        self.assertEqual([p["name"] for p in view], ["Ada Brenn", "Oderico"])
        self.assertEqual(set(view[0]), {"id", "name", "role", "where", "seen", "faction", "attitude", "status", "facts", "said"})
        self.assertEqual((view[0]["attitude"], view[0]["status"], view[0]["said"]), ("", "alive", ["Vi ho dato Tomas."]))


class CommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._old = os.environ.get("DND_CAMPAIGN_ROOT")
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        for name in ("prova", "altra"):
            (self.root / "campaigns" / name).mkdir(parents=True)
        self.events = []
        self._saved = (pp._announce, media.active_campaign)
        pp._announce = lambda event=None: self.events.append(event)
        media.active_campaign = lambda: "prova"

    def tearDown(self):
        pp._announce, media.active_campaign = self._saved
        if self._old is None:
            os.environ.pop("DND_CAMPAIGN_ROOT", None)
        else:
            os.environ["DND_CAMPAIGN_ROOT"] = self._old
        self._tmp.cleanup()

    def run_pp(self, *argv, campaign="prova"):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = pp.main(["--campaign", campaign, *argv])
        return code, out.getvalue() + err.getvalue()

    def saved(self, campaign="prova"):
        return pp.load(str(self.root / "campaigns" / campaign / "people.json"))

    def test_someone_from_the_first_meeting_on(self):
        code, out = self.run_pp("meet", "L'Incappucciato", "--role", "adepto", "--where", "Pozzo del Corvo")
        self.assertEqual(code, 0, out)
        self.assertIn("+ L'Incappucciato — adepto", out)
        self.assertEqual(self.events[-1], {"name": "L'Incappucciato", "new": True})
        self.assertEqual(self.run_pp("learn", "incapp", "Aspetta da undici anni uno del sangue")[0], 0)
        self.assertEqual(self.events[-1], {"name": "L'Incappucciato", "new": False})
        self.run_pp("learn", "incapp", "aspetta da undici anni uno del sangue")
        self.assertIsNone(self.events[-1])                                     # known already: no notice
        self.assertEqual(self.run_pp("said", "incapp", "Il sangue dato nutre tanto.")[0], 0)
        self.assertEqual(self.run_pp("seen", "incapp", "Al palo, nel campo")[0], 0)
        self.assertIsNone(self.events[-1])
        self.assertEqual(self.run_pp("rename", "incapp", "Nando")[0], 0)
        self.assertEqual(self.events[-1], {"name": "Nando", "new": False})
        self.assertEqual(self.run_pp("meet", "nando", "--where", "Rocca Muta", "--attitude", "neutrale")[0], 0)
        self.assertEqual(self.run_pp("set", "nando", "--status", "prigioniero", "--faction", "Confraternita")[0], 0)
        page = self.saved()[0]
        self.assertEqual((page["name"], page["where"], page["seen"], page["attitude"], page["status"], page["faction"]),
                         ("Nando", "Pozzo del Corvo", "Rocca Muta", "neutral", "prisoner", "Confraternita"))
        self.assertEqual((len(page["facts"]), len(page["said"])), (1, 1))
        self.assertIn("Nando — adepto (1 fact(s), 1 quote(s))", self.run_pp("list")[1])
        shown = self.run_pp("show", "nando")[1]
        self.assertIn("[neutral, prisoner]", shown)
        self.assertIn("« Il sangue dato nutre tanto. »", shown)
        self.assertEqual(self.run_pp("drop", "nando", "--said", "sangue")[0], 0)
        self.assertEqual(self.run_pp("forget", "nando")[0], 0)
        self.assertEqual(self.saved(), [])

    def test_a_mistake_changes_nothing(self):
        self.run_pp("meet", "Oderico")
        self.run_pp("meet", "Ada")
        before, announced = self.saved(), len(self.events)
        for argv in (("learn", "nessuno", "x"), ("said", "oderico", " "), ("set", "oderico"), ("set", "oderico", "--status", "boh"),
                     ("rename", "oderico", "ada"), ("seen", "oderico", ""), ("drop", "oderico"), ("forget", "nessuno"),
                     ("meet", "Nuovo", "--attitude", "tiepido")):
            code, out = self.run_pp(*argv)
            self.assertEqual(code, 1, argv)
            self.assertIn("Nothing was changed", out)
        self.assertEqual(self.saved(), before)
        self.assertEqual(len(self.events), announced)

    def test_another_campaign_is_written_without_telling_the_display(self):
        self.assertEqual(self.run_pp("meet", "Altrove", campaign="altra")[0], 0)
        self.assertEqual((len(self.saved("altra")), self.events), (1, []))

    def test_it_runs_as_a_script(self):
        env = dict(os.environ, PYTHONUTF8="1")
        r = subprocess.run([sys.executable, str(DISPLAY / "people.py"), "--campaign", "prova", "list"],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "the party has met nobody yet"), r.stderr[-400:])


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_people", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def test_every_screen_reads_it_and_only_the_dm_writes(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        camp = pathlib.Path(tmp.name)
        rows = []
        page, _ = pp.meet(rows, "Oderico", role="prete")
        pp.said(page, "A me risponde.")
        pp.save(str(camp / "people.json"), rows)
        saved = (self.mod._active_campaign, self.mod._find_campaign, self.mod._broadcast)
        self.addCleanup(lambda: setattr(self.mod, "_active_campaign", saved[0]) or setattr(self.mod, "_find_campaign", saved[1])
                        or setattr(self.mod, "_broadcast", saved[2]))
        sent = []
        self.mod._active_campaign, self.mod._find_campaign, self.mod._broadcast = (lambda: "prova"), (lambda n: camp), sent.append
        got = self.client.get("/people").get_json()
        self.assertEqual((got[0]["name"], got[0]["role"], got[0]["said"]), ("Oderico", "prete", ["A me risponde."]))
        self.assertEqual(self.client.post("/people", json={"event": {"name": "Oderico", "new": True, "x": 1}}).status_code, 204)
        self.assertEqual(sent[-1]["people_event"], {"name": "Oderico", "new": True})
        self.assertEqual(sent[-1]["people"][0]["name"], "Oderico")
        self.assertIn("people_changed", self.mod._DM_ENDPOINTS)
        self.assertNotIn("people_get", self.mod._DM_ENDPOINTS)
        self.mod._active_campaign = lambda: ""
        self.assertEqual(self.client.get("/people").get_json(), [])


_SLICE = re.compile(r"(function _personRows\(p\).*?)\n// ── end people rows ──", re.S)


class PageTests(unittest.TestCase):
    def test_a_page_lists_what_is_known_and_changes_its_mark_when_it_grows(self):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the people block is no longer where this test slices it")
        page = {"name": "Oderico", "role": "prete", "where": "Cappella", "seen": "", "faction": "", "attitude": "friendly",
                "status": "alive", "facts": ["Uno"], "said": []}
        grown = dict(page, said=["Parole"])
        dead = dict(page, status="dead")
        script = m.group(1) + "\nconsole.log(JSON.stringify([%s].map(_personRows).concat([_personRows()])));\n" % ", ".join(
            json.dumps(x) for x in (page, grown, dead))
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            first, second, third, blank = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        self.assertEqual(first["meta"], [{"key": "where", "value": "Cappella"}, {"key": "attitude", "value": "friendly"}])
        self.assertEqual((first["role"], first["facts"], first["said"]), ("prete", ["Uno"], []))
        self.assertEqual(third["meta"][-1], {"key": "status", "value": "dead"})
        self.assertEqual(len({first["sig"], second["sig"], third["sig"]}), 3)
        self.assertEqual((blank["meta"], blank["facts"], blank["said"]), ([], [], []))

    def test_the_page_has_the_panel_and_every_way_in(self):
        src = HTML.read_text(encoding="utf-8")
        for needle in ('id="people-modal"', 'id="ln-people"', 'data-ln-proxy="ln-people"', 'id="ph-people-opt"',
                       "(payload.people || payload.people_event)"):
            self.assertIn(needle, src)

    def test_strings_exist_in_every_language(self):
        keys = ["people.title", "people.all", "people.new", "people.filter", "people.empty", "people.noMatch", "people.nothing",
                "people.where", "people.seen", "people.faction", "people.attitude", "people.status", "people.facts",
                "people.said", "people.toastNew", "people.toastMore"]
        keys += ["people.att." + a for a in pp.ATTITUDES] + ["people.st." + s for s in pp.STATUSES if s != "alive"]
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
