"""
test_downtime.py — what the characters do between adventures, chosen from the phone.

display/downtime.py opens a stretch of downtime; each player picks a PHB
downtime activity on their own screen (the choice is kept in the campaign's
downtime.json); when everybody has chosen the DM is told; closing it works out
the outcome, moves long projects on and can pay the costs from the sheets.

Run from repo root:
    python -m pytest tests/test_downtime.py -q
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
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(SKILL / "scripts"))

import downtime as dt  # noqa: E402
import media  # noqa: E402


def _open(days=10, players=("Lyra", "Hernio")):
    data = dt.load("does-not-exist.json")
    dt.open_window(data, days, list(players), "Una settimana a Vallombra")
    return data


class WindowTests(unittest.TestCase):
    def test_choices_are_made_changed_and_checked(self):
        data = _open()
        self.assertEqual(dt.choose(data, "lyra", "ricerca", "La corona dei Valtrenna"), ("Lyra", False))
        self.assertEqual(dt.choose(data, "Lyra", "train", "Elfico"), ("Lyra", False))
        self.assertEqual(dt.choose(data, "Hernio", "lavorare"), ("Hernio", True))
        self.assertEqual(data["window"]["choices"]["Lyra"]["activity"], "train")
        for args in (("Ajeje", "work"), ("Lyra", "volare"), ("Lyra", "craft", " ")):
            with self.subTest(args=args), self.assertRaises(dt.DowntimeError):
                dt.choose(data, *args)
        with self.assertRaises(dt.DowntimeError):
            dt.open_window(data, 3, ["Lyra"])

    def test_a_window_needs_days_and_players(self):
        data = dt.load("does-not-exist.json")
        for days, players in ((0, ["Lyra"]), (400, ["Lyra"]), (5, [])):
            with self.subTest(days=days), self.assertRaises(dt.DowntimeError):
                dt.open_window(data, days, players)

    def test_closing_moves_projects_on_and_works_out_costs(self):
        data = _open(days=10, players=("Lyra", "Hernio", "Ajeje", "Vardamir"))
        dt.choose(data, "Lyra", "train", "Elfico")
        dt.choose(data, "Hernio", "craft", "Pozione di guarigione 50 mo")
        dt.choose(data, "Ajeje", "recuperate")
        results = {r["character"]: r for r in dt.close_window(data)}
        self.assertEqual((results["Lyra"]["cost_gp"], results["Hernio"]["cost_gp"], results["Ajeje"]["cost_gp"]), (10, 25, 0))
        self.assertIn("10 of 250 days", results["Lyra"]["text"])
        self.assertIn("50 of 50 gp", results["Hernio"]["text"])
        self.assertIn("FINISHED", results["Hernio"]["text"])
        self.assertIn("DC 15 Constitution", results["Ajeje"]["text"])
        self.assertIn("did not choose", results["Vardamir"]["text"])
        self.assertTrue(data["window"]["closed"])
        dt.open_window(data, 20, ["Lyra"])
        dt.choose(data, "Lyra", "train", "elfico")
        (again,) = dt.close_window(data)
        self.assertIn("30 of 250 days", again["text"])
        self.assertEqual(len(data["projects"]["Lyra"]), 1)
        self.assertEqual(len(data["history"]), 2)

    def test_screens_see_their_own_and_the_dm_sees_all(self):
        data = _open()
        dt.choose(data, "Lyra", "research", "La corona")
        lyra = dt.public_view(data, "Lyra")
        self.assertEqual((lyra["asked"], lyra["mine"], lyra["chosen"]), (True, {"activity": "research", "detail": "La corona"}, ["Lyra"]))
        self.assertNotIn("choices", lyra)
        self.assertEqual(dt.public_view(data, "Ajeje")["asked"], False)
        self.assertEqual(dt.public_view(data, dm=True)["choices"]["Lyra"]["detail"], "La corona")
        dt.close_window(data)
        self.assertIsNone(dt.public_view(data, "Lyra"))

    def test_the_dm_line_names_every_choice(self):
        data = _open()
        dt.choose(data, "Lyra", "train", "Elfico")
        dt.choose(data, "Hernio", "work")
        line = dt.format_ready(data["window"])
        self.assertTrue(line.startswith("[[DOWNTIME: every player has chosen for the 10 days"))
        self.assertIn("Lyra: train (Elfico)", line)
        self.assertIn("Hernio: work", line)


class CommandTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        chars = self.root / "campaigns" / "prova" / "characters"
        chars.mkdir(parents=True)
        (chars / "lyra.md").write_text("# Lyra\n\n## Equipment & Inventory\n**Adventuring Gear:**\n- Corda\n\n"
                                       "**Currency:** 30gp 0sp 0cp\n\n## Backstory & Notes\n", encoding="utf-8")
        (self.root / "rt").mkdir()
        for var, value in (("DND_CAMPAIGN_ROOT", str(self.root)), ("DND_RUNTIME_DIR", str(self.root / "rt"))):
            old = os.environ.get(var)
            os.environ[var] = value
            self.addCleanup(lambda var=var, old=old: os.environ.pop(var, None) if old is None else os.environ.__setitem__(var, old))
        self.events = []
        saved = (dt._announce, dt._party, media.active_campaign)
        dt._announce = self.events.append
        dt._party = lambda: ["Lyra", "Hernio"]
        media.active_campaign = lambda: "prova"
        self.addCleanup(lambda: (setattr(dt, "_announce", saved[0]), setattr(dt, "_party", saved[1]),
                                 setattr(media, "active_campaign", saved[2])))
        import inventory
        self._push = inventory._push
        inventory._push = lambda args: None
        self.addCleanup(lambda: setattr(inventory, "_push", self._push))

    def run_dt(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dt.main(["--campaign", "prova", *argv])
        return code, out.getvalue() + err.getvalue()

    def path(self):
        return str(self.root / "campaigns" / "prova" / "downtime.json")

    def test_open_choose_close_and_pay(self):
        code, out = self.run_dt("open", "7", "--note", "In attesa della neve")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.events[-1]["what"], "open")
        self.assertIn("Lyra: not chosen yet", out)
        data = dt.load(self.path())
        dt.choose(data, "Lyra", "research", "La corona")
        dt.save(self.path(), data)
        self.assertIn("Lyra: research — La corona", self.run_dt("show")[1])
        code, out = self.run_dt("close", "--charge")
        self.assertEqual(code, 0, out)
        self.assertIn("Lyra paid 7gp", out)
        self.assertIn("Hernio: did not choose", out)
        self.assertIn("**Currency:** 23gp", (self.root / "campaigns" / "prova" / "characters" / "lyra.md").read_text(encoding="utf-8"))
        self.assertEqual(self.events[-1], {"what": "closed"})
        self.assertEqual(self.run_dt("close")[0], 1)

    def test_only_and_cancel_and_projects(self):
        self.assertEqual(self.run_dt("open", "250", "--only", "Lyra")[0], 0)
        data = dt.load(self.path())
        dt.choose(data, "Lyra", "train", "Elfico")
        dt.save(self.path(), data)
        self.assertIn("LEARNED", self.run_dt("close")[1])
        self.assertIn("Lyra: train Elfico — 250/250 days — done", self.run_dt("projects")[1])
        self.run_dt("open", "3")
        self.assertEqual(self.run_dt("cancel")[0], 0)
        self.assertEqual(self.run_dt("cancel")[0], 1)

    def test_it_runs_as_a_script(self):
        env = dict(os.environ, PYTHONUTF8="1")
        r = subprocess.run([sys.executable, str(DISPLAY / "downtime.py"), "--campaign", "prova", "show"],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "no downtime"), r.stderr[-400:])


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_downtime", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.camp = pathlib.Path(tmp.name)
        m = self.mod
        saved = (m._active_campaign, m._find_campaign, m._broadcast, m._downtime.notes_add)
        self.addCleanup(lambda: (setattr(m, "_active_campaign", saved[0]), setattr(m, "_find_campaign", saved[1]),
                                 setattr(m, "_broadcast", saved[2]), setattr(m._downtime, "notes_add", saved[3])))
        self.sent, self.notes = [], []
        m._active_campaign, m._find_campaign, m._broadcast = (lambda: "prova"), (lambda n: self.camp), self.sent.append
        m._downtime.notes_add = self.notes.append
        dt.save(str(self.camp / "downtime.json"), _open(players=("Lyra", "Hernio")))

    def choose(self, who, activity, detail=""):
        return self.client.post("/downtime/choose", json={"character": who, "activity": activity, "detail": detail})

    def test_players_choose_and_the_dm_is_told_once(self):
        r = self.choose("Lyra", "research", "La corona")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["downtime"]["chosen"], ["Lyra"])
        self.assertEqual(self.notes, [])
        self.assertEqual(self.choose("Hernio", "work").status_code, 200)
        self.assertEqual(len(self.notes), 1)
        self.choose("Hernio", "carouse")
        self.assertEqual(len(self.notes), 1)                      # told once
        self.assertEqual(self.sent[-1]["downtime_event"]["what"], "chosen")
        saved = dt.load(str(self.camp / "downtime.json"))
        self.assertEqual(saved["window"]["choices"]["Hernio"]["activity"], "carouse")

    def test_mistakes_are_refused(self):
        self.assertEqual(self.choose("Lyra", "craft").get_json()["code"], "detail")
        self.assertEqual(self.choose("Ajeje", "work").status_code, 409)
        data = dt.load(str(self.camp / "downtime.json"))
        dt.close_window(data)
        dt.save(str(self.camp / "downtime.json"), data)
        self.assertEqual(self.choose("Lyra", "work").get_json()["code"], "closed")
        self.assertEqual(self.client.get("/downtime").get_json(), {"downtime": None})

    def test_the_dm_sees_every_choice_and_the_event_is_relayed(self):
        self.choose("Lyra", "research", "La corona")
        view = self.client.get("/downtime").get_json()["downtime"]
        self.assertEqual(view["choices"]["Lyra"]["detail"], "La corona")
        self.assertEqual(self.client.post("/downtime", json={"event": {"what": "open", "days": 7}}).status_code, 204)
        self.assertEqual(self.sent[-1], {"downtime_event": {"what": "open", "days": 7}})
        self.assertIn("downtime_changed", self.mod._DM_ENDPOINTS)


class PageTests(unittest.TestCase):
    def test_the_page_has_the_panel(self):
        html = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")
        for needle in ('id="dt-modal"', 'id="ln-downtime"', "window._onDowntime(", "/downtime/choose"):
            self.assertIn(needle, html)

    def test_every_activity_has_its_words_in_every_language(self):
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for k in dt.ACTIVITIES:
                for prefix in ("dt.act.", "dt.desc.", "dt.detail."):
                    with self.subTest(lang=path.stem, key=prefix + k):
                        self.assertTrue(data.get(prefix + k))


if __name__ == "__main__":
    unittest.main()
