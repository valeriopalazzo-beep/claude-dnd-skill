"""
test_tongues.py — a document written in another language.

handout.py --language Elfico: only the characters whose sheet lists the
language read it; the others get the shapes of its script from the server
(never the words) and who at the table can read it. --reader lets one more
character read it later.

Run from repo root:
    python -m pytest tests/test_tongues.py -q
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
sys.path.insert(0, str(SKILL / "scripts"))

import tongues  # noqa: E402

SHEET = "# {name}\n## Proficiencies\n**Languages:** {langs}\n"


class LanguageTests(unittest.TestCase):
    def test_names_in_both_languages(self):
        for a, b in (("Elfico", "Elvish"), ("elven", "elvish"), ("Nanico", "Dwarvish"), ("Gergo Ladresco", "Thieves' Cant"),
                     ("Gergo dei ladri", "thieves cant"), ("Linguaggio delle Profondità", "Deep Speech"),
                     ("Orchesco", "Orc"), ("Comune", "Common")):
            with self.subTest(a=a):
                self.assertEqual(tongues.canonical(a), tongues.canonical(b))
        self.assertEqual(tongues.canonical("Lingua dei Corvi"), "lingua dei corvi", "an invented language is itself")

    def test_the_sheet_line(self):
        text = "x\n**Languages:** Comune, Halfling, Gergo Ladresco\n"
        self.assertEqual(tongues.sheet_languages(text), {"common", "halfling", "thieves cant"})
        self.assertTrue(tongues.knows(text, "Thieves' Cant"))
        self.assertFalse(tongues.knows(text, "Elfico"))
        self.assertTrue(tongues.knows("- **Lingue:** comune e elfico", "Elvish"))
        self.assertEqual(tongues.sheet_languages("no languages here"), set())

    def test_glyphs_hide_every_word(self):
        text = "Il carico parte alla terza luna. Brucia questa lettera!"
        for lang in ("Elfico", "Nanico", "Infernale", "Draconico", "Celestiale", "Lingua dei Corvi"):
            with self.subTest(lang=lang):
                g = tongues.glyphs(text, lang)
                self.assertEqual(len(g), len(text))
                self.assertIsNone(re.search(r"[A-Za-z]", g), g)
                self.assertEqual(g.count(" "), text.count(" "))
                self.assertTrue(g.endswith("!"))
                self.assertEqual(tongues.glyphs(text, lang), g, "the same letters, the same glyphs")
        self.assertNotEqual(tongues.glyphs("luna", "Elfico"), tongues.glyphs("luna", "Nanico"))


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_tongues", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        m = self.mod
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="dnd-test-tongues-"))
        for name, langs in (("Lyra", "Comune, Elfico"), ("Hernio", "Comune, Halfling"), ("Lio", "Comune, Nanico, Elfico")):
            (self.dir / f"{name.lower()}.md").write_text(SHEET.format(name=name, langs=langs), encoding="utf-8")
        inv = m._inventory
        saved = (inv.paths.characters_dir, inv.active_campaign, m._broadcast, m._handouts_file, m._active_campaign,
                 m._persist_log, m._persist_tail)
        def restore():
            (inv.paths.characters_dir, inv.active_campaign, m._broadcast, m._handouts_file, m._active_campaign,
             m._persist_log, m._persist_tail) = saved
        self.addCleanup(restore)
        inv.paths.characters_dir = lambda: self.dir
        inv.active_campaign = lambda: ""
        self.sent = []
        m._broadcast = self.sent.append
        m._handouts_file = lambda: str(self.dir / "handouts.json")
        m._active_campaign = lambda: ""
        m._persist_log = m._persist_tail = lambda: None
        with m._stats_lock:
            self._stats = dict(m._current_stats)
            m._current_stats.clear()
            m._current_stats.update({"players": [{"name": "Lyra"}, {"name": "Hernio"}, {"name": "Lio"}]})
        self.addCleanup(self._restore_stats)

    def _restore_stats(self):
        with self.mod._stats_lock:
            self.mod._current_stats.clear()
            self.mod._current_stats.update(self._stats)

    def hand(self, **extra):
        body = {"title": "Iscrizione sull'arco", "kind": "scroll", "body": "Chi entra senza il sigillo non esce.",
                "from": "Vessar", **extra}
        r = self.client.post("/handout", json=body)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()

    def test_who_reads_it(self):
        h = self.hand(language="Elfico")
        self.assertEqual((h["language"], h["readers"]), ("Elfico", ["Lyra", "Lio"]))
        self.assertEqual(self.mod._handout_view(h, "lyra")["body"], h["body"])
        seen = self.mod._handout_view(h, "hernio")
        self.assertTrue(seen["unreadable"])
        self.assertNotIn("sigillo", seen["body"])
        self.assertNotIn("Vessar", seen["from"])
        self.assertEqual(self.mod._handout_view(h, ""), h, "the DM and the table screen read everything")
        plain = self.hand()
        self.assertNotIn("language", plain)
        self.assertEqual(self.mod._handout_view(plain, "hernio"), plain)

    def test_the_stream_never_sends_the_words(self):
        h = self.hand(language="Elfico")
        live = self.sent[-1]
        self.assertEqual(live["handout"]["body"], h["body"], "the broadcast is whole; each stream tailors it")
        mine = self.mod._handout_for(live, "hernio", "")
        self.assertTrue(mine["handout"]["unreadable"])
        self.assertNotIn("sigillo", json.dumps(mine, ensure_ascii=False))
        self.assertEqual(self.mod._handout_for(live, None, "lio")["handout"]["body"], h["body"])
        self.assertIs(self.mod._handout_for(live, None, ""), live)
        replay = {"replay_batch": [{"text": "…"}, {"handout": h}]}
        out = self.mod._handout_for(replay, "hernio", "")
        self.assertNotIn("sigillo", json.dumps(out, ensure_ascii=False))
        self.assertEqual(out["replay_batch"][0], {"text": "…"})
        self.assertIn("_handout_for(payload, _viewer, _ch)", (DISPLAY / "dnd-display-app.py").read_text(encoding="utf-8"))

    def test_the_documents_list_and_a_new_reader(self):
        h = self.hand(language="Elfico")
        listed = self.client.get("/handouts?character=Hernio").get_json()
        self.assertTrue(listed[-1]["unreadable"])
        self.assertEqual(self.client.get("/handouts").get_json()[-1]["body"], h["body"])
        r = self.client.post("/handout", json={"id": h["id"], "reader": "Hernio"})
        self.assertEqual(r.get_json()["readers"], ["Lyra", "Lio", "Hernio"])
        self.assertEqual(self.client.get("/handouts?character=Hernio").get_json()[-1]["body"], h["body"])
        plain = self.hand()
        self.assertEqual(self.client.post("/handout", json={"id": plain["id"], "reader": "Hernio"}).status_code, 400)

    def test_a_logged_in_player_cannot_ask_for_someone_else(self):
        self.hand(language="Elfico")
        with self.mod.app.test_request_context("/handouts?character=Lyra"):
            self.mod.g.role, self.mod.g.character = "player", "Hernio"
            resp = self.mod.handouts_list()
        body = resp[0].get_json()
        self.assertTrue(body[-1]["unreadable"])


_SLICE = re.compile(r"(function _handoutTongue\(h\).*?)\n// ── end handout tongue ──", re.S)


class PageTests(unittest.TestCase):
    def test_the_note_under_the_paper(self):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the handout block is no longer where this test slices it")
        it = json.loads((DISPLAY / "i18n" / "it.json").read_text(encoding="utf-8"))
        tr = ("const L = %s;\nfunction _t(k, v, f) { let s = L[k]; if (s === undefined) return f;"
              " for (const n in (v || {})) s = s.split('{' + n + '}').join(v[n]); return s; }\n") % json.dumps(it)
        cases = [{"language": "Elfico", "readers": ["Lyra", "Lio"], "unreadable": True},
                 {"language": "Elfico", "readers": [], "unreadable": True},
                 {"language": "Elfico", "readers": ["Lyra"]}, {}]
        script = tr + m.group(1) + "\nconsole.log(JSON.stringify(%s.map(_handoutTongue)));\n" % json.dumps(cases)
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            out = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        self.assertEqual(out[0]["note"], "È scritto in Elfico: non sai leggerlo. Lo sa leggere: Lyra, Lio.")
        self.assertEqual(out[1]["note"], "È scritto in Elfico: non sai leggerlo. Nessuno del gruppo lo sa leggere.")
        self.assertEqual((out[2]["tag"], out[2]["note"], out[2]["unreadable"]), ("In Elfico", "", False))
        self.assertIsNone(out[3])

    def test_strings_exist_in_every_language(self):
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("handout.lang", "handout.unreadable", "handout.readers", "handout.noReaders"):
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
