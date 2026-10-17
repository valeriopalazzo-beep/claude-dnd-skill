"""
test_rumors.py — what is said in the taverns, true, false or half true.

display/rumors.py keeps the rumors the DM writes (from the campaign's
secrets, quests, factions and threats — `seeds` lists them). A character
listens with a Charisma check on the phone (PHB 2014 p. 179): under the DC
nothing, at it one rumor, 5 over two, 10 over three; the ones nobody has heard
come first, one tied to a place is said only there. Carousing in downtime
brings one without a check. The players see what they heard, never whether
it is true.

Run from repo root:
    python -m pytest tests/test_rumors.py -q
"""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import random
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
import rumors  # noqa: E402


class RuleTests(unittest.TestCase):
    def test_how_many_a_check_brings(self):
        self.assertEqual([rumors.how_many(t, 10) for t in (9, 10, 14, 15, 19, 20, 30)], [0, 1, 1, 2, 2, 3, 3])

    def test_fresh_ones_first_and_only_where_they_are_said(self):
        rs = []
        a = rumors.add(rs, "Dicono che l'Isola compri prigionieri", "true", where="Ferrovalle")
        b = rumors.add(rs, "La Gran Maestra è morta da anni", "falsa")
        c = rumors.add(rs, "Il Rosso è cugino di un barcaiolo", "mezza")
        rumors.hear(b, "Lyra")
        self.assertEqual({r["id"] for r in rumors.pick(rs, "Berta", 2, "Ferrovalle", random.Random(1))}, {a["id"], c["id"]})
        self.assertNotIn(a, rumors.pick(rs, "Berta", 3, "Torre del Meridiano"))
        self.assertEqual(rumors.pick(rs, "Lyra", 3, ""), [c])          # she heard b; a is said only in Ferrovalle
        self.assertEqual(rumors.pick(rs, "Berta", 3, "Locanda di Ferrovalle")[-1], b)   # heard by others: last
        with self.assertRaisesRegex(rumors.RumorError, "already"):
            rumors.add(rs, "dicono che l'isola compri prigionieri", "true")
        with self.assertRaisesRegex(rumors.RumorError, "true, false or half"):
            rumors.add(rs, "Altro", "forse")

    def test_the_players_never_see_the_truth(self):
        rs = []
        r = rumors.add(rs, "La Gran Maestra è morta da anni", "false", about="depistaggio")
        rumors.add(rs, "Nessuno l'ha sentita", "true")
        rumors.hear(r, "Berta", "Mantice Rotto", "29 Nebbiaio")
        view = rumors.public_view(rs)
        self.assertEqual(view, [{"id": r["id"], "text": r["text"], "by": ["Berta"], "where": "Mantice Rotto",
                                 "when": "29 Nebbiaio", "ts": view[0]["ts"]}])
        self.assertNotIn("truth", json.dumps(view))
        self.assertEqual(rumors.dm_view(rs), {"unheard": 1, "heard": [{"text": r["text"], "truth": "false", "by": ["Berta"]}]})


SHEET = "# Berta Tully\n\n## Ability Scores\n| STR | DEX | CON | INT | WIS | CHA |\n|---|---|---|---|---|---|\n| 16 (+3) | 10 (+0) | 16 (+3) | 10 (+0) | 12 (+1) | 14 (+2) |\n"
PREP = """# Prep

## 4. Secrets & Clues
- [ ] Sull'Isola si estraggono anime dai prigionieri vivi.
- [x] Nello è vivo (venuto fuori nella sessione 1).

## 5. Fantastic Locations
- [ ] La sala tonda
"""


class CampaignTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        self.camp = self.root / "campaigns" / "prova"
        (self.camp / "characters").mkdir(parents=True)
        (self.camp / "characters" / "berta-tully.md").write_text(SHEET, encoding="utf-8")
        (self.camp / "prep.md").write_text(PREP, encoding="utf-8")
        (self.camp / "quests.json").write_text(json.dumps({"quests": [{"name": "L'Isola dei Ricambi", "status": "active",
                                                                         "summary": "Cesca vuole entrare"}]}), encoding="utf-8")
        (self.camp / "clocks.json").write_text(json.dumps({"clocks": [{"name": "Nello sull'Isola", "segments": 6, "filled": 2,
                                                                        "secret": True}]}), encoding="utf-8")
        (self.root / "rt").mkdir()
        for var, value in (("DND_CAMPAIGN_ROOT", str(self.root)), ("DND_RUNTIME_DIR", str(self.root / "rt"))):
            old = os.environ.get(var)
            os.environ[var] = value
            self.addCleanup(lambda var=var, old=old: os.environ.pop(var, None) if old is None else os.environ.__setitem__(var, old))
        self.events = []
        saved = (rumors._announce, rumors._story, media.active_campaign, dt._announce, dt._party)
        rumors._announce = lambda event=None: self.events.append(event)
        rumors._story = lambda line: self.events.append({"story": line})
        media.active_campaign = lambda: "prova"
        dt._announce = lambda event=None: None
        dt._party = lambda: ["Berta Tully"]
        self.addCleanup(lambda: (setattr(rumors, "_announce", saved[0]), setattr(rumors, "_story", saved[1]),
                                 setattr(media, "active_campaign", saved[2]), setattr(dt, "_announce", saved[3]),
                                 setattr(dt, "_party", saved[4])))

    def run_r(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = rumors.main(["--campaign", "prova", *argv])
        return code, out.getvalue() + err.getvalue()

    def test_seeds_from_the_campaign(self):
        code, out = self.run_r("seeds")
        self.assertIn("secret (not out yet): Sull'Isola si estraggono anime dai prigionieri vivi.", out)
        self.assertNotIn("Nello è vivo", out)                       # already out
        self.assertNotIn("La sala tonda", out)                      # not a secret
        self.assertIn("quest (active): L'Isola dei Ricambi — Cesca vuole entrare", out)
        self.assertIn("clock [secret]: Nello sull'Isola 2/6", out)

    def test_listening_in_a_tavern(self):
        self.assertEqual(self.run_r("hear", "--who", "Berta", "--total", "15")[0], 1)      # nothing to hear yet
        for text, truth in (("Dicono che l'Isola compri prigionieri", "true"), ("La Gran Maestra è morta", "false"),
                            ("Il Rosso è cugino di un barcaiolo", "half")):
            self.assertEqual(self.run_r("add", text, "--truth", truth)[0], 0)
        code, out = self.run_r("hear", "--who", "berta", "--total", "9")
        self.assertIn("nobody has much to say", out)
        code, out = self.run_r("hear", "--who", "berta", "--total", "15", "--where", "Mantice Rotto")
        self.assertEqual(code, 0, out)
        self.assertIn("Berta Tully: Charisma 15 vs DC 10 — 2 rumor(s)", out)
        self.assertEqual(self.events[-1], {"what": "heard", "who": "Berta Tully", "n": 2})
        self.assertTrue(any("🗣 Berta Tully gathers rumors in Mantice Rotto: 2 to tell" == e.get("story") for e in self.events if e))
        heard = rumors.public_view(rumors.load(rumors.data_path("prova")))
        self.assertEqual((len(heard), heard[0]["by"], heard[0]["where"]), (2, ["Berta Tully"], "Mantice Rotto"))
        code, out = self.run_r("tell", "Berta", "rosso")
        self.assertIn("Berta Tully hears", out)
        self.assertIn("HALF TRUE", self.run_r("list")[1])

    def test_carousing_brings_a_rumor(self):
        self.run_r("add", "Le chiavi dei fondatori erano di metallo delle Chiuse", "--truth", "true")
        data = dt.load(str(self.camp / "downtime.json"))
        dt.open_window(data, 5, ["Berta Tully"])
        dt.choose(data, "Berta Tully", "carouse", "al Mantice Rotto")
        dt.save(str(self.camp / "downtime.json"), data)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(dt.main(["--campaign", "prova", "close"]), 0)
        self.assertIn("The revelry brings a rumor (true): «Le chiavi dei fondatori erano di metallo delle Chiuse» — tell it.", out.getvalue())
        self.assertEqual(rumors.public_view(rumors.load(rumors.data_path("prova")))[0]["by"], ["Berta Tully"])


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_rumors", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def test_every_screen_gets_what_was_heard(self):
        m = self.mod
        sent = []
        saved = (m._broadcast, m._public_rumors)
        self.addCleanup(lambda: (setattr(m, "_broadcast", saved[0]), setattr(m, "_public_rumors", saved[1])))
        m._broadcast = sent.append
        m._public_rumors = lambda: [{"id": "a1", "text": "Dicono…", "by": ["Berta"]}]
        self.assertEqual(self.client.get("/rumors").get_json()[0]["text"], "Dicono…")
        self.client.post("/rumors", json={"event": {"what": "heard", "who": "Berta", "n": 2}})
        self.assertEqual(sent[-1]["rumor_event"], {"who": "Berta", "n": 2})
        self.assertIn("rumors_changed", m._DM_ENDPOINTS)
        src = (DISPLAY / "dnd-display-app.py").read_text(encoding="utf-8")
        self.assertIn('"rumors": rumors,', src)


class PageTests(unittest.TestCase):
    def test_the_panel_and_the_strings(self):
        html = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")
        for needle in ('id="rumor-modal"', 'id="ln-rumor"', 'data-ln-proxy="ln-rumor"', 'id="ph-rumor-opt"', 'id="ln-i-rumor"',
                       "window._onRumors(payload.rumors, payload.rumor_event)", "fetch('/rumors')"):
            self.assertIn(needle, html)
        self.assertIn('id="s-rumors"', (DISPLAY / "templates" / "dm.html").read_text(encoding="utf-8"))
        keys = ["rum.title", "rum.empty", "rum.heardBy", "rum.toast", "dms.rumors", "dms.rumors.unheard",
                "dms.rumors.true", "dms.rumors.false", "dms.rumors.half"]
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
