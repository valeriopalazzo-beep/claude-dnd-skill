"""
test_lookup_match.py — best-match ranking for lookup_record() without a category.

The display's free search sends no category. It used to stop at the first
category with any hit, so "Fireball" opened "Necklace of Fireballs".

Run from repo root:
    python3 -m unittest tests.test_lookup_match -v
"""
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
sys.path.insert(0, str(SKILL / "scripts"))

import lookup  # noqa: E402


def _hit(query, category=None):
    rec = lookup.lookup_record(query, category=category, ruleset="2014")
    return (rec or {}).get("name"), (rec or {}).get("_cat")


class UncategorizedMatchTests(unittest.TestCase):

    def test_exact_spell_beats_partial_item(self):
        self.assertEqual(_hit("Fireball"), ("Fireball", "spells"))

    def test_prefix_beats_contains(self):
        self.assertEqual(_hit("fireb"), ("Fireball", "spells"))

    def test_exact_monster(self):
        self.assertEqual(_hit("Goblin"), ("Goblin", "monsters"))

    def test_exact_condition(self):
        self.assertEqual(_hit("poisoned"), ("Poisoned", "conditions"))

    def test_tie_keeps_items_first(self):
        # "Shield" is both armor and a spell: equal score, items win as before.
        self.assertEqual(_hit("Shield"), ("Shield", "equipment"))

    def test_explicit_category_still_scopes(self):
        self.assertEqual(_hit("Shield", "spell"), ("Shield", "spells"))

    def test_item_category_searches_items_only(self):
        name, cat = _hit("Fireball", "item")
        self.assertIn(cat, ("equipment", "magic_items"))

    def test_lookup_text_is_the_spell(self):
        text = lookup.lookup("Fireball", ruleset="2014")
        self.assertIn("Fireball", text)
        self.assertNotIn("Necklace", text)


if __name__ == "__main__":
    unittest.main()
