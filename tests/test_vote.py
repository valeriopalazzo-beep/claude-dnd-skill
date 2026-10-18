"""
test_vote.py — the party decides on their phones.

vote.py asks the table (or one group of a split party) a question; each voter
answers on their own screen and may change it; the vote ends by itself when
everybody has voted, the result goes into the story and reaches the DM.

Run from repo root:
    python -m pytest tests/test_vote.py -q
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
sys.path.insert(0, str(SKILL / "scripts"))

import vote  # noqa: E402

VOTE = {"question": "Destra o sinistra?", "options": ["Destra", "Sinistra", "Indietro"],
        "voters": ["Ajeje", "Lyra", "Hernio", "Lio"], "votes": {"Ajeje": 1, "Lyra": 0, "Hernio": 0}}


class CountTests(unittest.TestCase):
    def test_the_count_the_winner_and_the_lines(self):
        self.assertEqual(vote.tally(VOTE), [2, 1, 0])
        self.assertEqual(vote.winners(VOTE), ["Destra"])
        self.assertEqual(vote.winners(dict(VOTE, votes={"Ajeje": 1, "Lyra": 0})), ["Destra", "Sinistra"])
        self.assertEqual(vote.winners(dict(VOTE, votes={})), [])
        self.assertEqual(vote.result_line(VOTE), "🗳 Destra o sinistra? — Destra 2 · Sinistra 1 · Indietro 0")
        self.assertEqual(vote.format_note(VOTE),
                         '[[VOTE: "Destra o sinistra?" — Destra 2, Sinistra 1, Indietro 0 (Ajeje: Sinistra; Lyra: Destra; '
                         'Hernio: Destra). Destra wins. Did not vote: Lio.]]')
        tie = vote.format_note(dict(VOTE, voters=["Ajeje", "Lyra"], votes={"Ajeje": 1, "Lyra": 0}))
        self.assertIn("A tie between Destra, Sinistra: you decide, or ask again. Everybody voted.", tie)

    def test_the_result_reaches_the_dm_once(self):
        old = vote.NOTES_FILE
        with tempfile.TemporaryDirectory() as tmp:
            vote.NOTES_FILE = os.path.join(tmp, "vote_notes.json")
            try:
                self.assertFalse(vote.notes_pending())
                vote.notes_add(VOTE)
                self.assertTrue(vote.notes_pending())
                self.assertEqual(len(vote.drain_notes()), 1)
                self.assertEqual((vote.drain_notes(), vote.notes_pending()), ([], False))
            finally:
                vote.NOTES_FILE = old
        for script in ("check_input.py", "autorun_wait.py"):
            self.assertIn("vote", (DISPLAY / script).read_text(encoding="utf-8"))

    def test_a_bad_vote_is_refused_before_the_display(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(vote.main(["open", "Solo una?", "--option", "Sì"]), 1)
        self.assertIn("Nothing was opened", err.getvalue())


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_vote", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        m = self.mod
        saved = (m._broadcast, m._persist_vote, m._persist_log, m._persist_tail, m._active_campaign, m._vote, m._vote_lib.notes_add)
        def restore():
            (m._broadcast, m._persist_vote, m._persist_log, m._persist_tail, m._active_campaign, m._vote, m._vote_lib.notes_add) = saved
        self.addCleanup(restore)
        self.sent, self.notes = [], []
        m._broadcast = self.sent.append
        m._persist_vote = m._persist_log = m._persist_tail = lambda: None
        m._active_campaign = lambda: "prova"
        m._vote = None
        m._vote_lib.notes_add = self.notes.append
        with m._stats_lock:
            self._stats = dict(m._current_stats)
            m._current_stats.clear()
            m._current_stats.update({"players": [{"name": "Ajeje"}, {"name": "Lyra"}, {"name": "Hernio"}]})
        self.addCleanup(self._restore_stats)

    def _restore_stats(self):
        with self.mod._stats_lock:
            self.mod._current_stats.clear()
            self.mod._current_stats.update(self._stats)

    def open_vote(self, **body):
        r = self.client.post("/vote", json={"question": "Destra o sinistra?", "options": ["Destra", "Sinistra"], **body})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()["vote"]

    def cast(self, who, option):
        return self.client.post("/vote/cast", json={"character": who, "option": option})

    def test_everybody_votes_and_the_vote_ends_by_itself(self):
        v = self.open_vote()
        self.assertEqual((v["voters"], v["counts"], v["closed"]), (["Ajeje", "Lyra", "Hernio"], [0, 0], False))
        self.assertTrue(self.sent[-1]["vote_event"]["opened"])
        self.assertEqual(self.cast("ajeje", 1).get_json()["vote"]["counts"], [0, 1])
        self.cast("Ajeje", 0)                                    # changes their mind
        self.assertEqual(self.client.get("/vote").get_json()["vote"]["counts"], [1, 0])
        self.cast("Lyra", 1)
        self.assertEqual(self.notes, [])
        v = self.cast("Hernio", 0).get_json()["vote"]
        self.assertEqual((v["closed"], v["counts"], v["winners"]), (True, [2, 1], ["Destra"]))
        self.assertEqual(self.notes[-1]["votes"], {"Ajeje": 0, "Lyra": 1, "Hernio": 0})
        self.assertIn({"text": "🗳 Destra o sinistra? — Destra 2 · Sinistra 1", "dice": True}, self.sent)
        self.assertEqual(self.cast("Lyra", 0).get_json()["code"], "closed")

    def test_a_group_votes_alone_and_its_result_is_for_them(self):
        v = self.open_vote(voters=["lyra", "hernio"], group="Collina")
        self.assertEqual((v["voters"], v["group"]), (["Lyra", "Hernio"], "Collina"))
        self.assertEqual(self.cast("Ajeje", 0).get_json()["code"], "voter")
        self.cast("Lyra", 0)
        self.cast("Hernio", 0)
        line = next(m for m in self.sent if str(m.get("text", "")).startswith("🗳"))
        self.assertEqual(line["only"], ["Lyra", "Hernio"])

    def test_the_dm_closes_it_and_a_bad_vote_is_refused(self):
        self.assertEqual(self.client.post("/vote", json={"question": "?", "options": ["Sì"]}).status_code, 400)
        self.assertEqual(self.client.post("/vote", json={"question": "?", "options": ["a", "b"], "voters": ["Gandalf"]}).status_code, 400)
        self.assertEqual(self.client.post("/vote", json={"close": True}).status_code, 409)
        self.open_vote()
        self.cast("Ajeje", 1)
        r = self.client.post("/vote", json={"close": True})
        self.assertEqual((r.status_code, r.get_json()["vote"]["winners"]), (200, ["Sinistra"]))
        self.assertEqual(self.notes, [], "closed by the DM: the result was printed to them, not queued")
        self.assertEqual(self.cast("Lyra", 0).status_code, 409)
        self.assertIn("vote_set", self.mod._DM_ENDPOINTS)
        self.assertNotIn("vote_cast", self.mod._DM_ENDPOINTS)

    def test_the_dm_takes_the_vote_off_the_screens(self):
        self.open_vote()
        self.cast("Ajeje", 1)
        self.client.post("/vote", json={"close": True})
        r = self.client.post("/vote", json={"clear": True})
        self.assertEqual((r.status_code, r.get_json()["vote"]), (200, None))
        self.assertEqual(self.client.get("/vote").get_json()["vote"], None)
        self.assertEqual(self.sent[-1], {"vote_event": {"cleared": True}})
        self.open_vote()                                         # an open one goes too
        m, tmp = self.mod, tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        saved = (m.LOG_FILE, m.STATS_FILE)
        self.addCleanup(lambda: (setattr(m, "LOG_FILE", saved[0]), setattr(m, "STATS_FILE", saved[1])))
        m.LOG_FILE, m.STATS_FILE = os.path.join(tmp, "log.json"), os.path.join(tmp, "stats.json")
        with m._text_log_lock:
            log = list(m._text_log)
        self.addCleanup(lambda: m._text_log.extend(log))
        self.client.post("/clear")
        self.assertEqual(self.client.get("/vote").get_json()["vote"], None, "a new story starts with no vote")

    def test_a_secret_vote_hides_the_count_from_the_players(self):
        self.open_vote(secret=True)
        self.cast("Ajeje", 1)
        with self.mod.app.test_request_context("/vote"):
            self.mod.g.role, self.mod.g.character = "player", "Ajeje"
            with self.mod._vote_lock:
                seen = self.mod._vote_view(self.mod._vote)
            self.assertEqual((seen["counts"], seen["votes"], seen["mine"], seen["voted"]), (None, None, 1, ["Ajeje"]))
            self.mod.g.role = "dm"
            with self.mod._vote_lock:
                self.assertEqual(self.mod._vote_view(self.mod._vote)["counts"], [0, 1])


_SLICE = re.compile(r"(function _votePlan\(vote, me\).*?)\n// ── end vote plan ──", re.S)


class PageTests(unittest.TestCase):
    def test_the_plan_of_one_screen(self):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the vote block is no longer where this test slices it")
        open_vote = {"options": ["Destra", "Sinistra"], "voters": ["Ajeje", "Lyra", "Lio"], "voted": ["Ajeje", "Lyra"],
                     "counts": [1, 1], "votes": {"Ajeje": 1, "Lyra": 0}, "closed": False, "winners": []}
        secret = dict(open_vote, counts=None, votes=None, mine=0)
        closed = dict(open_vote, closed=True, winners=["Destra"], counts=[2, 1], votes={"Ajeje": 1, "Lyra": 0, "Lio": 0}, voted=["Ajeje", "Lyra", "Lio"])
        script = m.group(1) + "\nconsole.log(JSON.stringify([_votePlan(%s, 'ajeje'), _votePlan(%s, 'Lyra'), _votePlan(%s, 'Lio'), _votePlan(%s, 'Thorin'), _votePlan(null, 'x')]));\n" % (
            json.dumps(open_vote), json.dumps(secret), json.dumps(closed), json.dumps(open_vote))
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            ajeje, lyra, lio, thorin, none = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        self.assertEqual([(x["label"], x["count"], x["pct"], x["names"], x["mine"]) for x in ajeje["rows"]],
                         [("Destra", 1, 50, ["Lyra"], False), ("Sinistra", 1, 50, ["Ajeje"], True)])
        self.assertEqual((ajeje["canVote"], ajeje["voted"], ajeje["of"], ajeje["waiting"]), (True, 2, 3, ["Lio"]))
        self.assertEqual((lyra["hidden"], lyra["rows"][0]["mine"], lyra["rows"][0]["count"]), (True, True, None))
        self.assertEqual((lio["closed"], lio["canVote"], lio["rows"][0]["win"], lio["rows"][1]["win"]), (True, False, True, False))
        self.assertEqual((thorin["canVote"], none), (False, None))

    def test_the_page_has_the_panel_and_every_way_in(self):
        src = HTML.read_text(encoding="utf-8")
        for needle in ('id="vote-modal"', 'id="ln-vote"', 'data-ln-proxy="ln-vote"', 'id="ph-vote-opt"',
                       "window._onVote(payload.vote_event)", 'id="ln-i-vote"'):
            self.assertIn(needle, src)

    def test_strings_exist_in_every_language(self):
        keys = ["vote.title", "vote.opened", "vote.voted", "vote.waiting", "vote.secretNote", "vote.yours", "vote.change",
                "vote.result", "vote.tie", "vote.notVoter", "vote.for", "vote.failed", "vote.closed", "vote.toast"]
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
