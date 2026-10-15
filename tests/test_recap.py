"""
test_recap.py — "Previously on…" on every screen at the start of a session.

recap.py sends the DM's recap; the display keeps it for the campaign being
played and shows it as a full-screen card, which opens by itself once per
device and is read by the narrator where narration is on.

Run from repo root:
    python -m pytest tests/test_recap.py -q
"""
import contextlib
import importlib.util
import io
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"
HTML = DISPLAY / "templates" / "index.html"
NODE = shutil.which("node")
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(SKILL / "scripts"))

import recap  # noqa: E402


class CommandTests(unittest.TestCase):
    def test_nothing_or_too_much_is_refused_before_the_display(self):
        for argv, why in ((["--text", "   "], "no text"), (["--text", "x" * 4001], "4000 at most")):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(recap.main(argv), 1)
            self.assertIn(why, err.getvalue())


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_recap", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        m = self.mod
        saved = (m.RECAP_FILE, m._broadcast, m._active_campaign)
        self.addCleanup(lambda: setattr(m, "RECAP_FILE", saved[0]) or setattr(m, "_broadcast", saved[1])
                        or setattr(m, "_active_campaign", saved[2]))
        m.RECAP_FILE = str(pathlib.Path(tmp.name) / "recap.json")
        self.sent = []
        m._broadcast = self.sent.append
        self.camp = "corona"
        m._active_campaign = lambda: self.camp

    def test_shown_kept_for_its_campaign_and_cleared(self):
        self.assertEqual(self.client.get("/recap").get_json(), {"recap": None})
        self.assertEqual(self.client.post("/recap", json={"text": "  "}).status_code, 400)
        r = self.client.post("/recap", json={"text": "La compagnia ha lasciato la Rocca.\n\nIl Giuramento attende.", "title": "Nelle puntate precedenti…"})
        self.assertEqual(r.status_code, 200)
        sent = r.get_json()["recap"]
        self.assertEqual((sent["title"], sent["campaign"]), ("Nelle puntate precedenti…", "corona"))
        self.assertAlmostEqual(sent["at"], time.time(), delta=5)
        self.assertEqual(self.sent[-1], {"recap": sent, "recap_new": True})
        self.assertEqual(self.client.get("/recap").get_json()["recap"]["id"], sent["id"])
        self.camp = "altra"
        self.assertIsNone(self.client.get("/recap").get_json()["recap"], "another campaign's recap is not shown")
        self.camp = "corona"
        self.assertEqual(self.client.post("/recap", json={"clear": True}).get_json(), {"recap": None})
        self.assertEqual((self.client.get("/recap").get_json(), self.sent[-1]), ({"recap": None}, {"recap": None}))
        self.assertIn("recap_set", self.mod._DM_ENDPOINTS)


_SLICE = re.compile(r"(function _recapParagraphs\(text\).*?)\n// ── end recap helpers ──", re.S)


class PageTests(unittest.TestCase):
    def test_paragraphs_and_when_the_card_opens_by_itself(self):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the recap block is no longer where this test slices it")
        script = m.group(1) + """
const r = {id: 'a1', at: 1000};
console.log(JSON.stringify([
  _recapParagraphs('Uno\\r\\ndue.\\n\\n  Tre   quattro.\\n \\n'), _recapParagraphs(''),
  _recapShouldOpen(r, '', 1000 + 3600), _recapShouldOpen(r, 'a1', 1000 + 60), _recapShouldOpen(r, '', 1000 + 13 * 3600),
  _recapShouldOpen(null, '', 0),
]));
"""
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            out = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        self.assertEqual(out, [["Uno due.", "Tre quattro."], [], True, False, False, False])

    def test_the_page_has_the_card_and_every_way_in(self):
        src = HTML.read_text(encoding="utf-8")
        for needle in ('id="recap-modal"', 'id="ln-recap"', 'data-ln-proxy="ln-recap"', 'id="ph-recap-opt"',
                       "window._onRecap(payload)", 'id="ln-i-recap"', "_ttsPlayBlock(btn, $('recap-text'))"):
            self.assertIn(needle, src)

    def test_strings_exist_in_every_language(self):
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("recap.title", "recap.menu", "recap.listen", "recap.go"):
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
