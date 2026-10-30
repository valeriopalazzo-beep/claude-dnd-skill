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
        self.assertEqual(set(view[0]), {"id", "name", "role", "where", "seen", "faction", "look", "attitude", "status", "facts", "said",
                                        "place", "group", "ts", "quests"})
        self.assertEqual((view[0]["attitude"], view[0]["status"], view[0]["said"]), ("", "alive", ["Vi ho dato Tomas."]))

    def test_each_page_is_filed_under_a_place(self):
        places = ["Ferrovalle", "Kaldrheim", "La torre di Dentesecco", "Corvo Basso"]
        rows = []
        pp.meet(rows, "Gigi", where="Il Mantice Rotto", place="ferrovalle")
        pp.meet(rows, "Orm", where="Porta del Corvo, Kaldrheim")
        pp.meet(rows, "Sigrid", where="Torre di Dentesecco")
        page, _ = pp.meet(rows, "Skrag", where="Abeti Neri (solo sentito nominare)")
        page["seen"] = "La torre di Dentesecco"
        pp.meet(rows, "Rosa", where="Il cunicolo dei tubi")
        pp.meet(rows, "Teresa")
        got = {p["name"]: p["group"] for p in pp.public_view(rows, places)}
        self.assertEqual(got, {"Gigi": "Ferrovalle",                       # --place, spelled as on the map
                               "Orm": "Kaldrheim",                         # the map's place named in where they met
                               "Sigrid": "La torre di Dentesecco",         # the article does not count
                               "Skrag": "La torre di Dentesecco",          # else where they were last seen
                               "Rosa": "Il cunicolo dei tubi",             # else where they met, as written
                               "Teresa": ""})

    def test_a_page_lists_the_quests_it_belongs_to(self):
        quests = [{"id": "q1", "name": "Il rogo della strega", "status": "resolved"},
                  {"id": "q2", "name": "La corona di cenere", "status": "active"}]
        rows = []
        page, _ = pp.meet(rows, "Corradi")
        self.assertTrue(pp.link(page, quests[0]))
        self.assertTrue(pp.link(page, quests[1]))
        self.assertFalse(pp.link(page, quests[1]), "linked once")
        page["quests"].append("gone")                                      # a quest removed since
        view = pp.public_view(rows, (), quests)
        self.assertEqual(view[0]["quests"], [{"name": "Il rogo della strega", "status": "resolved"},
                                             {"name": "La corona di cenere", "status": "active"}])
        self.assertTrue(pp.unlink(page, quests[0]))
        self.assertFalse(pp.unlink(page, quests[0]))
        self.assertIn("quests: La corona di cenere", pp.describe(page, quests))


class CardTests(unittest.TestCase):
    """Someone met for the first time — even made up on the spot — gets a card on every screen."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._old = os.environ.get("DND_CAMPAIGN_ROOT")
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        (self.root / "campaigns" / "prova").mkdir(parents=True)
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

    def run_pp(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = pp.main(["--campaign", "prova", *argv])
        return code, out.getvalue() + err.getvalue()

    def test_the_card_of_pippo(self):
        code, out = self.run_pp("meet", "Pippo", "--role", "figlio del cordaio, un Granello", "--where", "Mantice Rotto",
                                "--look", "ragazzino ustionato in faccia, legato col lazo di Berta")
        self.assertEqual(code, 0, out)
        self.assertIn("looks: ragazzino ustionato in faccia", out)
        self.assertEqual(self.events[-1]["card"], {"role": "figlio del cordaio, un Granello",
                                                   "look": "ragazzino ustionato in faccia, legato col lazo di Berta",
                                                   "where": "Mantice Rotto"})
        self.run_pp("seen", "pippo", "Isola dei Ricambi")
        code, out = self.run_pp("card", "pippo")                      # who was that again?
        self.assertEqual(self.events[-1], {"name": "Pippo", "new": False,
                                           "card": {"role": "figlio del cordaio, un Granello",
                                                    "look": "ragazzino ustionato in faccia, legato col lazo di Berta",
                                                    "where": "Isola dei Ricambi"}})
        self.run_pp("set", "pippo", "--look", "un occhio bendato")
        self.assertEqual(pp.find(pp.load(pp.data_path("prova")), "pippo")["look"], "un occhio bendato")
        self.assertEqual(self.run_pp("card", "nessuno")[0], 1)


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
        self.assertEqual(self.events[-1], {"name": "L'Incappucciato", "new": True,
                                           "card": {"role": "adepto", "look": "", "where": "Pozzo del Corvo"}})
        self.assertIn("no --look", out)
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

    def test_place_and_quests_from_the_command_line(self):
        (self.root / "campaigns" / "prova" / "quests.json").write_text(json.dumps({"quests": [
            {"id": "q1", "name": "Lavoro al cantiere della Gilda", "status": "resolved"},
            {"id": "q2", "name": "L'altro capo della catena", "status": "active"}]}), encoding="utf-8")
        code, out = self.run_pp("meet", "Gigi", "--where", "Il Mantice Rotto", "--place", "Ferrovalle", "--quest", "cantiere")
        self.assertEqual(code, 0, out)
        self.assertIn("filed under: Ferrovalle", out)
        self.assertIn("quests: Lavoro al cantiere della Gilda", out)
        self.assertEqual(self.run_pp("link", "gigi", "catena")[0], 0)
        self.assertEqual(self.saved()[0]["quests"], ["q1", "q2"])
        self.assertEqual(self.run_pp("unlink", "gigi", "cantiere")[0], 0)
        self.assertEqual(self.run_pp("set", "gigi", "--place", "Kaldrheim")[0], 0)
        self.assertEqual((self.saved()[0]["place"], self.saved()[0]["quests"]), ("Kaldrheim", ["q2"]))
        before = self.saved()
        for argv in (("link", "gigi", "catena"), ("unlink", "gigi", "cantiere"), ("link", "gigi", "nessuna"),
                     ("meet", "Nuovo", "--quest", "nessuna")):
            code, out = self.run_pp(*argv)
            self.assertEqual(code, 1, argv)
            self.assertIn("Nothing was changed", out)
        self.assertEqual(self.saved(), before)

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
        self.client.post("/people", json={"event": {"name": "Oderico", "new": True,
                                                    "card": {"role": "prete", "look": "vecchio", "where": "Cappella", "x": "<b>"}}})
        self.assertEqual(sent[-1]["people_event"]["card"], {"role": "prete", "look": "vecchio", "where": "Cappella"})
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
        self.assertEqual((first["role"], first["facts"], first["said"], first["look"]), ("prete", ["Uno"], [], ""))
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


class CardPageTests(unittest.TestCase):
    def test_the_card_is_on_the_page_in_every_language(self):
        src = HTML.read_text(encoding="utf-8")
        for needle in ('id="npc-card"', 'id="npc-card-look"', "event.card && showCard(event)", "people-look"):
            self.assertIn(needle, src)
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("people.cardNew", "people.cardAgain", "people.cardWhere", "people.cardOk"):
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


class GroupTests(unittest.TestCase):
    """Fifty names in one list told the table nothing: the panel files them by place or by quest."""

    def test_the_server_files_people_by_the_maps_places_and_the_quests(self):
        spec = importlib.util.spec_from_file_location("dnd_display_app_people_groups", str(DISPLAY / "dnd-display-app.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod._token_ok = lambda: True
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        camp = pathlib.Path(tmp.name)
        rows = []
        page, _ = pp.meet(rows, "Orm", where="Porta del Corvo, Kaldrheim")
        page["quests"] = ["q1"]
        pp.save(str(camp / "people.json"), rows)
        (camp / "region.json").write_text(json.dumps({"places": [{"name": "Kaldrheim"}, {"name": "Ferrovalle"}]}), encoding="utf-8")
        (camp / "quests.json").write_text(json.dumps({"quests": [{"id": "q1", "name": "Il Dente", "status": "active"}]}),
                                          encoding="utf-8")
        sent = []
        mod._active_campaign, mod._find_campaign, mod._broadcast = (lambda: "prova"), (lambda n: camp), sent.append
        got = mod.app.test_client().get("/people").get_json()[0]
        self.assertEqual((got["group"], got["quests"]), ("Kaldrheim", [{"name": "Il Dente", "status": "active"}]))
        mod._adopt_campaign_quests = lambda: {"quests": []}
        self.assertEqual(mod.app.test_client().post("/quests", json={}).status_code, 204)
        self.assertEqual(sent[-1]["people"][0]["name"], "Orm", "a quest that changes refreshes the names under it")

    def test_the_panel_groups(self):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = re.search(r"(function _peopleGroups\(people, by\).*?)\n// ── end people groups ──", HTML.read_text(encoding="utf-8"), re.S)
        self.assertTrue(m, "the people groups block is no longer where this test slices it")
        people = [
            {"name": "Gigi", "group": "Ferrovalle", "ts": 1, "quests": [{"name": "Cantiere", "status": "resolved"}]},
            {"name": "Orm", "group": "Kaldrheim", "ts": 5, "quests": [{"name": "Il Dente", "status": "active"},
                                                                    {"name": "Cantiere", "status": "resolved"}]},
            {"name": "Rosa", "group": "", "ts": 9, "quests": []},
            {"name": "Teresa", "group": "ferrovalle", "ts": 3},
        ]
        script = m.group(1) + "\nconst P = %s;\nconsole.log(JSON.stringify(['place', 'quest', 'name'].map(by => _peopleGroups(P, by)" \
                              ".map(g => [g.label, g.status, g.people.map(p => p.name)]))));\n" % json.dumps(people)
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            place, quest, name = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        self.assertEqual(place, [["Kaldrheim", "", ["Orm"]],                # the latest meeting first
                                 ["Ferrovalle", "", ["Gigi", "Teresa"]],    # one heading whatever the case
                                 ["", "", ["Rosa"]]])                       # nowhere: last
        self.assertEqual(quest, [["Il Dente", "active", ["Orm"]],           # open quests first
                                 ["Cantiere", "resolved", ["Gigi", "Orm"]],  # under every quest they are part of
                                 ["", "", ["Rosa", "Teresa"]]])
        self.assertEqual(name, [["", "", ["Gigi", "Orm", "Rosa", "Teresa"]]])

    def test_the_switch_is_on_the_page_in_every_language(self):
        src = HTML.read_text(encoding="utf-8")
        for needle in ('id="people-by"', 'data-by="place"', 'data-by="quest"', 'data-by="name"', "_peopleGroups(shown, by)"):
            self.assertIn(needle, src)
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("people.by", "people.by.place", "people.by.quest", "people.by.name", "people.noPlace", "people.noQuest"):
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
