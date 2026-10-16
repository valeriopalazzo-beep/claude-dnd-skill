"""
test_factions.py — what each faction thinks of the party, as a score that moves.

display/factions.py keeps a reputation from -10 to +10 for each faction in
<campaign>/factions.json; the standing word follows from the number. The
factions reach the screens inside the stats, like the ones pushed by hand with
--factions, and come back when the campaign is loaded.

Run from repo root:
    python -m pytest tests/test_factions.py -q
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

import factions as fx  # noqa: E402
import media  # noqa: E402


class FactionTests(unittest.TestCase):
    def test_the_word_follows_the_score(self):
        self.assertEqual([fx.standing(n) for n in (-10, -6, -5, -2, -1, 0, 1, 2, 5, 6, 10)],
                         ["Hostile", "Hostile", "Suspicious", "Suspicious", "Neutral", "Neutral", "Neutral",
                          "Friendly", "Friendly", "Allied", "Allied"])
        self.assertEqual([fx.score_of(w) for w in ("ostile", "Diffidente", "neutral", "amichevole", "ALLIED")],
                         [-8, -3, 0, 3, 8])
        with self.assertRaises(fx.FactionError):
            fx.score_of("boh")

    def test_a_new_faction_keeps_what_it_was_given(self):
        rows = []
        f = fx.add(rows, "Casa Valtrenna", score=-3, leader="Ser Bertrand", members=["Ser Bertrand"],
                   known=["Hanno un frammento"])
        self.assertEqual((f["name"], f["score"], f["leader"], f["members"], f["known"], f["secret"]),
                         ("Casa Valtrenna", -3, "Ser Bertrand", ["Ser Bertrand"], ["Hanno un frammento"], False))
        self.assertEqual(fx.add(rows, "La Confraternita", word="ostile")["score"], -8)
        for args, kwargs in ((("",), {}), (("casa valtrenna",), {}), (("Troppo",), {"score": 11}),
                             (("Entrambi",), {"score": 1, "word": "amichevole"}), (("Strana",), {"word": "boh"})):
            with self.assertRaises(fx.FactionError):
                fx.add(rows, *args, **kwargs)
        self.assertEqual(len(rows), 2)

    def test_moves_stop_at_the_ends_and_are_remembered(self):
        rows = []
        f = fx.add(rows, "La Guardia", score=8)
        self.assertEqual(fx.move(f, by=5, reason="Avete salvato il capitano"), 2)
        self.assertEqual(fx.move(f, by=1), 0)
        self.assertEqual(fx.move(f, to=-4, reason="Avete mentito"), -14)
        self.assertEqual([(h["delta"], h["score"], h["reason"]) for h in f["history"]],
                         [(2, 10, "Avete salvato il capitano"), (-14, -4, "Avete mentito")])
        with self.assertRaises(fx.FactionError):
            fx.move(f, to=12)
        for _ in range(fx.HISTORY_KEPT + 5):
            fx.move(f, by=1 if f["score"] < 0 else -1)
        self.assertEqual(len(f["history"]), fx.HISTORY_KEPT)

    def test_the_panel_gets_no_secret_faction_and_the_last_moves_first(self):
        rows = []
        f = fx.add(rows, "La Guardia", leader="Il capitano")
        fx.add(rows, "I Ladri", secret=True)
        for i in range(7):
            fx.move(f, by=1, reason=f"passo {i}")
        view = fx.public_view(rows)
        self.assertEqual([v["name"] for v in view], ["La Guardia"])
        self.assertEqual((view[0]["score"], view[0]["standing"], view[0]["leader"]), (7, "Allied", "Il capitano"))
        self.assertEqual([h["reason"] for h in view[0]["history"]], ["passo 6", "passo 5", "passo 4", "passo 3", "passo 2"])
        self.assertNotIn("secret", view[0])

    def test_taking_in_the_displays_factions_reads_the_word(self):
        rows = []
        fx.add(rows, "Casa Valtrenna")
        new = fx.take_in(rows, [{"name": "casa valtrenna", "standing": "Hostile"},
                                {"name": "Il Villaggio", "standing": "Suspicious", "goal": "Bruciarvi", "known": ["Credono a Tecla"]},
                                {"name": "Strani", "standing": "Unknown"}, "rotto"])
        self.assertEqual(new, ["Il Villaggio", "Strani"])
        self.assertEqual([(r["name"], r["score"]) for r in rows], [("Casa Valtrenna", 0), ("Il Villaggio", -3), ("Strani", 0)])
        self.assertEqual((rows[1]["goal"], rows[1]["known"]), ("Bruciarvi", ["Credono a Tecla"]))


class CommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._old = os.environ.get("DND_CAMPAIGN_ROOT")
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        for name in ("prova", "altra"):
            (self.root / "campaigns" / name).mkdir(parents=True)
        self.events = []
        self._saved = (fx._announce, fx._display_factions, media.active_campaign)
        fx._announce = lambda event=None: self.events.append(event)
        media.active_campaign = lambda: "prova"

    def tearDown(self):
        fx._announce, fx._display_factions, media.active_campaign = self._saved
        if self._old is None:
            os.environ.pop("DND_CAMPAIGN_ROOT", None)
        else:
            os.environ["DND_CAMPAIGN_ROOT"] = self._old
        self._tmp.cleanup()

    def run_fx(self, *argv, campaign="prova"):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = fx.main(["--campaign", campaign, *argv])
        return code, out.getvalue() + err.getvalue()

    def saved(self, campaign="prova"):
        return fx.load(str(self.root / "campaigns" / campaign / "factions.json"))

    def test_a_faction_from_first_word_to_the_end(self):
        code, out = self.run_fx("add", "Casa Valtrenna", "--score", "-3", "--leader", "Ser Bertrand")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.events[-1]["new"], True)
        code, out = self.run_fx("up", "valtrenna", "6", "--reason", "Avete riportato il frammento")
        self.assertEqual(code, 0, out)
        self.assertIn("Suspicious → Friendly", out)
        self.assertEqual({k: self.events[-1][k] for k in ("delta", "score", "standing", "reason")},
                         {"delta": 6, "score": 3, "standing": "Friendly", "reason": "Avete riportato il frammento"})
        self.run_fx("down", "valtrenna", "--reason", "Lyra lo ha minacciato")
        self.assertEqual(self.events[-1]["delta"], -1)
        self.run_fx("to", "valtrenna", "2")
        self.assertIn("nothing moved", self.run_fx("to", "valtrenna", "2")[1])
        self.assertIsNone(self.events[-1])
        self.assertEqual(self.run_fx("member", "valtrenna", "Ser Bertrand")[0], 0)
        self.assertEqual(self.run_fx("learn", "valtrenna", "Hanno un frammento")[0], 0)
        self.assertEqual(self.run_fx("set", "valtrenna", "--goal", "La corona", "--name", "I Valtrenna")[0], 0)
        f = self.saved()[0]
        self.assertEqual((f["name"], f["score"], f["goal"], f["members"], f["known"], len(f["history"])),
                         ("I Valtrenna", 2, "La corona", ["Ser Bertrand"], ["Hanno un frammento"], 2))
        self.assertIn("+6 → +3  Avete riportato il frammento", self.run_fx("show", "valtrenna")[1])
        self.assertEqual(self.run_fx("drop", "valtrenna", "--known", "frammento")[0], 0)
        self.assertEqual(self.run_fx("remove", "valtrenna")[0], 0)
        self.assertEqual(self.saved(), [])

    def test_a_secret_faction_says_nothing_until_revealed(self):
        self.run_fx("add", "La Confraternita", "--standing", "ostile", "--secret")
        self.run_fx("down", "confraternita", "2")
        self.assertEqual(self.events, [None, None])
        self.run_fx("reveal", "confraternita")
        self.assertEqual((self.events[-1]["name"], self.events[-1]["standing"]), ("La Confraternita", "Hostile"))

    def test_a_mistake_changes_nothing(self):
        self.run_fx("add", "La Guardia")
        before, announced = self.saved(), len(self.events)
        for argv in (("add", "la guardia"), ("add", "Altra", "--standing", "boh"), ("up", "nessuna"), ("up", "guardia", "0"),
                     ("to", "guardia", "15"), ("to", "guardia", "tanto"), ("set", "guardia"), ("drop", "guardia"),
                     ("learn", "guardia", " ")):
            code, out = self.run_fx(*argv)
            self.assertEqual(code, 1, argv)
            self.assertIn("Nothing was changed", out)
        self.assertEqual(self.saved(), before)
        self.assertEqual(len(self.events), announced)

    def test_only_the_campaign_on_the_display_talks_to_it(self):
        self.assertEqual(self.run_fx("add", "Altrove", campaign="altra")[0], 0)
        self.assertEqual((len(self.saved("altra")), self.events), (1, []))
        for cmd in ("push", "import"):
            self.assertEqual(self.run_fx(cmd, campaign="altra")[0], 1)
        self.assertEqual(self.run_fx("push")[0], 0)
        self.assertEqual(self.events, [None])

    def test_import_takes_in_what_was_pushed_by_hand(self):
        fx._display_factions = lambda: [{"name": "Casa Valtrenna", "standing": "Suspicious"}]
        code, out = self.run_fx("import")
        self.assertEqual(code, 0, out)
        self.assertEqual([(f["name"], f["score"]) for f in self.saved()], [("Casa Valtrenna", -3)])
        self.assertIn("nothing to take in", self.run_fx("import")[1])

    def test_it_runs_as_a_script(self):
        env = dict(os.environ, PYTHONUTF8="1")
        r = subprocess.run([sys.executable, str(DISPLAY / "factions.py"), "--campaign", "prova", "list"],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "no factions yet"), r.stderr[-400:])


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_factions", str(DISPLAY / "dnd-display-app.py"))
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
            self.mod._current_stats.pop("factions", None)

    def write(self):
        rows = []
        fx.add(rows, "La Guardia", score=4)
        fx.add(rows, "I Ladri", secret=True)
        fx.save(str(self.camp / "factions.json"), rows)

    def test_a_change_puts_the_campaigns_factions_on_every_screen(self):
        self.write()
        r = self.client.post("/factions", json={"event": {"name": "La Guardia", "delta": 2, "score": 4, "reason": "x", "y": 1}})
        self.assertEqual(r.status_code, 204)
        self.assertEqual([(f["name"], f["standing"], f["score"]) for f in self.sent[-1]["stats"]["factions"]],
                         [("La Guardia", "Friendly", 4)])
        self.assertEqual(self.sent[-1]["faction_event"],
                         {"name": "La Guardia", "delta": 2, "score": 4, "standing": "Friendly", "new": False, "reason": "x"})
        self.assertIn("factions_changed", self.mod._DM_ENDPOINTS)

    def test_a_campaign_without_the_file_keeps_the_factions_pushed_by_hand(self):
        with self.mod._stats_lock:
            self.mod._current_stats["factions"] = [{"name": "A mano", "standing": "Neutral"}]
        before = len(self.sent)
        self.assertEqual(self.client.post("/factions", json={}).status_code, 204)
        self.assertEqual(len(self.sent), before)
        self.assertEqual(self.mod._current_stats["factions"], [{"name": "A mano", "standing": "Neutral"}])

    def test_loading_the_campaign_brings_its_factions_back(self):
        self.write()
        self.assertEqual(self.client.post("/stats", json={"campaign": "prova"}).status_code, 204)
        self.assertEqual([f["name"] for f in self.mod._current_stats["factions"]], ["La Guardia"])
        self.client.post("/stats", json={"campaign": "prova", "factions": [{"name": "Nella stessa chiamata", "standing": "Allied"}]})
        self.assertEqual([f["name"] for f in self.mod._current_stats["factions"]], ["Nella stessa chiamata"])


class PageTests(unittest.TestCase):
    def test_the_page_draws_the_bar_and_says_when_a_faction_moves(self):
        html = HTML.read_text(encoding="utf-8")
        self.assertIn("payload.faction_event", html)
        self.assertIn("_factionBar(f.score)", html)

    def test_strings_exist_in_every_language(self):
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("faction.toast.new", "faction.toast.up", "faction.toast.down"):
                with self.subTest(lang=path.stem, key=key):
                    self.assertIn("{name}", data.get(key, ""))
            self.assertIn("{n}", data.get("faction.score", ""))
            self.assertTrue(data.get("faction.history"))


if __name__ == "__main__":
    unittest.main()
