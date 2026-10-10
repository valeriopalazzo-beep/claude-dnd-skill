"""
test_whisper.py — a secret note reaches one player and nobody else.

The DM sends a note to one character (display/whisper.py → POST /whisper);
a player can write back. The note must never reach another player's browser
or the shared table screen, and a player's note must land in the DM's inbox
(read by check_input.py / autorun_wait.py) with instructions not to narrate it.

Run from repo root:
    python -m pytest tests/test_whisper.py -q
"""
import importlib.util
import os
import pathlib
import subprocess
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"
sys.path.insert(0, str(DISPLAY))


def _import_app():
    spec = importlib.util.spec_from_file_location(
        "dnd_display_app_whisper", str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class WhisperTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.mod = _import_app()
        cls.mod._token_ok = lambda: True
        cls.mod._rate_ok = lambda ip: True
        cls.client = cls.mod.app.test_client()
        cls.inbox = pathlib.Path(cls.mod._whisper.INBOX_FILE)

    def setUp(self):
        self.mod._current_stats = {"players": [{"name": "Aldric"}, {"name": "Mira"}]}
        self.mod._whispers.clear()
        self.inbox.unlink(missing_ok=True)
        self.sent = []
        self._orig_broadcast = self.mod._broadcast
        self.mod._broadcast = self.sent.append

    def tearDown(self):
        self.mod._broadcast = self._orig_broadcast
        self.inbox.unlink(missing_ok=True)

    def _dm_note(self, **extra):
        body = {"to": "aldric", "text": "Dietro l'arazzo qualcuno respira — non sei solo."}
        body.update(extra)
        return self.client.post("/whisper", json=body)

    # ── DM → player ──────────────────────────────────────────────────────────

    def test_dm_note_is_stored_with_the_party_spelling_and_untouched_text(self):
        r = self._dm_note()
        self.assertEqual(r.status_code, 200)
        note = r.get_json()
        self.assertEqual((note["from"], note["to"]), ("dm", "Aldric"))
        self.assertEqual(note["text"], "Dietro l'arazzo qualcuno respira — non sei solo.")
        self.assertEqual(self.sent, [{"whisper": note}])
        self.assertFalse(self.inbox.exists(), "a DM note is not mail for the DM")

    def test_unknown_recipient_is_refused_and_names_the_party(self):
        r = self._dm_note(to="Gandalf")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.get_json()["party"], ["Aldric", "Mira"])
        self.assertEqual(self.sent, [])

    def test_empty_note_is_refused(self):
        self.assertEqual(self._dm_note(text="   ").status_code, 400)

    # ── who gets to see it ───────────────────────────────────────────────────

    def test_only_the_recipient_reads_the_note(self):
        payload = {"whisper": self._dm_note().get_json()}
        f = self.mod._whisper_for
        self.assertEqual(f(payload, "aldric", "aldric"), payload)        # logged-in Aldric
        self.assertEqual(f(payload, None, "aldric"), payload)            # phone view on the DM's PC
        for viewer, bound in (("mira", "mira"), (None, ""), (None, "mira")):
            with self.subTest(viewer=viewer, bound=bound):
                seen = f(payload, viewer, bound)
                self.assertEqual(seen, {"whisper_teaser": {"to": "Aldric"}})
                self.assertNotIn("respira", str(seen))

    def test_without_teaser_the_others_get_nothing_at_all(self):
        payload = {"whisper": self._dm_note(teaser=False).get_json()}
        self.assertIsNone(self.mod._whisper_for(payload, "mira", "mira"))
        self.assertIsNone(self.mod._whisper_for(payload, None, ""))
        self.assertEqual(self.mod._whisper_for(payload, "aldric", "aldric"), payload)

    def test_other_broadcasts_pass_through_untouched(self):
        payload = {"stats": {"players": []}}
        self.assertIs(self.mod._whisper_for(payload, "mira", "mira"), payload)
        self.assertIsNone(self.mod._whisper_for(None, "mira", "mira"))

    # ── player → DM ──────────────────────────────────────────────────────────

    def test_player_note_goes_to_the_dm_inbox_and_to_no_other_screen(self):
        r = self.client.post("/whisper", json={"from": "Mira", "text": "Sfilo la chiave ad Aldric di nascosto"})
        self.assertEqual(r.status_code, 200)
        note = r.get_json()
        self.assertEqual((note["from"], note["to"]), ("Mira", "dm"))
        payload = {"whisper": note}
        self.assertEqual(self.mod._whisper_for(payload, "mira", "mira"), payload)
        self.assertIsNone(self.mod._whisper_for(payload, "aldric", "aldric"))
        self.assertIsNone(self.mod._whisper_for(payload, None, ""), "not even a hint on the table screen")

        lines = self.mod._whisper.drain_inbox()
        self.assertEqual(len(lines), 1)
        self.assertIn("SECRET NOTE from Mira", lines[0])
        self.assertIn('whisper.py --to "Mira"', lines[0])
        self.assertTrue(lines[0].endswith("Sfilo la chiave ad Aldric di nascosto"))
        self.assertEqual(self.mod._whisper.drain_inbox(), [], "reading the inbox clears it")

    def test_a_logged_in_player_cannot_write_as_the_dm_or_as_someone_else(self):
        with self.mod.app.test_request_context(
                "/whisper", method="POST",
                json={"to": "Aldric", "from": "Aldric", "text": "Fidati di Mira"}):
            self.mod.g.role, self.mod.g.character = "player", "mira"
            resp = self.mod.whisper()
        note = resp[0].get_json()
        self.assertEqual((note["from"], note["to"]), ("Mira", "dm"))

    def test_a_name_outside_the_party_cannot_write(self):
        r = self.client.post("/whisper", json={"from": "Intruso", "text": "ciao"})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(self.inbox.exists())

    def test_shell_characters_are_stripped_from_a_player_note(self):
        r = self.client.post("/whisper", json={"from": "Mira", "text": "apro `rm -rf` $(la porta)"})
        self.assertEqual(r.get_json()["text"], "apro rm -rf la porta")

    # ── reading the thread back ──────────────────────────────────────────────

    def test_thread_lists_one_character_and_one_campaign(self):
        self._dm_note()
        self.client.post("/whisper", json={"from": "Aldric", "text": "Chi e"})
        self.client.post("/whisper", json={"to": "Mira", "text": "Per Mira"})
        self.mod._whispers.append({"id": "old", "from": "dm", "to": "Aldric", "text": "Altra campagna",
                                   "ts": 1.0, "campaign": "un-altra"})
        thread = self.client.get("/whispers?character=aldric").get_json()
        self.assertEqual([n["text"] for n in thread],
                         ["Dietro l'arazzo qualcuno respira — non sei solo.", "Chi e"])
        self.assertEqual(self.client.get("/whispers").get_json(), [], "no character named, nothing shown")
        self.assertEqual(len(self.client.get("/whispers?all=1").get_json()), 3)

    def test_a_logged_in_player_only_ever_gets_their_own_thread(self):
        self._dm_note()
        self.client.post("/whisper", json={"to": "Mira", "text": "Per Mira"})
        with self.mod.app.test_request_context("/whispers?character=Aldric&all=1"):
            self.mod.g.role, self.mod.g.character = "player", "mira"
            notes = self.mod.whispers_list()[0].get_json()
        self.assertEqual([n["text"] for n in notes], ["Per Mira"])

    # ── the DM's turn check hands the note over ──────────────────────────────

    def test_check_input_prints_the_secret_note_even_with_no_action_queued(self):
        self.client.post("/whisper", json={"from": "Mira", "text": "Tengo d'occhio il mercante"})
        env = dict(os.environ, PYTHONUTF8="1")
        out = subprocess.run([sys.executable, str(DISPLAY / "check_input.py")],
                             capture_output=True, text=True, encoding="utf-8", env=env, timeout=30).stdout
        self.assertIn("SECRET NOTE from Mira", out)
        self.assertIn("Tengo d'occhio il mercante", out)
        self.assertFalse(self.inbox.exists())


class WhisperWiringTests(unittest.TestCase):

    def test_stream_filters_every_client_through_the_whisper_gate(self):
        src = (DISPLAY / "dnd-display-app.py").read_text(encoding="utf-8")
        self.assertIn("_whisper_for(_for_viewer(q.get(timeout=5), _viewer), _viewer, _ch)", src)

    def test_strings_exist_in_every_language(self):
        import json
        keys = ("whisper.title", "whisper.hint", "whisper.placeholder", "whisper.send", "whisper.empty",
                "whisper.dm", "whisper.you", "whisper.arrived", "whisper.failed", "whisper.teaser")
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
