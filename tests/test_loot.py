"""
test_loot.py — random treasure lands on the sheets through inventory.py.

scripts/loot.py rolls a pile for a challenge rating (or takes one the DM
dictates), keeps it in <campaign>/loot.json and hands it out: coins in equal
shares, numbered items to whoever takes them. A wrong name changes nothing.

Run from repo root:
    python -m pytest tests/test_loot.py -q
"""
import contextlib
import importlib.util
import io
import os
import pathlib
import random
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
sys.path.insert(0, str(SKILL / "scripts"))

SHEET = """# {name}

## Equipment & Inventory
**Weapons:**
- Stocco

**Armour:**
- —

**Adventuring Gear:**
- Corda

**Currency:** 1gp 0sp 0cp

## Notes
"""

POOL = (
    [
        {"name": "Potion of Climbing", "index": "potion-of-climbing", "rarity": "Common", "category": "Potion",
         "_i18n": {"it": {"name": "Pozione della Scalata"}}},
        {"name": "Spell Scroll (1st)", "index": "spell-scroll-1st", "rarity": "Common", "category": "Scroll",
         "_i18n": {"it": {"name": "Pergamena Magica (1° Livello)"}}},
        {"name": "Bag of Holding", "index": "bag-of-holding", "rarity": "Uncommon", "category": "Wondrous Items",
         "_i18n": {"it": {"name": "Borsa Conservante"}}},
        {"name": "Flame Tongue", "index": "flame-tongue", "rarity": "Rare", "category": "Weapon"},
    ],
    [
        {"name": "Magic Missile", "index": "magic-missile", "level": 1,
         "_i18n": {"it": {"name": "Dardo Incantato"}}},
        {"name": "Fireball", "index": "fireball", "level": 3},
    ],
)


def _load():
    spec = importlib.util.spec_from_file_location("loot_under_test", SKILL / "scripts" / "loot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RollTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loot = _load()

    def test_challenge_ratings_fall_in_four_tiers(self):
        tiers = [self.loot.tier_for(self.loot.parse_cr(cr)) for cr in ("0", "1/4", "4", "5", "10", "11", "16", "17", "30")]
        self.assertEqual(tiers, [0, 0, 0, 1, 1, 2, 2, 3, 3])
        for bad in ("", "tre", "1/0", "-2"):
            with self.assertRaises(self.loot.LootError):
                self.loot.parse_cr(bad)

    def test_the_same_seed_gives_the_same_pile(self):
        a = self.loot.roll_treasure(3, hoard=True, lang="it", rng=random.Random(11), pool=POOL)
        b = self.loot.roll_treasure(3, hoard=True, lang="it", rng=random.Random(11), pool=POOL)
        self.assertEqual(a, b)

    def test_what_creatures_carry_is_coins_only_and_grows_with_their_number(self):
        one = [self.loot.roll_treasure(2, monsters=1, rng=random.Random(s)) for s in range(40)]
        ten = [self.loot.roll_treasure(2, monsters=10, rng=random.Random(s)) for s in range(40)]
        self.assertTrue(all(not p["items"] and p["coins"] for p in one + ten))
        worth = lambda piles: sum(self.loot.inventory.coins_cp(p["coins"]) for p in piles)
        self.assertGreater(worth(ten), 5 * worth(one))

    def test_a_hoard_is_worth_more_at_every_tier(self):
        def average(cr):
            piles = [self.loot.roll_treasure(cr, hoard=True, rng=random.Random(s), pool=POOL) for s in range(60)]
            return sum(self.loot.inventory.coins_cp(p["coins"]) for p in piles) / len(piles)
        averages = [average(cr) for cr in (2, 7, 13, 20)]
        self.assertEqual(averages, sorted(averages))
        self.assertGreater(averages[1], 5 * averages[0])

    def test_items_are_named_in_the_table_language_and_stack(self):
        seen_it, seen_en = set(), set()
        for s in range(200):
            for lang, seen in (("it", seen_it), ("en", seen_en)):
                pile = self.loot.roll_treasure(3, hoard=True, lang=lang, rng=random.Random(s), pool=POOL)
                names = [i["name"] for i in pile["items"]]
                self.assertEqual(len(names), len(set(names)), "equal items are one line with a count")
                seen.update(names)
        self.assertIn("Pozione della Scalata", seen_it)
        self.assertIn("Potion of Climbing", seen_en)
        self.assertTrue(any(n.endswith("(50 mo)") for n in seen_it))
        self.assertTrue(any(n.endswith("(50 gp)") for n in seen_en))
        # a spell scroll says which spell, of the scroll's level
        self.assertIn("Pergamena Magica (1° Livello): Dardo Incantato", seen_it)
        self.assertFalse(any("Fireball" in n for n in seen_en))
        # an item with no translation keeps its English name
        self.assertTrue(all("Flame Tongue" == n for n in seen_it if "Flame" in n))


class TakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loot = _load()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._old = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_LANG")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        os.environ.pop("DND_LANG", None)
        self.camp = self.root / "campaigns" / "prova"
        (self.camp / "characters").mkdir(parents=True)
        (self.camp / "state.md").write_text("# Prova\n\n## Session Flags\n- language: it\n", encoding="utf-8")
        for name in ("Aldric", "Mira", "Lio"):
            (self.camp / "characters" / f"{name.lower()}.md").write_text(SHEET.format(name=name), encoding="utf-8")
        self.pushed = []
        self._push = self.loot.inventory._push
        self.loot.inventory._push = self.pushed.append

    def tearDown(self):
        self.loot.inventory._push = self._push
        for key, old in self._old.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        self._tmp.cleanup()

    def _run(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.loot.main(["--campaign", "prova", *argv])
        return code, out.getvalue() + err.getvalue()

    def _sheet(self, name):
        return (self.camp / "characters" / f"{name.lower()}.md").read_text(encoding="utf-8")

    def _purse(self, name):
        return self.loot.inventory.read_purse(self._sheet(name))

    def test_set_then_take_splits_the_coins_and_gives_the_items(self):
        code, out = self._run("set", "--coins", "100gp", "1cp", "--item", "Chiave d'argento", "--item", "Granato (100 mo) x3")
        self.assertEqual(code, 0, out)
        self.assertIn("2. Granato (100 mo) ×3", out)
        self.assertTrue((self.camp / "loot.json").exists())

        code, out = self._run("take", "--among", "Aldric,Mira,Lio", "--item", "1:Mira", "--item", "2:Lio:2")
        self.assertEqual(code, 0, out)
        # 10001 cp in three: 3333 each, 2 cp left over go to the first name
        self.assertEqual(self.loot.inventory.coins_cp(self._purse("Aldric")), 100 + 3333 + 2)
        self.assertEqual(self.loot.inventory.coins_cp(self._purse("Mira")), 100 + 3333)
        self.assertIn("- Chiave d'argento", self._sheet("Mira"))
        self.assertIn("- Granato (100 mo) ×2", self._sheet("Lio"))
        # one garnet nobody took is still there
        self.assertIn("Granato (100 mo)", self._run("show")[1])
        code, out = self._run("take", "--items-to", "Aldric")
        self.assertEqual(code, 0, out)
        self.assertIn("- Granato (100 mo)", self._sheet("Aldric"))
        self.assertFalse((self.camp / "loot.json").exists())

    def test_everything_to_one_character(self):
        self._run("set", "--coins", "12gp", "--item", "Pozione di guarigione x2")
        code, out = self._run("take", "--to", "Lio")
        self.assertEqual(code, 0, out)
        self.assertEqual(self._purse("Lio")["gp"], 13)
        self.assertIn("- Pozione di guarigione ×2", self._sheet("Lio"))
        self.assertFalse((self.camp / "loot.json").exists())

    def test_a_wrong_name_or_number_changes_nothing(self):
        self._run("set", "--coins", "30gp", "--item", "Chiave")
        before = {n: self._sheet(n) for n in ("Aldric", "Mira", "Lio")}
        for argv in (("take", "--among", "Aldric,Nessuno"),
                     ("take", "--among", "Aldric,Mira", "--item", "1:Nessuno"),
                     ("take", "--item", "2:Mira"),
                     ("take", "--item", "1:Mira:5"),
                     ("take", "--among", "Aldric,aldric"),
                     ("take", "--to", "Lio", "--among", "Aldric,Mira"),
                     ("take",)):
            code, out = self._run(*argv)
            self.assertEqual(code, 1, argv)
            self.assertIn("Nothing was changed", out)
        self.assertEqual(before, {n: self._sheet(n) for n in ("Aldric", "Mira", "Lio")})
        self.assertIn("Chiave", self._run("show")[1])

    def test_a_roll_does_not_bury_a_treasure_still_waiting(self):
        self.assertEqual(self._run("roll", "--cr", "2", "--monsters", "3", "--seed", "1")[0], 0)
        code, out = self._run("roll", "--cr", "2", "--seed", "2")
        self.assertEqual(code, 1)
        self.assertIn("still waiting", out)
        self.assertEqual(self._run("roll", "--cr", "2", "--seed", "2", "--add")[0], 0)
        self.assertEqual(self._run("drop")[0], 0)
        self.assertFalse((self.camp / "loot.json").exists())

    def test_a_roll_can_hand_the_treasure_out_at_once_in_the_campaign_language(self):
        code, out = self._run("roll", "--cr", "1/2", "--monsters", "2", "--seed", "5", "--among", "Aldric,Mira")
        self.assertEqual(code, 0, out)
        self.assertFalse((self.camp / "loot.json").exists())
        self.assertGreater(self.loot.inventory.coins_cp(self._purse("Mira")), 100)
        self.assertEqual(self.loot.paths.campaign_language("prova"), "it")
        os.environ["DND_LANG"] = "en"
        self.assertEqual(self.loot.paths.campaign_language("prova"), "en")

    def test_roll_needs_a_rating_and_a_campaign(self):
        self.assertEqual(self._run("roll")[0], 1)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.loot.main(["--campaign", "non-esiste", "show"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
