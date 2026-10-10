"""
test_campaign_book.py — the chronicle of a campaign as a book.

scripts/campaign_book.py lays out what the campaign already holds — the session
log, the party, the pictures of places, the battle maps, the handouts — as
HTML and PDF. What is the DM's alone (calibration, open threads, XP) is never
printed.

Run from repo root:
    python -m pytest tests/test_campaign_book.py -q
"""
import contextlib
import datetime
import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
sys.path.insert(0, str(SKILL / "scripts"))

LOG = """# Session Log — prova

## Session 2 — 2026-10-03 — La scala di Orm
**Location:** galleria → Sala del Giuramento | **In-world date:** 13 Brumosa 1147 | **Duration:** una notte

### Recap
La Confraternita fa crollare la galleria. Il gruppo **insegue** i rapitori.

### Key Events
- Hernio legge la lettera del Duca.
- Tomas è libero.

### Decisions Made
- Scendono invece di tornare indietro.

### XP Awarded
- 75 PE a testa

### Loot & Changes
- Lio: 50 mo

### Open Threads Heading into Next Session
- Gunda è una falsa pista (segreto del DM).

### Session Recap
Davanti alla saracinesca, qualcuno li chiama per nome.

### DM Calibration
- **What worked:** il ritmo
- **Adjust next time:** meno PNG

---

## Session Template

## Session X — <date>
### Recap
<3-5 sentence summary>
"""

ARCHIVE = """# Archive

## Session 1 — 2026-10-02 — Arrivo a Vallombra
**Location:** Vallombra | **In-world date:** 12 Brumosa 1147

### Recap
La Compagnia arriva al tramonto.

## Session 2 — 2026-10-03 — vecchia stesura
### Recap
Questa versione è superata.
"""

SHEET = "# {name}\n\n## Identity\n- **Race:** Nano | **Class:** Barbaro | **Level:** 2 | **Background:** Eremita\n"


def _load():
    spec = importlib.util.spec_from_file_location("campaign_book_under_test", SKILL / "scripts" / "campaign_book.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class BookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.book = _load()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._old = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_LANG")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        os.environ.pop("DND_LANG", None)
        self.camp = self.root / "campaigns" / "corona-di-cenere"
        (self.camp / "characters").mkdir(parents=True)
        (self.camp / "media").mkdir()
        (self.camp / "state.md").write_text("# Stato\n\n## Session Flags\n- language: it\n\n## DM Notes\n- il re è morto\n",
                                            encoding="utf-8")
        (self.camp / "session-log.md").write_text(LOG, encoding="utf-8")
        (self.camp / "session-log-archive.md").write_text(ARCHIVE, encoding="utf-8")
        for name in ("Lio", "Lyra Ombrafina"):
            (self.camp / "characters" / (name.lower().replace(" ", "-") + ".md")).write_text(
                SHEET.format(name=name), encoding="utf-8")
        self._picture("scene-vallombra.png", "scene", "Vallombra", "2026-10-02T21:19:08")
        self._picture("scene-sala.png", "scene", "Sala del Giuramento", "2026-10-04T00:40:00")   # after midnight
        self._picture("portrait-lio.png", "portrait", "Lio", "2026-10-02T23:30:00")
        self._picture("mapart-abc.png", "map-art", "stone hall", "2026-10-03T23:00:00")
        for name, when in (("map-la-scala-di-orm-aaaaaaaa.svg", (2026, 10, 3, 23, 31)),
                           ("map-la-scala-di-orm-bbbbbbbb.svg", (2026, 10, 3, 23, 44))):
            path = self.camp / "media" / name
            path.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text x="5" y="24" font-size="19">La scala di Orm</text></svg>',
                            encoding="utf-8")
            stamp = datetime.datetime(*when).timestamp()
            os.utime(path, (stamp, stamp))
        (self.camp / "handouts.json").write_text(json.dumps([
            {"id": "a1", "title": "Lettera del Duca", "kind": "letter", "body": "Non interferirai\nfinché il rito non sarà compiuto.",
             "from": "Il Duca"}]), encoding="utf-8")

    def tearDown(self):
        for key, old in self._old.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        self._tmp.cleanup()

    def _picture(self, name, kind, subject, created):
        media = self.camp / "media"
        (media / name).write_bytes(b"\x89PNG fake")
        index = media / "index.json"
        items = json.loads(index.read_text(encoding="utf-8")) if index.exists() else []
        items.append({"file": name, "kind": kind, "subject": subject, "created": created})
        index.write_text(json.dumps(items), encoding="utf-8")

    def make(self, **kwargs):
        kwargs.setdefault("to_pdf", lambda page: None)
        done = self.book.make("corona-di-cenere", **kwargs)
        return done, done["html"].read_text(encoding="utf-8")

    def test_a_session_entry_is_read_into_its_parts(self):
        sessions = self.book.parse_sessions(LOG)
        self.assertEqual([s["number"] for s in sessions], [2])               # the template is not a session
        s = sessions[0]
        self.assertEqual((s["date"], s["title"]), ("2026-10-03", "La scala di Orm"))
        self.assertEqual(s["meta"]["location"], "galleria → Sala del Giuramento")
        self.assertEqual(s["meta"]["in-world date"], "13 Brumosa 1147")
        self.assertEqual(set(s["sections"]), {"recap", "events", "decisions", "loot", "closing"})
        self.assertEqual(s["sections"]["closing"], "Davanti alla saracinesca, qualcuno li chiama per nome.")

    def test_the_archive_supplies_the_old_sessions_and_the_live_log_wins(self):
        sessions = self.book.load_sessions(self.camp)
        self.assertEqual([(s["number"], s["title"]) for s in sessions], [(1, "Arrivo a Vallombra"), (2, "La scala di Orm")])

    def test_a_picture_goes_to_the_evening_it_was_made(self):
        sessions = self.book.load_sessions(self.camp)
        pictures, faces = self.book.load_pictures(self.camp)
        by_session = self.book.assign_pictures(sessions, pictures)
        self.assertEqual([p["caption"] for p in by_session[1]], ["Vallombra"])
        self.assertEqual([(p["kind"], p["caption"], p["file"].name) for p in by_session[2]],
                         [("map", "La scala di Orm", "map-la-scala-di-orm-bbbbbbbb.svg"),   # the map's last state only
                          ("scene", "Sala del Giuramento", "scene-sala.png")])
        self.assertEqual(faces, {})                                          # no portraits unless asked

    def test_the_book_tells_the_story_and_keeps_the_dms_pages_out(self):
        done, page = self.make()
        self.assertEqual((done["sessions"], done["pictures"], done["pdf"]), (2, 3, None))
        self.assertEqual(done["html"], self.camp / "book" / "corona-di-cenere.html")
        for wanted in ("<h1>Corona di Cenere</h1>", "Cronaca della campagna", "Lio · Lyra Ombrafina", "La compagnia",
                       "Nano Barbaro · livello 2", "Sessione 1 — Arrivo a Vallombra", "Sessione 2 — La scala di Orm",
                       "<strong>insegue</strong>", "<li>Tomas è libero.</li>", "Le scelte", "Davanti alla saracinesca",
                       "scene-vallombra.png", "map-la-scala-di-orm-bbbbbbbb.svg", "Documenti trovati", "Lettera del Duca",
                       "finché il rito non sarà compiuto.", "— Il Duca"):
            self.assertIn(wanted, page)
        for secret in ("falsa pista", "What worked", "75 PE", "Lio: 50 mo", "il re è morto", "vecchia stesura",
                       "portrait-lio.png", "mapart-abc.png", "map-la-scala-di-orm-aaaaaaaa.svg", "Session Template"):
            self.assertNotIn(secret, page)

    def test_options_add_loot_portraits_a_title_and_a_range(self):
        _, page = self.make(loot=True, portraits=True, title="La Corona <di> Cenere", sessions_range="2", lang="en")
        self.assertIn("La Corona &lt;di&gt; Cenere", page)
        self.assertIn("Lio: 50 mo", page)
        self.assertIn("portrait-lio.png", page)
        self.assertIn("Session 2 — La scala di Orm", page)
        self.assertNotIn("Arrivo a Vallombra", page)
        self.assertIn("Chronicle of the campaign", page)

    def test_the_pdf_is_written_when_a_browser_can_print_it(self):
        seen = []
        done, page = self.make(to_pdf=lambda text: seen.append(text) or b"%PDF-1.7 fake %%EOF", out_dir=str(self.root / "fuori"))
        self.assertEqual(seen, [page])
        self.assertEqual(done["pdf"], self.root / "fuori" / "corona-di-cenere.pdf")
        self.assertEqual(done["pdf"].read_bytes(), b"%PDF-1.7 fake %%EOF")
        self.assertFalse((self.camp / "book").exists())
        done, _ = self.make(pdf=False, to_pdf=lambda text: self.fail("no PDF was asked for"))
        self.assertIsNone(done["pdf"])

    def test_the_title_is_made_from_the_campaign_name(self):
        self.assertEqual(self.book.pretty_title("corona-di-cenere"), "Corona di Cenere")
        self.assertEqual(self.book.pretty_title("the-iron_vault"), "The Iron Vault")

    def test_nothing_to_print_is_said_plainly(self):
        (self.camp / "session-log.md").write_text("# Session Log\n\n*(No sessions yet.)*\n", encoding="utf-8")
        (self.camp / "session-log-archive.md").unlink()
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.book.main(["--campaign", "corona-di-cenere", "--no-pdf"]), 1)
            self.assertEqual(self.book.main(["--campaign", "non-esiste", "--no-pdf"]), 1)
            self.assertEqual(self.book.main(["--campaign", "../corona-di-cenere", "--no-pdf"]), 1)
        self.assertIn("no session to print yet", err.getvalue())
        self.assertFalse((self.camp / "book").exists())
        with self.assertRaises(self.book.BookError):
            self.book.make("corona-di-cenere", sessions_range="tre")


class DisplayTests(unittest.TestCase):
    """Once a book exists the display offers it to every player."""

    def test_the_last_book_can_be_downloaded_by_everyone(self):
        display = SKILL / "display"
        sys.path.insert(0, str(display))
        spec = importlib.util.spec_from_file_location("dnd_display_app_book", str(display / "dnd-display-app.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod._token_ok = lambda: True
        client = mod.app.test_client()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        camp = pathlib.Path(tmp.name)
        sent = []
        mod._active_campaign, mod._find_campaign, mod._broadcast = (lambda: "prova"), (lambda n: camp), sent.append

        self.assertEqual(client.get("/book/info").get_json(), {"available": False})
        self.assertEqual(client.get("/book").status_code, 404)
        (camp / "book").mkdir()
        (camp / "book" / "prova.pdf").write_bytes(b"%PDF-1.7 fake %%EOF")
        info = client.get("/book/info").get_json()
        self.assertEqual((info["available"], info["size"]), (True, 19))
        got = client.get("/book")
        self.assertEqual((got.status_code, got.mimetype, got.data), (200, "application/pdf", b"%PDF-1.7 fake %%EOF"))
        self.assertIn("prova.pdf", got.headers["Content-Disposition"])
        got.close()
        self.assertEqual(client.post("/book").status_code, 204)
        self.assertTrue(sent[-1]["book"]["available"])
        self.assertIn("book_changed", mod._DM_ENDPOINTS)
        self.assertNotIn("book_download", mod._DM_ENDPOINTS)
        mod._active_campaign = lambda: ""
        self.assertEqual(client.get("/book/info").get_json(), {"available": False})

    def test_the_page_has_the_entries_and_the_words(self):
        display = SKILL / "display"
        src = (display / "templates" / "index.html").read_text(encoding="utf-8")
        for needle in ('id="ln-book"', 'data-ln-proxy="ln-book"', 'id="ph-book-opt"', "('/book', '_blank')",
                       "payload.book &&"):
            self.assertIn(needle, src)
        for path in sorted((display / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("book.title", "book.ready"):
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
