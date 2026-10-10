"""
test_puzzle.py — a puzzle the players solve on their own phones.

display/puzzle.py makes a combination lock, a riddle or a cipher; the display
server keeps the answer, checks every try and shows each one to the table.
When it is solved, or the attempts run out, the DM is told through
check_input.py / autorun_wait.py.

Run from repo root:
    python -m pytest tests/test_puzzle.py -q
"""
import importlib.util
import json
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

import puzzle as pz  # noqa: E402


class MakingTests(unittest.TestCase):
    def test_a_lock_is_read_from_its_code(self):
        lock = pz.make_lock("Il forziere", "3-7-1", attempts=5, hint="Le date", feedback=True)
        self.assertEqual((lock["symbols"], lock["wheels"], lock["solution"], lock["max_attempts"]), ("digits", 3, [3, 7, 1], 5))
        self.assertEqual(pz.make_lock("x", "371")["solution"], [3, 7, 1])
        letters = pz.make_lock("La porta", "s-o-l")
        self.assertEqual((letters["symbols"], letters["solution"]), ("letters", [18, 14, 11]))
        runes = pz.make_lock("Il sigillo", "2-5-8", symbols="runes")
        self.assertEqual(runes["solution"], [1, 4, 7])
        for code, symbols in (("1", ""), ("1-2-3-4-5-6-7-8-9", ""), ("A-7", "letters"), ("9-9", "runes"), ("1-2", "dadi")):
            with self.assertRaises(pz.PuzzleError):
                pz.make_lock("x", code, symbols=symbols)
        with self.assertRaises(pz.PuzzleError):
            pz.make_lock("", "1-2")

    def test_a_riddle_needs_words_and_an_answer(self):
        riddle = pz.make_riddle("L'enigma", "Parlo senza bocca.", ["Eco", "un'eco"])
        self.assertEqual((riddle["answers"], riddle["answer_shown"]), (["eco"], "Eco"))
        with self.assertRaises(pz.PuzzleError):
            pz.make_riddle("L'enigma", "Parlo senza bocca.", [" ", "!"])
        with self.assertRaises(pz.PuzzleError):
            pz.make_riddle("L'enigma", "", ["eco"])

    def test_answers_forgive_case_accents_punctuation_and_an_article(self):
        for text in ("eco", "ECO!", " L'Eco ", "l’eco", "un'eco.", "The echo"):
            self.assertIn(pz.normalise_answer(text), ("eco", "echo"), text)
        self.assertEqual(pz.normalise_answer("È la Città"), "elacitta")
        self.assertEqual(pz.normalise_answer("…"), "")

    def test_a_cipher_scrambles_the_message_and_keeps_no_accent_to_give_it_away(self):
        cipher = pz.make_cipher("Il biglietto", "La chiave è sotto la terza pietra", 7)
        self.assertEqual(cipher["cipher"], "Sh jophcl l zvaav sh alygh wplayh")
        self.assertEqual(pz.caesar(cipher["cipher"], -7), "La chiave e sotto la terza pietra")
        self.assertEqual(pz.caesar("xyz XYZ 12!", 3), "abc ABC 12!")
        for plain, shift in (("ab", 3), ("la chiave", 0), ("la chiave", 26)):
            with self.assertRaises(pz.PuzzleError):
                pz.make_cipher("x", plain, shift)


class TryingTests(unittest.TestCase):
    def test_a_wrong_try_is_recorded_and_the_right_one_solves(self):
        lock = pz.make_lock("Il forziere", "3-7-1", feedback=True)
        wrong = pz.attempt(lock, "Mira", [3, 1, 1], now=10)
        self.assertEqual(wrong, {"ok": False, "shown": "3-1-1", "feedback": "2"})
        right = pz.attempt(lock, "Aldric", [3, 7, 1], now=20)
        self.assertEqual((right["ok"], lock["solved"], lock["solved_by"], lock["used"]), (True, True, "Aldric", 2))
        self.assertEqual([(t["who"], t["guess"], t["ok"]) for t in lock["tries"]],
                         [("Mira", "3-1-1", False), ("Aldric", "3-7-1", True)])
        with self.assertRaises(pz.PuzzleError):
            pz.attempt(lock, "Lio", [0, 0, 0], now=30)                 # it is over

    def test_without_feedback_a_lock_says_only_no(self):
        lock = pz.make_lock("Il forziere", "3-7-1")
        self.assertEqual(pz.attempt(lock, "Mira", [3, 7, 0], now=10)["feedback"], "")

    def test_the_attempts_of_the_table_run_out(self):
        riddle = pz.make_riddle("L'enigma", "Chi sono?", ["eco"], attempts=2)
        pz.attempt(riddle, "Lio", "il vento", now=10)
        self.assertFalse(riddle["failed"])
        pz.attempt(riddle, "Mira", "la voce", now=20)
        self.assertTrue(riddle["failed"])
        self.assertIn('FAILED — the 2 attempts are spent (last: Mira: la voce)', pz.outcome_line(riddle))
        with self.assertRaises(pz.PuzzleError):
            pz.attempt(riddle, "Lio", "eco", now=30)

    def test_a_riddle_takes_any_accepted_answer_however_written(self):
        riddle = pz.make_riddle("L'enigma", "Chi sono?", ["eco", "il rimbombo"])
        self.assertTrue(pz.attempt(riddle, "Lio", " L'ECO! ", now=10)["ok"])
        self.assertIn('SOLVED by Lio (L\'ECO!) at try 1', pz.outcome_line(riddle))

    def test_a_cipher_is_solved_by_the_right_turn_of_the_wheel(self):
        cipher = pz.make_cipher("Il biglietto", "la chiave e sotto la pietra", 7)
        self.assertEqual(pz.attempt(cipher, "Mira", 3, now=10)["ok"], False)
        got = pz.attempt(cipher, "Mira", 7, now=20)
        self.assertEqual((got["ok"], got["shown"]), (True, "la chiave e sotto la pietra"))

    def test_tries_cannot_be_hammered_or_malformed(self):
        lock = pz.make_lock("Il forziere", "3-7-1")
        pz.attempt(lock, "Mira", [0, 0, 0], now=10)
        with self.assertRaises(pz.PuzzleError):
            pz.attempt(lock, "Mira", [0, 0, 1], now=10.5)              # too soon
        for bad in ([1, 2], [1, 2, 99], ["a", "b", "c"], "371", None):
            with self.assertRaises(pz.PuzzleError):
                pz.attempt(lock, "Mira", bad, now=100)
        self.assertEqual(lock["used"], 1)
        riddle = pz.make_riddle("L'enigma", "Chi sono?", ["eco"])
        with self.assertRaises(pz.PuzzleError):
            pz.attempt(riddle, "Lio", "  ", now=10)
        cipher = pz.make_cipher("Il biglietto", "la chiave", 7)
        with self.assertRaises(pz.PuzzleError):
            pz.attempt(cipher, "Lio", "sette", now=10)

    def test_the_screens_never_get_the_answer(self):
        lock = pz.make_lock("Il forziere", "3-7-1", hint="Le date sulla lapide")
        riddle = pz.make_riddle("L'enigma", "Chi sono?", ["eco"])
        cipher = pz.make_cipher("Il biglietto", "la chiave e sotto la pietra", 7)
        for puzzle, secrets in ((lock, ("solution", "Le date")), (riddle, ("answers", "answer_shown", "eco")),
                                (cipher, ("solution", "plain", "la chiave"))):
            text = json.dumps(pz.public_view(dict(puzzle, id="x")), ensure_ascii=False)
            for secret in secrets:
                self.assertNotIn(secret, text, puzzle["kind"])
        lock["hint_shown"] = True
        self.assertEqual(pz.public_view(lock)["hint"], "Le date sulla lapide")
        pz.attempt(cipher, "Mira", 7, now=10)
        self.assertEqual(pz.public_view(cipher)["plain"], "la chiave e sotto la pietra")   # once solved, it reads
        self.assertIsNone(pz.public_view(None))


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_puzzle", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        self.mod._puzzle = None
        self.sent, self.news = [], []
        self._saved = (self.mod._broadcast, self.mod._persist_puzzle, self.mod._puzzle_lib.news_add, self.mod._time.time)
        self.mod._broadcast = self.sent.append
        self.mod._persist_puzzle = lambda: None
        self.mod._puzzle_lib.news_add = self.news.append
        self.clock = [1000.0]
        self.mod._time.time = lambda: self.clock[0]

    def tearDown(self):
        (self.mod._broadcast, self.mod._persist_puzzle, self.mod._puzzle_lib.news_add, self.mod._time.time) = self._saved
        self.mod._puzzle = None

    def attempt(self, who, guess):
        self.clock[0] += 5
        r = self.client.post("/puzzle/try", json={"character": who, "guess": guess})
        return r.status_code, r.get_json()

    def test_a_puzzle_goes_on_every_screen_without_its_answer(self):
        r = self.client.post("/puzzle", json={"puzzle": pz.make_lock("Il forziere", "3-7-1", attempts=3)})
        self.assertEqual(r.status_code, 200)
        view = r.get_json()
        self.assertTrue(view["id"])
        self.assertNotIn("solution", view)
        self.assertEqual(self.sent[-1]["puzzle_event"], {"what": "new", "title": "Il forziere"})
        self.assertEqual(self.client.get("/puzzle").get_json()["title"], "Il forziere")
        for bad in ({}, {"puzzle": {"kind": "lock", "title": "x"}}, {"puzzle": {"kind": "sudoku", "title": "x"}}):
            self.assertEqual(self.client.post("/puzzle", json=bad).status_code, 400)

    def test_every_try_is_shown_and_the_dm_is_told_when_it_is_over(self):
        self.client.post("/puzzle", json={"puzzle": pz.make_lock("Il forziere", "3-7-1", attempts=3, feedback=True)})
        code, got = self.attempt("Mira", [3, 1, 1])
        self.assertEqual((code, got["ok"]), (200, False))
        event = self.sent[-1]["puzzle_event"]
        self.assertEqual((event["who"], event["guess"], event["ok"], event["feedback"]), ("Mira", "3-1-1", False, "2"))
        self.assertEqual(self.news, [])
        code, got = self.attempt("Aldric", [3, 7, 1])
        self.assertTrue(got["ok"] and got["puzzle"]["solved"])
        self.assertEqual(len(self.news), 1)
        self.assertIn('"Il forziere" SOLVED by Aldric', self.news[0])
        self.assertEqual(self.attempt("Lio", [0, 0, 0])[0], 409)

    def test_running_out_of_attempts_is_told_too(self):
        self.client.post("/puzzle", json={"puzzle": pz.make_riddle("L'enigma", "Chi sono?", ["eco"], attempts=1)})
        code, got = self.attempt("Lio", "il vento")
        self.assertTrue(got["puzzle"]["failed"])
        self.assertTrue(self.sent[-1]["puzzle_event"]["failed"])
        self.assertIn("FAILED", self.news[0])

    def test_a_try_too_soon_or_malformed_changes_nothing(self):
        self.client.post("/puzzle", json={"puzzle": pz.make_lock("Il forziere", "3-7-1")})
        self.attempt("Mira", [0, 0, 0])
        r = self.client.post("/puzzle/try", json={"character": "Mira", "guess": [3, 7, 1]})   # same instant
        self.assertEqual((r.status_code, r.get_json()["code"]), (409, "wait"))
        self.assertEqual(self.attempt("Mira", [1])[1]["code"], "bad")
        self.assertEqual(self.client.get("/puzzle").get_json()["used"], 1)

    def test_hint_and_close_are_the_dms(self):
        self.assertEqual(self.client.post("/puzzle/hint", json={}).status_code, 404)
        self.client.post("/puzzle", json={"puzzle": pz.make_lock("Il forziere", "3-7-1", hint="Le date")})
        self.assertEqual(self.client.get("/puzzle").get_json()["hint"], "")
        self.assertEqual(self.client.post("/puzzle/hint", json={}).get_json()["hint"], "Le date")
        self.assertEqual(self.client.post("/puzzle/hint", json={"text": "Guardate la lapide"}).get_json()["hint"], "Guardate la lapide")
        self.assertEqual(self.client.delete("/puzzle").status_code, 200)
        self.assertEqual(self.sent[-1], {"puzzle": None})
        self.assertEqual(self.client.get("/puzzle").get_json(), {})
        self.assertEqual(self.attempt("Mira", [1, 2, 3])[0], 404)
        for name in ("puzzle_open", "puzzle_close", "puzzle_hint", "puzzle_status"):
            self.assertIn(name, self.mod._DM_ENDPOINTS)
        self.assertNotIn("puzzle_try", self.mod._DM_ENDPOINTS)
        self.assertNotIn("puzzle_get", self.mod._DM_ENDPOINTS)


class NewsTests(unittest.TestCase):
    def test_outcomes_wait_for_the_dm_and_are_told_once(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old = pz.NEWS_FILE
        pz.NEWS_FILE = str(pathlib.Path(tmp.name) / "puzzle_news.json")
        self.addCleanup(lambda: setattr(pz, "NEWS_FILE", old))
        self.assertFalse(pz.news_pending())
        self.assertEqual(pz.drain_news(), [])
        pz.news_add("[[PUZZLE uno]]")
        pz.news_add("[[PUZZLE due]]")
        self.assertTrue(pz.news_pending())
        self.assertEqual(pz.drain_news(), ["[[PUZZLE uno]]", "[[PUZZLE due]]"])
        self.assertFalse(pz.news_pending())

    def test_the_dms_scripts_read_them(self):
        self.assertIn("from puzzle import drain_news", (DISPLAY / "check_input.py").read_text(encoding="utf-8"))
        wait = (DISPLAY / "autorun_wait.py").read_text(encoding="utf-8")
        self.assertIn("_puzzle.news_pending()", wait)
        self.assertIn("_puzzle.drain_news()", wait)


_SLICE = re.compile(r"(function _puzzleSpin\(value, size, by\).*?)\n// ── end puzzle helpers ──", re.S)


class PageTests(unittest.TestCase):
    def test_the_wheels_turn_round_and_the_cipher_reads_like_the_scripts(self):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the puzzle-helpers block is no longer where this test slices it")
        cipher = pz.make_cipher("x", "La chiave è sotto la terza pietra, 3 passi!", 7)["cipher"]
        script = m.group(1) + ("\nconsole.log(JSON.stringify([_puzzleSpin(9, 10, 1), _puzzleSpin(0, 10, -1), _puzzleSpin(3, 26, 1),"
                               " _puzzleDecode(%s, 7), _puzzleDecode(%s, 0), _puzzleDecode('abc', 29)]));\n"
                               % (json.dumps(cipher), json.dumps(cipher)))
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            got = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        self.assertEqual(got[:3], [0, 9, 4])
        self.assertEqual(got[3], "La chiave e sotto la terza pietra, 3 passi!")
        self.assertEqual(got[4], cipher)
        self.assertEqual(got[5], pz.caesar("abc", -29))

    def test_the_page_has_the_puzzle_and_every_way_in(self):
        src = HTML.read_text(encoding="utf-8")
        for needle in ('id="puzzle-modal"', 'id="ln-puzzle"', 'data-ln-proxy="ln-puzzle"', 'id="ph-puzzle-opt"',
                       "fetch('/puzzle/try'", "('puzzle' in payload || payload.puzzle_event)"):
            self.assertIn(needle, src)

    def test_strings_exist_in_every_language(self):
        keys = ["puzzle.title", "puzzle.table", "puzzle.try", "puzzle.answer", "puzzle.answerPh", "puzzle.this",
                "puzzle.turn", "puzzle.left", "puzzle.left1", "puzzle.solved", "puzzle.failed", "puzzle.hint",
                "puzzle.right", "puzzle.new", "puzzle.err.wait", "puzzle.err.bad"]
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
