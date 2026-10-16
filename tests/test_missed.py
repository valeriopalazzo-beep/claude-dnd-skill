"""
test_missed.py — "While you were away…": a recap for a player who missed a session.

display/missed.py keeps, with the campaign, the recaps the DM writes at the end
of a session for the PCs who were absent; the display opens each one on that
player's own screen only, until they mark it read.

Run from repo root:
    python -m pytest tests/test_missed.py -q
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

import media  # noqa: E402
import missed  # noqa: E402


class RecapTests(unittest.TestCase):
    def test_a_recap_keeps_its_paragraphs_and_refuses_nothing_or_too_much(self):
        rows = []
        r = missed.add(rows, "Lyra", "Mentre eri via,\r\n  Hernio e Lio\tsono scesi.\n\n\n<b>Ti</b> hanno tenuto 12 mo.  ",
                       title="  Mentre eri a   Vallombra… ", session="4", now=100)
        self.assertEqual(r["text"], "Mentre eri via, Hernio e Lio sono scesi.\n\nbTi/b hanno tenuto 12 mo.")
        self.assertEqual((r["title"], r["session"], r["at"], r["read_at"]), ("Mentre eri a Vallombra…", "4", 100, None))
        for text in ("   ", "x" * (missed.MAX_CHARS + 1)):
            with self.assertRaises(missed.MissedError):
                missed.add(rows, "Lyra", text)
        self.assertEqual(len(rows), 1)

    def test_only_its_owner_marks_it_read(self):
        rows = []
        r = missed.add(rows, "Lyra Ombrafina", "Testo.", now=1)
        missed.add(rows, "Hernio", "Altro.", now=2)
        self.assertIsNone(missed.mark_read(rows, "Hernio", r["id"]))
        self.assertIsNone(r["read_at"])
        self.assertIs(missed.mark_read(rows, "lyra  ombrafina", r["id"], now=50), r)
        self.assertEqual(r["read_at"], 50)
        missed.mark_read(rows, "Lyra Ombrafina", r["id"], now=99)
        self.assertEqual(r["read_at"], 50, "read once, the first time counts")
        self.assertEqual([x["text"] for x in missed.for_character(rows, "LYRA OMBRAFINA")], ["Testo."])

    def test_old_read_recaps_make_room_first(self):
        rows = []
        first = missed.add(rows, "Lyra", "1", now=1)
        second = missed.add(rows, "Lyra", "2", now=2)
        missed.mark_read(rows, "Lyra", second["id"])
        for i in range(missed.MAX_KEPT - 1):
            missed.add(rows, "Lyra", f"n{i}", now=10 + i)
        ids = [x["id"] for x in rows]
        self.assertNotIn(second["id"], ids, "the read one goes before an unread older one")
        self.assertIn(first["id"], ids)
        self.assertEqual(len(rows), missed.MAX_KEPT)

    def test_names_mean_one_character(self):
        names = ["Lyra Ombrafina", "Lio", "Vardamir ma Feyn"]
        self.assertEqual(missed.resolve(names, "lyra"), "Lyra Ombrafina")
        self.assertEqual(missed.resolve(names, "Lio"), "Lio", "the exact name beats a part of another")
        for bad in ("", "Hernio"):
            with self.assertRaises(missed.MissedError):
                missed.resolve(names, bad)
        with self.assertRaises(missed.MissedError):
            missed.resolve(["Lio", "Lionel"], "lio n")


class CommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._old = os.environ.get("DND_CAMPAIGN_ROOT")
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        camp = self.root / "campaigns" / "prova"
        (camp / "characters").mkdir(parents=True)
        for stem, title in (("lyra-ombrafina", "Lyra Ombrafina"), ("hernio", "Hernio"), ("lio", "Lio")):
            (camp / "characters" / f"{stem}.md").write_text(f"# {title}\n\n**Level:** 3\n", encoding="utf-8")
        (camp / "state.md").write_text("# Stato\n\n## Session Flags\n- autorun: false\n- absent: lyra ombrafina, Mira\n",
                                       encoding="utf-8")
        self.camp = camp
        self.events = []
        self._saved = (missed._announce, media.active_campaign)
        missed._announce = self.events.append
        media.active_campaign = lambda: "prova"

    def tearDown(self):
        missed._announce, media.active_campaign = self._saved
        if self._old is None:
            os.environ.pop("DND_CAMPAIGN_ROOT", None)
        else:
            os.environ["DND_CAMPAIGN_ROOT"] = self._old
        self._tmp.cleanup()

    def run_m(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = missed.main(["--campaign", "prova", *argv])
        return code, out.getvalue() + err.getvalue()

    def saved(self):
        return missed.load(str(self.camp / "missed.json"))

    def test_absent_reads_state_md_with_the_sheets_spelling(self):
        self.assertEqual(missed.absent("prova"), ["Lyra Ombrafina", "Mira"])
        self.assertEqual(self.run_m("absent")[1].strip(), "Lyra Ombrafina, Mira")
        (self.camp / "state.md").write_text("## Session Flags\n- absent: none\n", encoding="utf-8")
        self.assertIn("nobody is absent", self.run_m("absent")[1])

    def test_a_recap_from_writing_to_removing(self):
        code, out = self.run_m("add", "lyra", "--session", "4", "--text", "Mentre eri via, il gruppo è sceso al pozzo.")
        self.assertEqual(code, 0, out)
        self.assertIn("Lyra Ombrafina — session 4", out)
        self.assertEqual(self.events, ["Lyra Ombrafina"])
        rows = self.saved()
        self.assertEqual((rows[0]["character"], rows[0]["session"]), ("Lyra Ombrafina", "4"))
        self.assertIn("NOT READ YET", self.run_m("list", "--unread")[1])
        self.assertIn("il gruppo è sceso al pozzo", self.run_m("show", "Lyra")[1])
        self.assertIn("no recap for Hernio", self.run_m("show", "hernio")[1])
        code, out = self.run_m("remove", rows[0]["id"])
        self.assertEqual((code, self.saved(), self.events[-1]), (0, [], "Lyra Ombrafina"))

    def test_a_mistake_changes_nothing(self):
        self.run_m("add", "Hernio", "--text", "Qualcosa.")
        before, told = self.saved(), len(self.events)
        for argv in (("add", "Nessuno", "--text", "x"), ("add", "Hernio", "--text", "  "), ("remove", "zzzz"),
                     ("add", "r", "--text", "x")):
            code, out = self.run_m(*argv)
            self.assertEqual(code, 1, argv)
            self.assertIn("Nothing was changed", out)
        self.assertEqual((self.saved(), len(self.events)), (before, told))

    def test_it_runs_as_a_script(self):
        env = dict(os.environ, PYTHONUTF8="1")
        r = subprocess.run([sys.executable, str(DISPLAY / "missed.py"), "--campaign", "prova", "list"],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "no recap yet"), r.stderr[-400:])


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_missed", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.camp = pathlib.Path(tmp.name)
        rows = []
        self.lyra = missed.add(rows, "Lyra Ombrafina", "Per Lyra.", session="4")
        self.hernio = missed.add(rows, "Hernio", "Per Hernio.")
        missed.save(str(self.camp / "missed.json"), rows)
        m = self.mod
        saved = (m._active_campaign, m._find_campaign, m._broadcast)
        self.addCleanup(lambda: setattr(m, "_active_campaign", saved[0]) or setattr(m, "_find_campaign", saved[1])
                        or setattr(m, "_broadcast", saved[2]))
        self.sent = []
        m._active_campaign, m._find_campaign, m._broadcast = (lambda: "prova"), (lambda n: self.camp), self.sent.append

    def as_player(self, character, method, path, body=None):
        m = self.mod
        with m.app.test_request_context(path, method=method, json=body):
            m.g.role, m.g.character = "player", character
            view = m.missed_get if method == "GET" else m.missed_read
            resp = view()
            resp, code = resp if isinstance(resp, tuple) else (resp, resp.status_code)
            return code, resp.get_json()

    def test_a_player_gets_their_own_whatever_they_ask_for(self):
        code, got = self.as_player("Lyra Ombrafina", "GET", "/missed?character=Hernio")
        self.assertEqual((code, [r["text"] for r in got["recaps"]]), (200, ["Per Lyra."]))
        code, got = self.as_player("Lyra Ombrafina", "GET", "/missed?all=1")
        self.assertEqual([r["text"] for r in got["recaps"]], ["Per Lyra."], "?all is the DM's")
        code, _ = self.as_player("Lyra Ombrafina", "POST", "/missed/read", {"id": self.hernio["id"], "character": "Hernio"})
        self.assertEqual(code, 404, "nobody marks another player's recap")
        code, got = self.as_player("Lyra Ombrafina", "POST", "/missed/read", {"id": self.lyra["id"]})
        self.assertEqual(code, 200)
        rows = missed.load(str(self.camp / "missed.json"))
        self.assertEqual([bool(r["read_at"]) for r in rows], [True, False])

    def test_the_dms_browser_names_its_character_and_the_dm_sees_who_read(self):
        got = self.client.get("/missed?character=hernio").get_json()
        self.assertEqual([r["text"] for r in got["recaps"]], ["Per Hernio."])
        self.assertEqual(self.client.get("/missed").get_json(), {"recaps": []}, "the table screen has no character")
        self.assertEqual(len(self.client.get("/missed?all=1").get_json()["recaps"]), 2)
        self.assertEqual(self.client.post("/missed/read", json={"id": self.hernio["id"], "character": "Hernio"}).status_code, 200)
        self.assertEqual(self.client.post("/missed-changed", json={"character": "Hernio"}).status_code, 200)
        self.assertEqual(self.sent[-1], {"missed_changed": {"character": "Hernio"}})
        self.assertIn("missed_changed", self.mod._DM_ENDPOINTS)
        self.assertNotIn("missed_get", self.mod._DM_ENDPOINTS)
        self.assertNotIn("missed_read", self.mod._DM_ENDPOINTS)
        dm = self.client.get("/dm-screen/data").get_json()
        self.assertEqual(dm["missed"], [{"character": "Lyra Ombrafina", "session": "4", "read": False},
                                        {"character": "Hernio", "session": "", "read": True}])


_SLICE = re.compile(r"(function _missedToShow\(recaps, all\).*?)\n// ── end missed helpers ──", re.S)


class PageTests(unittest.TestCase):
    def test_what_the_card_shows_and_how_it_heads_each_session(self):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the missed block is no longer where this test slices it")
        script = "const _t = (k, v, f) => k + (v ? ':' + v.n : '');\n" + m.group(1) + """
const rows = [{id: 'a', text: 'Uno', session: '3', read_at: 5}, {id: 'b', text: 'Due', title: 'Al pozzo'}, {id: 'c', text: ''}];
console.log(JSON.stringify([
  _missedToShow(rows, false).map(r => r.id), _missedToShow(rows, true).map(r => r.id), _missedToShow(null, true),
  _missedHeading(rows[0], true), _missedHeading(rows[1], true), _missedHeading(rows[0], false),
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
        self.assertEqual(out, [["b"], ["a", "b"], [], "missed.session:3", "Al pozzo", ""])

    def test_the_page_has_the_card_and_every_way_in(self):
        src = HTML.read_text(encoding="utf-8")
        for needle in ('id="missed-modal"', 'id="ln-missed"', 'data-ln-proxy="ln-missed"', 'id="ph-missed-opt"',
                       "window._onMissed(payload.missed_changed)", "fetch('/missed/read'",
                       "_ttsPlayBlock(btn, $('missed-text'))"):
            self.assertIn(needle, src)
        self.assertIn('id="s-missed"', (DISPLAY / "templates" / "dm.html").read_text(encoding="utf-8"))

    def test_strings_exist_in_every_language(self):
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("missed.title", "missed.menu", "missed.go", "missed.session"):
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))
            self.assertIn("{n}", data["missed.session"])


if __name__ == "__main__":
    unittest.main()
