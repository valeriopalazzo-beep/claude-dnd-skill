"""
test_encounter.py — encounters are sized for the party at the table.

scripts/encounter.py reads the party (campaign sheets minus `absent:`, or
levels on the command line), applies the 5e encounter budget and either finds
how many of the creatures the DM named make the difficulty, or proposes
differently shaped groups from the SRD.

Run from repo root:
    python -m pytest tests/test_encounter.py -q
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
sys.path.insert(0, str(SKILL / "scripts"))


def _load():
    spec = importlib.util.spec_from_file_location("encounter_under_test", SKILL / "scripts" / "encounter.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mon(name, cr, kind="humanoid", it=""):
    rec = {"name": name, "index": name.lower().replace(" ", "-"), "cr": cr, "type": kind,
           "hp": 7, "hp_dice": "2d6", "ac": 15, "dex": 14}
    if it:
        rec["_i18n"] = {"it": {"name": it}}
    return rec


GOBLIN = _mon("Goblin", 0.25)
HOBGOBLIN = _mon("Hobgoblin", 0.5)
OGRE = _mon("Ogre", 2, "giant")
SKELETON = _mon("Skeleton", 0.25, "undead", "Scheletro")
GHOUL = _mon("Ghoul", 1, "undead")
WIGHT = _mon("Wight", 3, "undead")
POOL = [GOBLIN, HOBGOBLIN, OGRE, SKELETON, GHOUL, WIGHT, _mon("Bugbear", 1), _mon("Orc", 0.5),
        _mon("Zombie", 0.25, "undead"), _mon("Shadow", 0.5, "undead"), _mon("Hill Giant", 5, "giant")]


class BudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.enc = _load()

    def test_thresholds_are_summed_per_character(self):
        self.assertEqual(self.enc.thresholds([1, 1, 1, 1]), (100, 200, 300, 400))
        self.assertEqual(self.enc.thresholds([3, 1]), (100, 200, 300, 500))

    def test_the_multiplier_follows_the_number_of_monsters_and_the_party_size(self):
        self.assertEqual([self.enc.multiplier(n, 4) for n in (1, 2, 3, 6, 7, 10, 11, 14, 15)],
                         [1, 1.5, 2, 2, 2.5, 2.5, 3, 3, 4])
        self.assertEqual(self.enc.multiplier(1, 2), 1.5)     # a small party: one step up
        self.assertEqual(self.enc.multiplier(1, 6), 0.5)     # a large one: one step down
        self.assertEqual(self.enc.multiplier(20, 2), 5)

    def test_a_group_is_rated_against_the_party(self):
        m = self.enc.measure([(GOBLIN, 4)], [1, 1, 1, 1])
        self.assertEqual((m["raw"], m["multiplier"], m["adjusted"], m["difficulty"]), (200, 2, 400, "deadly"))
        self.assertEqual(self.enc.measure([(GOBLIN, 1)], [1, 1, 1, 1])["difficulty"], "trivial")
        self.assertEqual(self.enc.measure([(GOBLIN, 4)], [5, 5, 5, 5])["difficulty"], "trivial")

    def test_named_creatures_get_the_numbers_that_make_the_difficulty(self):
        for difficulty in self.enc.DIFFICULTIES:
            m = self.enc.fit_counts([HOBGOBLIN, GOBLIN], [2, 2, 2, 2], difficulty)
            self.assertTrue(m["on_target"], difficulty)
            self.assertEqual(m["difficulty"], difficulty)
            self.assertEqual([r["name"] for r, _ in m["group"]], ["Hobgoblin", "Goblin"])
            self.assertTrue(all(n >= 1 for _, n in m["group"]))

    def test_creatures_that_cannot_fit_give_the_closest_and_say_so(self):
        m = self.enc.fit_counts([_mon("Ancient Dragon", 24, "dragon")], [1, 1, 1, 1], "easy")
        self.assertFalse(m["on_target"])
        self.assertEqual(m["count"], 1)

    def test_proposals_are_on_target_one_type_each_and_differently_shaped(self):
        for difficulty in ("easy", "medium", "hard", "deadly"):
            found = self.enc.propose(POOL, [3, 3, 3, 3], difficulty, random.Random(7), 3)
            self.assertTrue(found, difficulty)
            for m in found:
                self.assertEqual(m["difficulty"], difficulty)
                self.assertEqual(len({self.enc._base_type(r) for r, _ in m["group"]}), 1)
                self.assertLessEqual(m["count"], 8)
            self.assertGreater(len({m["shape"] for m in found}), 1, difficulty)

    def test_the_same_seed_proposes_the_same_groups(self):
        names = lambda found: [[(r["name"], n) for r, n in m["group"]] for m in found]
        a = self.enc.propose(POOL, [2, 2, 2], "hard", random.Random(4), 3)
        b = self.enc.propose(POOL, [2, 2, 2], "hard", random.Random(4), 3)
        self.assertEqual(names(a), names(b))

    def test_combatants_are_numbered_and_named_in_the_table_language(self):
        rows = self.enc.combatants([(SKELETON, 2), (GHOUL, 1)], "it")
        self.assertEqual([r["name"] for r in rows], ["Scheletro 1", "Scheletro 2", "Ghoul"])
        self.assertEqual(rows[0], {"name": "Scheletro 1", "dex_mod": 2, "hp": 7, "ac": 15, "type": "npc"})


class CommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.enc = _load()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._old = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_LANG")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        os.environ.pop("DND_LANG", None)
        camp = self.root / "campaigns" / "prova"
        (camp / "characters").mkdir(parents=True)
        (camp / "state.md").write_text(
            "# Prova\n**Ruleset:** 2014\n\n## Session Flags\n- language: it\n- absent: Mira\n", encoding="utf-8")
        for name, level in (("Aldric", 3), ("Mira", 3), ("Lio", 2)):
            (camp / "characters" / f"{name.lower()}.md").write_text(
                f"# {name}\n\n## Identity\n- **Race:** Umano | **Class:** Guerriero | **Level:** {level} |\n",
                encoding="utf-8")

    def tearDown(self):
        for key, old in self._old.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        self._tmp.cleanup()

    def _run(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.enc.main(list(argv))
        return code, out.getvalue() + err.getvalue()

    def test_the_party_is_the_pcs_present(self):
        self.assertEqual(self.enc.campaign_party("prova"), [("Aldric", 3), ("Lio", 2)])

    def test_build_with_named_creatures_from_the_real_srd(self):
        code, out = self._run("build", "--campaign", "prova", "-d", "hard", "--with", "hobgoblin", "--with", "goblin")
        self.assertEqual(code, 0, out)
        self.assertIn("Party: Aldric 3, Lio 2", out)
        self.assertIn("Encounter — HARD", out)
        line = next(l for l in out.splitlines() if "combat.py init:" in l)
        rows = json.loads(line.split("combat.py init:", 1)[1])
        self.assertTrue(all(r["type"] == "npc" and r["hp"] > 0 for r in rows))
        self.assertIn('--monsters "Hobgoblin:1/2:', out)

    def test_build_proposes_groups_of_the_asked_type_in_italian(self):
        code, out = self._run("build", "--campaign", "prova", "-d", "medium", "--type", "non morto", "--seed", "2")
        self.assertEqual(code, 0, out)
        self.assertIn("Option 1 — MEDIUM", out)
        self.assertRegex(out, r"\d × (Scheletro|Zombi|Ombra|Ghoul|Fuoco Fatuo|Ghast|Spettro|Ombra|Wight|Mummia)")

    def test_rate_understands_italian_names_and_counts(self):
        code, out = self._run("rate", "--party", "1,1,1,1", "--monsters", "scheletro:4,zombi", "--lang", "it")
        self.assertEqual(code, 0, out)
        self.assertIn("4 × Scheletro (Skeleton)", out)
        self.assertIn("250 XP × 2 (5 monsters) = 500 adjusted", out)
        self.assertIn("DEADLY", out)

    def test_what_cannot_be_answered_says_why(self):
        for argv in (("build", "--difficulty", "hard"),
                     ("build", "--level", "3", "--players", "4", "--type", "robot"),
                     ("build", "--level", "3", "--players", "4", "--with", "xyzzy-non-esiste"),
                     ("build", "--party", "3,trenta"),
                     ("build", "--level", "25"),
                     ("rate", "--party", "1,1", "--monsters", ",")):
            code, out = self._run(*argv)
            self.assertEqual(code, 1, argv)
            self.assertIn("encounter:", out)


if __name__ == "__main__":
    unittest.main()
