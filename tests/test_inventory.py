"""
test_inventory.py — coins and items are applied to the sheet, not remembered.

scripts/inventory.py edits the `## Equipment & Inventory` section of a
character's .md: it makes change, refuses what the purse cannot cover, and
leaves the file untouched when a request does not match.

Run from repo root:
    python -m pytest tests/test_inventory.py -q
"""
import importlib.util
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
sys.path.insert(0, str(SKILL / "scripts"))

SHEET = """# {name}
**Player:** Test

## Equipment & Inventory
**Weapons:**
- Stocco
- 2 pugnali

**Armour:**
- Armatura di cuoio

**Adventuring Gear:**
- Arnesi da scasso
- Piede di porco
- Pozione di guarigione ×2

**Currency:** 15gp 0sp 6cp

## Backstory & Notes
- —
"""

EMPTY = """# Nuovo
## Equipment & Inventory
**Weapons:**
-

**Armour:**
- —

**Adventuring Gear:**
-

**Currency:** 0gp 0sp 0cp

## Backstory & Notes
"""


def _load():
    spec = importlib.util.spec_from_file_location("inventory_under_test", SKILL / "scripts" / "inventory.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CoinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inv = _load()

    def test_coins_are_read_in_english_and_italian(self):
        self.assertEqual(self.inv.parse_coins("12gp 5sp"), {"gp": 12, "sp": 5})
        self.assertEqual(self.inv.parse_coins("12 mo, 5 ma 3mr"), {"gp": 12, "sp": 5, "cp": 3})
        self.assertEqual(self.inv.coins_cp({"pp": 1, "gp": 2, "ep": 1, "sp": 3, "cp": 4}), 1284)

    def test_a_bare_number_or_a_word_is_not_an_amount(self):
        for bad in ("12", "tanto oro", "5gp circa", ""):
            with self.subTest(bad=bad), self.assertRaises(self.inv.InventoryError):
                self.inv.parse_coins(bad)

    def test_paying_makes_change(self):
        purse = {"gp": 15, "sp": 0, "cp": 6}
        self.assertEqual(self.inv.pay_from(purse, 5), {"pp": 0, "gp": 15, "ep": 0, "sp": 0, "cp": 1})
        self.assertEqual(self.inv.pay_from(purse, 50), {"pp": 0, "gp": 14, "ep": 0, "sp": 5, "cp": 6})
        self.assertEqual(self.inv.pay_from(purse, 1506), {"pp": 0, "gp": 0, "ep": 0, "sp": 0, "cp": 0})

    def test_platinum_is_kept_whole_until_it_is_needed(self):
        purse = {"pp": 2, "gp": 5}
        self.assertEqual(self.inv.pay_from(purse, 300)["pp"], 2)
        after = self.inv.pay_from(purse, 800)        # 25gp − 8gp = 17gp: one platinum must break
        self.assertEqual((after["pp"], after["gp"]), (1, 7))

    def test_a_purse_that_is_too_light_refuses(self):
        with self.assertRaises(self.inv.InventoryError) as cm:
            self.inv.pay_from({"gp": 1}, 101)
        self.assertIn("not enough", str(cm.exception))

    def test_prices_print_as_gold_silver_copper(self):
        self.assertEqual(self.inv.format_cp(1250), "12gp 5sp")
        self.assertEqual(self.inv.format_cp(0), "0gp")


class SheetTextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inv = _load()
        cls.text = SHEET.format(name="Aldric")

    def test_purse_and_items_are_read_from_the_section(self):
        self.assertEqual(self.inv.read_purse(self.text)["gp"], 15)
        self.assertEqual(self.inv.read_purse(self.text)["cp"], 6)
        self.assertEqual(self.inv.list_items(self.text), {
            "weapons": ["Stocco", "2 pugnali"],
            "armour": ["Armatura di cuoio"],
            "gear": ["Arnesi da scasso", "Piede di porco", "Pozione di guarigione ×2"],
        })

    def test_writing_the_purse_touches_only_the_currency_line(self):
        out = self.inv.write_purse(self.text, {"gp": 3, "sp": 2, "cp": 0})
        self.assertIn("**Currency:** 3gp 2sp 0cp\n", out)
        self.assertEqual(out.replace("3gp 2sp 0cp", "15gp 0sp 6cp"), self.text)

    def test_platinum_appears_only_when_held_and_a_note_survives(self):
        text = self.text.replace("15gp 0sp 6cp", "15gp 0sp 6cp (più una gemma)")
        out = self.inv.write_purse(text, {"pp": 1, "gp": 0, "sp": 0, "cp": 0})
        self.assertIn("**Currency:** 1pp 0gp 0sp 0cp (più una gemma)\n", out)

    def test_a_new_item_goes_at_the_end_of_its_list(self):
        out = self.inv.add_item(self.text, "Corda di canapa")
        self.assertIn("- Pozione di guarigione ×2\n- Corda di canapa\n\n**Currency:**", out)
        out = self.inv.add_item(self.text, "Spada corta", section="weapons")
        self.assertIn("- 2 pugnali\n- Spada corta\n\n**Armour:**", out)

    def test_one_more_of_the_same_raises_the_count(self):
        out = self.inv.add_item(self.text, "pozione di guarigione")
        self.assertIn("- Pozione di guarigione ×3\n", out)
        out = self.inv.add_item(self.text, "Piede di porco", qty=2)
        self.assertIn("- Piede di porco ×3\n", out)

    def test_the_first_item_replaces_the_placeholder(self):
        out = self.inv.add_item(EMPTY, "Torcia", qty=5)
        self.assertIn("**Adventuring Gear:**\n- Torcia ×5\n\n**Currency:**", out)
        out = self.inv.add_item(EMPTY, "Scudo", section="armor")
        self.assertIn("**Armour:**\n- Scudo\n\n**Adventuring Gear:**", out)

    def test_removing_one_of_several_lowers_the_count_and_the_last_drops_the_line(self):
        out, full = self.inv.remove_item(self.text, "pozione")
        self.assertEqual(full, "Pozione di guarigione")
        self.assertIn("- Pozione di guarigione\n\n**Currency:**", out)
        out, _ = self.inv.remove_item(self.text, "Pozione di guarigione", qty=2)
        self.assertNotIn("Pozione", out)
        self.assertIn("- Piede di porco\n\n**Currency:**", out)

    def test_an_ambiguous_or_missing_item_changes_nothing(self):
        text = self.inv.add_item(self.text, "Pozione di forza")
        with self.assertRaises(self.inv.InventoryError) as cm:
            self.inv.remove_item(text, "pozione")
        self.assertIn("Pozione di forza", str(cm.exception))
        with self.assertRaises(self.inv.InventoryError):
            self.inv.remove_item(self.text, "Bacchetta magica")
        with self.assertRaises(self.inv.InventoryError):
            self.inv.remove_item(self.text, "Pozione di guarigione", qty=3)

    def test_windows_line_endings_are_kept(self):
        crlf = self.text.replace("\n", "\r\n")
        out = self.inv.add_item(self.inv.write_purse(crlf, {"gp": 1, "sp": 0, "cp": 0}), "Corda")
        out, _ = self.inv.remove_item(out, "Stocco")
        self.assertNotIn("\n", out.replace("\r\n", ""))
        self.assertIn("**Currency:** 1gp 0sp 0cp\r\n", out)
        self.assertIn("- Corda\r\n", out)

    def test_a_sheet_without_the_section_is_refused(self):
        with self.assertRaises(self.inv.InventoryError):
            self.inv.read_purse("# Nessuno\n\n## Backstory\n")


class OperationTests(unittest.TestCase):
    """Whole operations on files: everything is applied, or nothing is."""

    def setUp(self):
        self.inv = _load()
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="dnd-test-inventory-"))
        for name in ("Aldric", "Mira", "Lio"):
            (self.dir / f"{name.lower()}.md").write_text(SHEET.format(name=name), encoding="utf-8")
        self.inv.paths.characters_dir = lambda: self.dir
        self.inv.active_campaign = lambda: ""
        self.pushed = []
        self.inv._push = self.pushed.append

    def _text(self, name):
        return (self.dir / f"{name.lower()}.md").read_text(encoding="utf-8")

    def test_buying_pays_and_adds_in_one_go(self):
        purse = self.inv.op_buy("aldric", "Lanterna schermabile", 500)
        self.assertEqual((purse["gp"], purse["cp"]), (10, 6))
        text = self._text("Aldric")
        self.assertIn("**Currency:** 10gp 0sp 6cp", text)
        self.assertIn("- Lanterna schermabile\n", text)
        self.assertEqual(self.pushed, [["--player", "Aldric", "--inventory-add", "Lanterna schermabile"]])

    def test_a_purchase_too_dear_leaves_the_sheet_alone(self):
        before = self._text("Aldric")
        with self.assertRaises(self.inv.InventoryError):
            self.inv.op_buy("Aldric", "Armatura a piastre", 150000)
        self.assertEqual(self._text("Aldric"), before)
        self.assertEqual(self.pushed, [])

    def test_buying_several_charges_for_each(self):
        purse = self.inv.op_buy("Aldric", "Torcia", 1, qty=5)
        self.assertEqual((purse["gp"], purse["cp"]), (15, 1))
        self.assertIn("- Torcia ×5\n", self._text("Aldric"))

    def test_selling_removes_and_pays(self):
        purse, full = self.inv.op_sell("Aldric", "stocco", 1250)
        self.assertEqual(full, "Stocco")
        self.assertEqual((purse["gp"], purse["sp"], purse["cp"]), (27, 5, 6))
        self.assertNotIn("- Stocco", self._text("Aldric"))

    def test_giving_an_item_moves_it_between_sheets(self):
        self.assertEqual(self.inv.op_give("Aldric", "Mira", item="Piede di porco"), "Piede di porco")
        self.assertNotIn("Piede di porco", self._text("Aldric"))
        self.assertIn("- Piede di porco ×2\n", self._text("Mira"))

    def test_giving_coins_needs_them(self):
        self.inv.op_give("Aldric", "Mira", coins={"gp": 10})
        self.assertIn("**Currency:** 5gp 0sp 6cp", self._text("Aldric"))
        self.assertIn("**Currency:** 25gp 0sp 6cp", self._text("Mira"))
        before = (self._text("Aldric"), self._text("Mira"))
        with self.assertRaises(self.inv.InventoryError):
            self.inv.op_give("Aldric", "Mira", coins={"gp": 10})
        self.assertEqual((self._text("Aldric"), self._text("Mira")), before)

    def test_loot_is_split_in_equal_shares_and_the_remainder_is_reported(self):
        share, rest = self.inv.op_split({"gp": 10, "cp": 1}, ["Aldric", "Mira", "Lio"])
        self.assertEqual((share, rest), (333, 2))
        for name in ("Aldric", "Mira", "Lio"):
            self.assertIn("**Currency:** 18gp 3sp 9cp", self._text(name))

    def test_a_split_with_an_unknown_name_pays_nobody(self):
        before = self._text("Aldric")
        with self.assertRaises(self.inv.InventoryError):
            self.inv.op_split({"gp": 9}, ["Aldric", "Fantasma"])
        self.assertEqual(self._text("Aldric"), before)

    def test_the_sheet_is_found_by_the_name_written_on_it(self):
        (self.dir / "vardamir-ma-feyn.md").write_text(SHEET.format(name="Vardamir Ma'feyn"), encoding="utf-8")
        self.assertEqual(self.inv.find_sheet("Vardamir Ma'feyn").name, "vardamir-ma-feyn.md")
        with self.assertRaises(self.inv.InventoryError):
            self.inv.find_sheet("Nessuno")

    def test_the_command_line_exits_non_zero_and_changes_nothing_on_a_bad_request(self):
        before = self._text("Aldric")
        self.assertEqual(self.inv.main(["pay", "Aldric", "999gp"]), 1)
        self.assertEqual(self.inv.main(["remove", "Aldric", "Unicorno"]), 1)
        self.assertEqual(self._text("Aldric"), before)
        self.assertEqual(self.inv.main(["gain", "Aldric", "2gp", "4sp"]), 0)
        self.assertIn("**Currency:** 17gp 4sp 6cp", self._text("Aldric"))


if __name__ == "__main__":
    unittest.main()
