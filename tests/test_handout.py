"""
test_handout.py — documents the party finds are shown and kept.

The DM hands out a letter, a notice, a journal page… (display/handout.py →
POST /handout). It goes into the story (and its replay) for every screen, and
into the campaign's handouts.json so the players can read it again.

Run from repo root:
    python -m pytest tests/test_handout.py -q
"""
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"
sys.path.insert(0, str(DISPLAY))

LETTER = "Ilde,\nil carico parte alla terza luna — brucia questa lettera."


def _import_app():
    spec = importlib.util.spec_from_file_location(
        "dnd_display_app_handout", str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class HandoutTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.mod = _import_app()
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()
        cls._orig = (cls.mod._handouts_file, cls.mod._media_dir, cls.mod._broadcast,
                     cls.mod._persist_log, cls.mod._persist_tail)
        cls.mod._persist_log = lambda: None
        cls.mod._persist_tail = lambda: None

    @classmethod
    def tearDownClass(cls):
        (cls.mod._handouts_file, cls.mod._media_dir, cls.mod._broadcast,
         cls.mod._persist_log, cls.mod._persist_tail) = cls._orig

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="dnd-test-handouts-"))
        (self.dir / "media").mkdir()
        (self.dir / "media" / "mappa.png").write_bytes(b"\x89PNG")
        self.file = self.dir / "handouts.json"
        self.mod._handouts_file = lambda: str(self.file)
        self.mod._media_dir = lambda: str(self.dir / "media")
        self.sent = []
        self.mod._broadcast = self.sent.append
        self.mod._text_log.clear()

    def _post(self, **body):
        body.setdefault("title", "Lettera del corriere")
        body.setdefault("body", LETTER)
        return self.client.post("/handout", json=body)

    def test_a_handout_is_saved_shown_and_logged_for_replay(self):
        r = self._post(kind="letter", **{"from": "V."})
        self.assertEqual(r.status_code, 200)
        h = r.get_json()
        self.assertEqual((h["title"], h["kind"], h["from"], h["body"]),
                         ("Lettera del corriere", "letter", "V.", LETTER))
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), [h])
        self.assertEqual(self.sent, [{"handout": h}])
        self.assertEqual(list(self.mod._text_log)[-1]["handout"], h)

    def test_kind_defaults_to_letter_and_an_unknown_kind_is_refused(self):
        self.assertEqual(self._post().get_json()["kind"], "letter")
        r = self._post(kind="tablet")
        self.assertEqual(r.status_code, 400)
        self.assertIn("scroll", r.get_json()["kinds"])

    def test_a_handout_needs_a_title_and_something_to_show(self):
        self.assertEqual(self._post(title="  ").status_code, 400)
        self.assertEqual(self._post(body="").status_code, 400)
        self.assertFalse(self.file.exists())
        self.assertEqual(self.sent, [])

    def test_an_image_handout_must_name_a_real_media_file(self):
        self.assertEqual(self._post(body="", image="mappa.png").status_code, 200)
        self.assertEqual(self._post(body="", image="manca.png").status_code, 400)
        self.assertEqual(self._post(body="", image="../handouts.json").status_code, 400)

    def test_players_can_list_what_the_party_holds(self):
        a = self._post(title="Prima").get_json()
        b = self._post(title="Seconda", kind="notice").get_json()
        self.assertEqual(self.client.get("/handouts").get_json(), [a, b])

    def test_showing_an_old_handout_again_does_not_duplicate_it(self):
        h = self._post().get_json()
        self.sent.clear()
        r = self.client.post("/handout", json={"id": h["id"]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.sent, [{"handout": h}])
        self.assertEqual(len(self.client.get("/handouts").get_json()), 1)
        self.assertEqual(self.client.post("/handout", json={"id": "nope"}).status_code, 404)

    def test_removing_a_handout_tells_every_screen(self):
        h = self._post().get_json()
        self.sent.clear()
        self.assertEqual(self.client.delete("/handout/" + h["id"]).status_code, 204)
        self.assertEqual(self.sent, [{"handout_removed": h["id"]}])
        self.assertEqual(self.client.get("/handouts").get_json(), [])
        self.assertEqual(self.client.delete("/handout/" + h["id"]).status_code, 404)

    def test_handing_out_is_the_dms_job_reading_is_everyones(self):
        self.assertIn("handout", self.mod._DM_ENDPOINTS)
        self.assertIn("handout_delete", self.mod._DM_ENDPOINTS)
        self.assertNotIn("handouts_list", self.mod._DM_ENDPOINTS)

    def test_a_corrupt_file_reads_as_no_handouts(self):
        self.file.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.client.get("/handouts").get_json(), [])


class HandoutWiringTests(unittest.TestCase):

    def test_the_page_renders_live_and_replayed_handouts(self):
        src = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("if (payload.handout) renderHandoutBlock(payload.handout);", src)
        self.assertIn("renderHandoutBlock(item.handout, true);", src)
        for kind in ("letter", "notice", "journal", "scroll", "note"):
            with self.subTest(kind=kind):
                self.assertIn(f".handout-paper.kind-{kind}", src)

    def test_cli_and_server_agree_on_the_kinds(self):
        import handout
        self.assertEqual(tuple(handout.KINDS), tuple(_import_app()._HANDOUT_KINDS))

    def test_strings_exist_in_every_language(self):
        keys = ["handout.title", "handout.all", "handout.open", "handout.new", "handout.empty",
                "handout.arrived"] + ["handout.kind." + k for k in ("letter", "notice", "journal", "scroll", "note")]
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
