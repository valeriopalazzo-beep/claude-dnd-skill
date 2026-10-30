"""
test_quests.py — the party's quests, kept with the campaign.

display/quests.py keeps the quests in <campaign>/quests.json instead of a list
pushed by hand at every load: a change reaches every screen, and loading the
campaign brings them back.

Run from repo root:
    python -m pytest tests/test_quests.py -q
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
HTML = DISPLAY / "templates" / "index.html"
sys.path.insert(0, str(DISPLAY))

import media  # noqa: E402
import quests as qs  # noqa: E402


class QuestTests(unittest.TestCase):
    def test_a_new_quest_keeps_what_it_was_given(self):
        rows = []
        q = qs.add(rows, "  La veglia   di Oderico ", giver="Padre Oderico", location="Collina",
                   objectives=["Vegliare una notte", "vegliare UNA notte"], clues=["Dicono «Ausgar»"])
        self.assertEqual((q["name"], q["status"], q["giver"], q["reward"]), ("La veglia di Oderico", "active", "Padre Oderico", ""))
        self.assertEqual(q["objectives"], [{"text": "Vegliare una notte", "done": False}])     # the same step twice is one step
        self.assertEqual(q["clues"], ["Dicono «Ausgar»"])
        for bad in (("la VEGLIA di oderico", {}), ("", {}), ("Altra", {"status": "sospesa"})):
            with self.assertRaises(qs.QuestError):
                qs.add(rows, bad[0], **bad[1])
        self.assertEqual(len(rows), 1)

    def test_the_dm_is_reminded_of_every_open_step(self):
        rows = []
        qs.add(rows, "Il lampionaio", objectives=["Raggiungere Via dei Lampioni", "Decidere cosa fare di Tobia"])
        qs.mark(rows[0], "Raggiungere")
        qs.add(rows, "Il cantiere", objectives=["Presentarsi all'alba"])
        qs.mark(rows[1], "Presentarsi")
        qs.add(rows, "Vecchia storia", status="resolved", objectives=["Fatto"])
        qs.add(rows, "Il sesto nome", status="threat")
        line = qs.reminder(rows)
        self.assertTrue(line.startswith("[[QUESTS — still open: «Il lampionaio» → Decidere cosa fare di Tobia |"))
        self.assertNotIn("Raggiungere", line)                                   # ticked steps are not repeated
        self.assertIn("«Il cantiere» → no step left open: resolve it", line)    # all done, quest still open
        self.assertIn("«Il sesto nome»", line)                                  # threats are open too
        self.assertNotIn("Vecchia storia", line)
        self.assertIn("quests.py done", line)
        self.assertEqual(qs.reminder([]), "")
        for script in ("check_input.py", "autorun_wait.py"):
            self.assertIn("reminder_line", (DISPLAY / script).read_text(encoding="utf-8"))

    def test_fields_change_empty_and_rename(self):
        rows = []
        q = qs.add(rows, "Il sesto nome", status="threat", deadline="Luna piena")
        qs.add(rows, "La veglia")
        self.assertEqual(qs.change(rows, q, summary="Manca un sesto", deadline="", giver=None), ["summary", "deadline"])
        self.assertEqual((q["summary"], q["deadline"]), ("Manca un sesto", ""))
        self.assertEqual(qs.change(rows, q, summary="Manca un sesto"), [])                     # nothing new
        self.assertEqual(qs.change(rows, q, name="Il sesto sangue", status="resolved"), ["name", "status"])
        with self.assertRaises(qs.QuestError):
            qs.change(rows, q, name="la veglia")                                               # another quest has it
        with self.assertRaises(qs.QuestError):
            qs.change(rows, q, status="boh")

    def test_steps_are_ticked_by_any_part_that_means_one(self):
        rows = []
        q = qs.add(rows, "La Rocca", objectives=["Arrivare alla Rocca", "Entrare nella cantina", "Scendere la scala"])
        self.assertTrue(qs.mark(q, "cantina")["done"])
        self.assertFalse(qs.mark(q, "CANTINA", done=False)["done"])
        for bad in ("la", "torre", ""):                                                        # several, none, nothing
            with self.assertRaises(qs.QuestError):
                qs.mark(q, bad)
        self.assertEqual(qs.drop(q, objective="scala"), "Scendere la scala")
        qs.add_clue(q, "La parola è «Io resto»")
        self.assertFalse(qs.add_clue(q, "la parola è «io resto»"))
        self.assertEqual(qs.drop(q, clue="parola"), "La parola è «Io resto»")
        with self.assertRaises(qs.QuestError):
            qs.drop(q, objective="x", clue="y")
        self.assertEqual((len(q["objectives"]), q["clues"]), (2, []))

    def test_a_short_name_must_mean_one_quest(self):
        rows = []
        qs.add(rows, "La veglia")
        qs.add(rows, "La veglia di Oderico")
        self.assertEqual(qs.find(rows, "oderico")["name"], "La veglia di Oderico")
        self.assertEqual(qs.find(rows, "la veglia")["name"], "La veglia")                      # the exact name wins
        for bad in ("vegl", "drago", ""):
            with self.assertRaises(qs.QuestError):
                qs.find(rows, bad)

    def test_the_panel_gets_open_quests_first_and_no_empty_fields(self):
        rows = []
        qs.add(rows, "Fatta", status="resolved")
        qs.add(rows, "Minaccia", status="threat", deadline="Domani")
        q = qs.add(rows, "In corso", objectives=["Uno"], clues=["Indizio"])
        qs.mark(q, "uno")
        view = qs.public_view(rows)
        self.assertEqual([r["name"] for r in view], ["Minaccia", "In corso", "Fatta"])
        self.assertEqual(view[0], {"name": "Minaccia", "status": "threat", "deadline": "Domani"})
        self.assertEqual(view[1], {"name": "In corso", "status": "active", "objectives": [{"text": "Uno", "done": True}],
                                   "clues": ["Indizio"]})
        self.assertNotIn("id", view[2])

    def test_taking_in_the_displays_quests_adds_only_the_missing_ones(self):
        rows = []
        qs.add(rows, "La veglia", summary="la mia")
        shown = [{"name": "la VEGLIA", "status": "active", "summary": "quella del display"},
                 {"name": "L'oro dei Valtrenna", "status": "resolved", "reward": "20 mo",
                  "objectives": [{"text": "Riscuotere", "done": True}, "Guidare"], "clues": ["Le borse le tiene Vardamir"]},
                 {"name": "Strana", "status": "in forse"}, {"status": "active"}, "x"]
        self.assertEqual(qs.take_in(rows, shown), ["L'oro dei Valtrenna", "Strana"])
        self.assertEqual(rows[0]["summary"], "la mia")
        gold = qs.find(rows, "oro")
        self.assertEqual((gold["status"], gold["reward"], gold["clues"]), ("resolved", "20 mo", ["Le borse le tiene Vardamir"]))
        self.assertEqual(gold["objectives"], [{"text": "Riscuotere", "done": True}, {"text": "Guidare", "done": False}])
        self.assertEqual(qs.find(rows, "strana")["status"], "active")
        self.assertEqual(qs.take_in(rows, shown), [])


class CommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._old = os.environ.get("DND_CAMPAIGN_ROOT")
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        for name in ("prova", "altra"):
            (self.root / "campaigns" / name).mkdir(parents=True)
        self.events = []
        self._saved = (qs._announce, qs._display_quests, media.active_campaign)
        qs._announce = lambda event=None: self.events.append(event)
        media.active_campaign = lambda: "prova"

    def tearDown(self):
        qs._announce, qs._display_quests, media.active_campaign = self._saved
        if self._old is None:
            os.environ.pop("DND_CAMPAIGN_ROOT", None)
        else:
            os.environ["DND_CAMPAIGN_ROOT"] = self._old
        self._tmp.cleanup()

    def run_qs(self, *argv, campaign="prova"):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = qs.main(["--campaign", campaign, *argv])
        return code, out.getvalue() + err.getvalue()

    def saved(self, campaign="prova"):
        return qs.load(str(self.root / "campaigns" / campaign / "quests.json"))

    def test_a_quest_from_first_word_to_the_end(self):
        code, out = self.run_qs("add", "La veglia di Oderico", "--giver", "Padre Oderico",
                                "--objective", "Vegliare una notte", "--clue", "Dicono «Ausgar»")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.events[-1], {"name": "La veglia di Oderico", "what": "new"})
        self.assertEqual(self.run_qs("objective", "veglia", "Leggere i registri")[0], 0)
        code, out = self.run_qs("done", "veglia", "vegliare")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip().splitlines()[-1], "✓ ticked: La veglia di Oderico — Vegliare una notte")
        code, out = self.run_qs("done", "veglia", "registri")
        self.assertTrue(out.strip().endswith("— Leggere i registri (no step left open: resolve it, or add the next one)"))
        out = self.run_qs("undo", "veglia", "registri")[1]
        self.assertEqual(out.strip().splitlines()[-1], "○ unticked: La veglia di Oderico — Leggere i registri")
        self.assertEqual(self.run_qs("clue", "veglia", "Stanotte c'è anche Bertrand")[0], 0)
        self.assertEqual(self.events[-1], {"name": "La veglia di Oderico", "what": "updated"})
        self.run_qs("clue", "veglia", "stanotte c'è anche bertrand")
        self.assertIsNone(self.events[-1])                                      # known already: no toast
        self.assertEqual(self.run_qs("set", "veglia", "--deadline", "Stanotte", "--name", "La veglia")[0], 0)
        q = self.saved()[0]
        self.assertEqual((q["name"], q["deadline"], [o["done"] for o in q["objectives"]], len(q["clues"])),
                         ("La veglia", "Stanotte", [True, False], 2))
        self.assertEqual(self.run_qs("resolve", "veglia")[0], 0)
        self.assertEqual(self.events[-1], {"name": "La veglia", "what": "resolved"})
        self.run_qs("reopen", "veglia")
        self.assertEqual((self.saved()[0]["status"], self.events[-1]["what"]), ("active", "updated"))
        self.run_qs("fail", "veglia")
        self.assertEqual(self.events[-1]["what"], "failed")
        code, out = self.run_qs("list")
        self.assertIn("[failed] La veglia — 1/2 objectives, 2 clue(s)", out)
        self.assertIn("[x] Vegliare una notte", self.run_qs("show", "veglia")[1])
        self.assertEqual(self.run_qs("drop", "veglia", "--clue", "ausgar")[0], 0)
        self.assertEqual(self.run_qs("remove", "veglia")[0], 0)
        self.assertEqual(self.saved(), [])

    def test_a_mistake_changes_nothing(self):
        self.run_qs("add", "La veglia", "--objective", "Vegliare")
        before, announced = self.saved(), len(self.events)
        for argv in (("add", "la veglia"), ("add", "Altra", "--status", "boh"), ("set", "veglia"), ("set", "nessuna", "--giver", "x"),
                     ("done", "veglia", "scendere"), ("drop", "veglia"), ("resolve", "nessuna"), ("clue", "veglia", " ")):
            code, out = self.run_qs(*argv)
            self.assertEqual(code, 1, argv)
            self.assertIn("Nothing was changed", out)
        self.assertEqual(self.saved(), before)
        self.assertEqual(len(self.events), announced)

    def test_only_the_campaign_on_the_display_talks_to_it(self):
        self.assertEqual(self.run_qs("add", "Altrove", campaign="altra")[0], 0)
        self.assertEqual((len(self.saved("altra")), self.events), (1, []))
        for cmd in ("push", "import"):
            code, out = self.run_qs(cmd, campaign="altra")
            self.assertEqual(code, 1)
            self.assertIn("not the campaign on the display", out)
        self.assertEqual(self.run_qs("push")[0], 0)
        self.assertEqual(self.events, [None])

    def test_import_takes_in_what_was_pushed_by_hand(self):
        qs._display_quests = lambda: [{"name": "Il rogo", "status": "active", "summary": "Fermato."},
                                      {"name": "L'oro", "status": "resolved"}]
        code, out = self.run_qs("import")
        self.assertEqual(code, 0, out)
        self.assertIn("+ Il rogo", out)
        self.assertEqual([q["name"] for q in self.saved()], ["Il rogo", "L'oro"])
        self.assertIn("nothing to take in", self.run_qs("import")[1])

    def test_it_runs_as_a_script(self):
        env = dict(os.environ, PYTHONUTF8="1")
        r = subprocess.run([sys.executable, str(DISPLAY / "quests.py"), "--campaign", "prova", "list"],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "no quests yet"), r.stderr[-400:])


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_quests", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.camp = pathlib.Path(tmp.name)
        saved = (self.mod._active_campaign, self.mod._find_campaign, self.mod._broadcast)
        self.addCleanup(lambda: setattr(self.mod, "_active_campaign", saved[0]) or setattr(self.mod, "_find_campaign", saved[1])
                        or setattr(self.mod, "_broadcast", saved[2]))
        self.sent = []
        self.mod._active_campaign, self.mod._find_campaign, self.mod._broadcast = (lambda: "prova"), (lambda n: self.camp), self.sent.append
        with self.mod._stats_lock:
            self.mod._current_stats.pop("quests", None)

    def write(self, *names):
        rows = []
        for name in names:
            qs.add(rows, name)
        qs.save(str(self.camp / "quests.json"), rows)

    def test_a_change_puts_the_campaigns_quests_on_every_screen(self):
        self.write("La veglia", "Il rogo")
        r = self.client.post("/quests", json={"event": {"name": "La veglia", "what": "new", "x": 1}})
        self.assertEqual(r.status_code, 204)
        self.assertEqual([q["name"] for q in self.sent[-1]["stats"]["quests"]], ["La veglia", "Il rogo"])
        self.assertEqual(self.sent[-1]["quest_event"], {"name": "La veglia", "what": "new"})
        self.assertEqual([q["name"] for q in self.mod._current_stats["quests"]], ["La veglia", "Il rogo"])
        self.client.post("/quests", json={"event": {"name": "La veglia", "what": "esplosa"}})
        self.assertNotIn("quest_event", self.sent[-1])
        self.assertIn("quests_changed", self.mod._DM_ENDPOINTS)

    def test_a_campaign_without_the_file_keeps_the_quests_pushed_by_hand(self):
        with self.mod._stats_lock:
            self.mod._current_stats["quests"] = [{"name": "A mano", "status": "active"}]
        before = len(self.sent)
        self.assertEqual(self.client.post("/quests", json={}).status_code, 204)
        self.assertEqual(len(self.sent), before)
        self.assertEqual(self.mod._current_stats["quests"], [{"name": "A mano", "status": "active"}])

    def test_loading_the_campaign_brings_its_quests_back(self):
        self.write("La veglia")
        self.assertEqual(self.client.post("/stats", json={"campaign": "prova"}).status_code, 204)
        self.assertEqual([q["name"] for q in self.mod._current_stats["quests"]], ["La veglia"])
        stats = [p["stats"] for p in self.sent if "stats" in p][-1]
        self.assertEqual([q["name"] for q in stats["quests"]], ["La veglia"])
        self.client.post("/stats", json={"campaign": "prova", "quests": [{"name": "Nella stessa chiamata", "status": "active"}]})
        self.assertEqual([q["name"] for q in self.mod._current_stats["quests"]], ["Nella stessa chiamata"])


class PageTests(unittest.TestCase):
    def test_the_page_says_when_a_quest_changes(self):
        self.assertIn("payload.quest_event", HTML.read_text(encoding="utf-8"))

    def test_strings_exist_in_every_language(self):
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for what in ("new", "updated", "resolved", "failed"):
                with self.subTest(lang=path.stem, what=what):
                    self.assertIn("{name}", data.get("quest.toast." + what, ""))


if __name__ == "__main__":
    unittest.main()
