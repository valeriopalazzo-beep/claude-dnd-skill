"""
test_bestiary.py — what the party has learned about the creatures it met.

display/bestiary.py is a notebook that starts empty: a creature gets a page
when the party meets it, and the page grows only with what the DM records as
the characters find it out. The real numbers stay with the SRD until revealed.

Run from repo root:
    python -m pytest tests/test_bestiary.py -q
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

import bestiary as bs  # noqa: E402

GHOUL = {"name": "Ghoul", "index": "ghoul", "cr": 1, "ac": 12, "hp": 22, "speed": "walk 30 ft.",
         "type": "undead", "size": "Medium"}
SKELETON = {"name": "Skeleton", "index": "skeleton", "cr": 0.25, "ac": 13, "hp": 13, "speed": "walk 30 ft.",
            "type": "undead", "size": "Medium", "_i18n": {"it": {"name": "Scheletro"}}}


class NotebookTests(unittest.TestCase):
    def test_a_first_sight_opens_an_empty_page_in_the_table_language(self):
        pages = []
        entry, fresh = bs.meet(pages, "skeleton", record=SKELETON, lang="it", count=4)
        self.assertTrue(fresh)
        self.assertEqual((entry["name"], entry["srd_name"], entry["met"], entry["defeated"]), ("Scheletro", "Skeleton", 4, 0))
        self.assertEqual((entry["known"], entry["facts"], entry["resist"], entry["immune"], entry["weak"]), ({}, [], [], [], []))

    def test_meeting_it_again_counts_and_finds_the_page_by_either_name(self):
        pages = []
        bs.meet(pages, "skeleton", record=SKELETON, lang="it")
        for query in ("scheletro", "Skeleton", "schel"):
            entry, fresh = bs.meet(pages, query, record=None)
            self.assertFalse(fresh)
        self.assertEqual((len(pages), pages[0]["met"]), (1, 4))
        entry, fresh = bs.meet(pages, "ossa che camminano", record=SKELETON)   # the same SRD creature, another word
        self.assertFalse(fresh)

    def test_the_party_can_give_it_a_name_or_meet_something_of_the_dms_own(self):
        pages = []
        entry, _ = bs.meet(pages, "ghoul", alias="Divoratore di tombe", record=GHOUL)
        self.assertEqual((entry["name"], entry["srd_name"]), ("Divoratore di tombe", "Ghoul"))
        with self.assertRaises(bs.BestiaryError):
            bs.meet(pages, "La Custode", record=None)                          # not in the SRD: say so
        entry, fresh = bs.meet(pages, "La Custode", custom=True, kind="non morto")
        self.assertEqual((fresh, entry["srd_name"], entry["known"]), (True, "", {"type": "non morto"}))
        with self.assertRaises(bs.BestiaryError):
            bs.meet(pages, "ghoul", record=GHOUL, count=0)

    def test_learning_adds_only_what_is_new(self):
        pages = []
        entry, _ = bs.meet(pages, "ghoul", record=GHOUL)
        new = bs.learn(entry, "Il suo tocco paralizza", immune=["veleno"], weak=["radiante"])
        self.assertEqual(new, ["Il suo tocco paralizza", "immune: veleno", "weak: radiante"])
        self.assertEqual(bs.learn(entry, "il suo  tocco PARALIZZA", immune=["Veleno"]), [])
        self.assertEqual((entry["facts"], entry["immune"], entry["weak"], entry["resist"]),
                         (["Il suo tocco paralizza"], ["veleno"], ["radiante"], []))

    def test_real_numbers_appear_only_when_revealed(self):
        pages = []
        entry, _ = bs.meet(pages, "ghoul", record=GHOUL)
        self.assertEqual(bs.public_view(pages)[0]["known"], {})
        new = bs.learn(entry, stats=["ac", "hp"], record=GHOUL)
        self.assertEqual((new, entry["known"]), (["ac: 12", "hp: 22"], {"ac": 12, "hp": 22}))
        bs.learn(entry, stats=["all"], record=GHOUL)
        self.assertEqual(entry["known"], {"ac": 12, "hp": 22, "speed": "walk 30 ft.", "cr": "1", "type": "undead, Medium"})
        self.assertEqual(bs.srd_stats(SKELETON)["cr"], "1/4")
        with self.assertRaises(bs.BestiaryError):
            bs.learn(entry, stats=["weight"], record=GHOUL)
        custom, _ = bs.meet(pages, "La Custode", custom=True)
        with self.assertRaises(bs.BestiaryError):
            bs.learn(custom, stats=["ac"], record=None)

    def test_a_creature_outside_the_srd_gets_its_numbers_by_hand(self):
        pages = []
        entry, _ = bs.meet(pages, "La Custode", custom=True)
        new = bs.learn(entry, values=["ac=15", "HP = 52"])
        self.assertEqual((new, entry["known"]), (["ac: 15", "hp: 52"], {"ac": "15", "hp": "52"}))
        self.assertEqual(bs.learn(entry, values=["ac=15"]), [])
        for bad in ("weight=3", "ac", "ac="):
            with self.assertRaises(bs.BestiaryError):
                bs.learn(entry, values=[bad])

    def test_a_custom_page_takes_the_srd_block_it_is_played_with(self):
        pages = []
        entry, _ = bs.meet(pages, "Divoratore", custom=True, kind="non morto")
        entry["facts"].append("Puzza di cripta")
        again, fresh = bs.meet(pages, "ghoul", alias="Divoratore", record=GHOUL, count=2)
        self.assertIs(again, entry)
        self.assertEqual((fresh, entry["srd_name"], entry["met"], entry["facts"]),
                         (False, "Ghoul", 3, ["Puzza di cripta"]))
        bs.learn(entry, stats=["ac"], record=GHOUL)
        self.assertEqual(entry["known"]["ac"], 12)

    def test_defeats_are_counted(self):
        pages = []
        entry, _ = bs.meet(pages, "ghoul", record=GHOUL, count=2)
        bs.defeat(entry, 2)
        bs.defeat(entry)
        self.assertEqual((entry["met"], entry["defeated"]), (2, 3))
        with self.assertRaises(bs.BestiaryError):
            bs.defeat(entry, 0)

    def test_the_players_get_the_pages_in_order_without_the_srd_key(self):
        pages = []
        bs.meet(pages, "skeleton", record=SKELETON, lang="it")
        bs.meet(pages, "ghoul", record=GHOUL)
        view = bs.public_view(pages)
        self.assertEqual([c["name"] for c in view], ["Ghoul", "Scheletro"])
        self.assertEqual(set(view[0]), {"id", "name", "met", "defeated", "known", "resist", "immune", "weak", "facts"})

    def test_a_short_name_must_mean_one_page(self):
        pages = []
        bs.meet(pages, "ghoul", record=GHOUL)
        bs.meet(pages, "Ghoul delle cripte", custom=True)
        self.assertEqual(bs.find(pages, "cripte")["name"], "Ghoul delle cripte")
        self.assertEqual(bs.find(pages, "ghoul")["name"], "Ghoul")              # the exact name wins
        for bad in ("gho", "drago", ""):
            with self.assertRaises(bs.BestiaryError):
                bs.find(pages, bad)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._old = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_LANG")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        os.environ.pop("DND_LANG", None)
        camp = self.root / "campaigns" / "prova"
        camp.mkdir(parents=True)
        (camp / "state.md").write_text("# Prova\n**Ruleset:** 2014\n\n## Session Flags\n- language: it\n", encoding="utf-8")
        self.events = []
        self._announce = bs._announce
        bs._announce = self.events.append

    def tearDown(self):
        bs._announce = self._announce
        for key, old in self._old.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        self._tmp.cleanup()

    def run_bs(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = bs.main(["--campaign", "prova", *argv])
        return code, out.getvalue() + err.getvalue()

    def saved(self):
        return bs.load(str(self.root / "campaigns" / "prova" / "bestiary.json"))

    def test_the_real_srd_supplies_italian_names_and_numbers(self):
        code, out = self.run_bs("meet", "scheletro", "--count", "3")
        self.assertEqual(code, 0, out)
        self.assertIn("+ Scheletro  (SRD: Skeleton)", out)
        self.assertEqual(self.events[-1], {"name": "Scheletro", "new": True})
        code, out = self.run_bs("learn", "scheletro", "Le frecce gli passano tra le costole", "--weak", "contundente",
                                "--stat", "ac", "--stat", "type")
        self.assertEqual(code, 0, out)
        page = self.saved()[0]
        self.assertEqual(page["known"]["ac"], 13)
        self.assertIn("non morto", page["known"]["type"])
        self.assertEqual(self.events[-1], {"name": "Scheletro", "new": False})
        self.run_bs("learn", "scheletro", "--weak", "contundente")
        self.assertIsNone(self.events[-1])                                     # nothing new, nothing announced
        self.run_bs("meet", "scheletro")
        self.run_bs("defeat", "scheletro", "2")
        self.assertEqual((self.saved()[0]["met"], self.saved()[0]["defeated"]), (4, 2))

    def test_rename_forget_list_and_show(self):
        self.run_bs("meet", "ghoul")
        self.run_bs("meet", "La Custode", "--custom", "--type", "non morto")
        self.assertEqual(self.run_bs("rename", "ghoul", "Divoratore")[0], 0)
        self.assertEqual(self.run_bs("rename", "custode", "divoratore")[0], 1)
        code, out = self.run_bs("list")
        self.assertIn("Divoratore — met 1", out)
        self.assertIn("(custom)", self.run_bs("show", "custode")[1])
        self.assertEqual(self.run_bs("forget", "divoratore")[0], 0)
        self.assertEqual([c["name"] for c in self.saved()], ["La Custode"])

    def test_a_mistake_changes_nothing(self):
        self.run_bs("meet", "ghoul")
        before, announced = self.saved(), len(self.events)
        for argv in (("meet", "drago che non esiste"), ("learn", "ghoul"), ("learn", "nessuno", "x"),
                     ("learn", "ghoul", "--stat", "peso"), ("defeat", "ghoul", "0"), ("forget", "nessuno")):
            code, out = self.run_bs(*argv)
            self.assertEqual(code, 1, argv)
            self.assertIn("Nothing was changed", out)
        self.assertEqual(self.saved(), before)
        self.assertEqual(len(self.events), announced)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_bestiary", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def test_every_screen_reads_it_and_only_the_dm_writes(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        camp = pathlib.Path(tmp.name)
        pages = []
        entry, _ = bs.meet(pages, "ghoul", record=GHOUL)
        bs.learn(entry, "Paralizza")
        bs.save(str(camp / "bestiary.json"), pages)
        saved = (self.mod._active_campaign, self.mod._find_campaign, self.mod._broadcast)
        self.addCleanup(lambda: setattr(self.mod, "_active_campaign", saved[0]) or setattr(self.mod, "_find_campaign", saved[1])
                        or setattr(self.mod, "_broadcast", saved[2]))
        sent = []
        self.mod._active_campaign, self.mod._find_campaign, self.mod._broadcast = (lambda: "prova"), (lambda n: camp), sent.append
        got = self.client.get("/bestiary").get_json()
        self.assertEqual((got[0]["name"], got[0]["facts"]), ("Ghoul", ["Paralizza"]))
        self.assertNotIn("index", got[0])
        self.assertEqual(self.client.post("/bestiary", json={"event": {"name": "Ghoul", "new": True, "x": 1}}).status_code, 204)
        self.assertEqual(sent[-1]["bestiary_event"], {"name": "Ghoul", "new": True})
        self.assertEqual(sent[-1]["bestiary"][0]["name"], "Ghoul")
        self.assertIn("bestiary_changed", self.mod._DM_ENDPOINTS)
        self.assertNotIn("bestiary_get", self.mod._DM_ENDPOINTS)
        self.mod._active_campaign = lambda: ""
        self.assertEqual(self.client.get("/bestiary").get_json(), [])


_SLICE = re.compile(r"(function _beastRows\(c\).*?)\n// ── end bestiary rows ──", re.S)


class PageTests(unittest.TestCase):
    def test_unknown_numbers_are_listed_as_unknown(self):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the bestiary block is no longer where this test slices it")
        page = {"name": "Ghoul", "known": {"ac": 12, "type": "undead"}, "weak": ["radiante"], "resist": [],
                "immune": ["veleno"], "facts": ["Paralizza"]}
        script = m.group(1) + "\nconsole.log(JSON.stringify([_beastRows(%s), _beastRows({name: 'x'})]));\n" % json.dumps(page)
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            rows, blank = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        self.assertEqual(rows["stats"], [{"key": "type", "value": "undead"}, {"key": "ac", "value": "12"},
                                         {"key": "hp", "value": None}, {"key": "speed", "value": None},
                                         {"key": "cr", "value": None}])
        self.assertEqual(rows["defences"], [{"key": "weak", "values": ["radiante"]}, {"key": "immune", "values": ["veleno"]}])
        self.assertEqual((rows["facts"], rows["count"]), (["Paralizza"], 5))
        self.assertEqual((blank["count"], blank["defences"], blank["facts"]), (0, [], []))

    def test_the_page_has_the_panel_and_every_way_in(self):
        src = HTML.read_text(encoding="utf-8")
        for needle in ('id="beast-modal"', 'id="ln-beast"', 'data-ln-proxy="ln-beast"', 'id="ph-beast-opt"',
                       "(payload.bestiary || payload.bestiary_event)"):
            self.assertIn(needle, src)

    def test_strings_exist_in_every_language(self):
        keys = ["beast.title", "beast.new", "beast.empty", "beast.nothing", "beast.met", "beast.met1", "beast.defeated",
                "beast.known", "beast.known1", "beast.weak", "beast.resist", "beast.immune", "beast.toastNew", "beast.toastMore"]
        keys += ["beast.stat." + k for k in bs.STATS]
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
