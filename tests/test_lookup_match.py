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


class ItalianNameTests(unittest.TestCase):
    """Italian names from data/i18n/it.json resolve to the English record."""

    def test_italian_spell(self):
        self.assertEqual(_hit("Palla di fuoco"), ("Fireball", "spells"))

    def test_italian_condition(self):
        self.assertEqual(_hit("Avvelenato"), ("Poisoned", "conditions"))

    def test_alias_with_wizard_name(self):
        self.assertEqual(_hit("Freccia Acida di Melf"), ("Acid Arrow", "spells"))

    def test_accents_are_optional(self):
        self.assertEqual(_hit("velocita"), ("Haste", "spells"))
        self.assertEqual(_hit("Velocità"), ("Haste", "spells"))

    def test_italian_prefix(self):
        self.assertEqual(_hit("dardo inc"), ("Magic Missile", "spells"))

    def test_italian_with_category(self):
        self.assertEqual(_hit("Scudo", "spell"), ("Shield", "spells"))

    def test_italian_typo_suggests_english_name(self):
        names = [nm for nm, _ in lookup.suggest("Palla di fucoo", ruleset="2014")]
        self.assertIn("Fireball", names)

    def test_every_overlay_key_is_a_real_record(self):
        import json
        srd = json.loads((SKILL / "data" / "dnd5e_srd.json").read_text(encoding="utf-8"))
        files = sorted((SKILL / "data" / "i18n" / "it").glob("*.json"))
        self.assertTrue(files)
        for path in files:
            overlay = json.loads(path.read_text(encoding="utf-8"))
            for cat, entries in overlay.items():
                if cat.startswith("_"):
                    continue
                real = {r["index"] for r in srd.get(cat, [])}
                self.assertFalse(set(entries) - real, f"{path.name}: unknown {cat} keys")


class TranslatedCardTests(unittest.TestCase):
    """lookup_translated() builds the Italian card, English name in the title."""

    def test_condition_card_in_italian(self):
        text = lookup.lookup_translated("Avvelenato", "it", ruleset="2014")
        self.assertTrue(text.startswith("## Avvelenato (Poisoned)"))
        self.assertIn("svantaggio", text)

    def test_english_query_gets_the_same_card(self):
        self.assertEqual(lookup.lookup_translated("poisoned", "it", ruleset="2014"),
                         lookup.lookup_translated("avvelenato", "it", ruleset="2014"))

    def test_english_lang_has_no_translation(self):
        self.assertIsNone(lookup.lookup_translated("poisoned", "en", ruleset="2014"))

    def test_unknown_lang_has_no_translation(self):
        self.assertIsNone(lookup.lookup_translated("poisoned", "xx", ruleset="2014"))

    def test_name_only_record_has_no_card(self):
        # A record with a translated name but no translated description
        rec = {"name": "X", "index": "x", "_i18n": {"it": {"name": "Ics"}}}
        self.assertIsNone(lookup._localize(rec, "it"))

    def test_vocab_translates_strings_and_lists(self):
        lookup._vocab_by_lang.setdefault("zz", {})
        lookup._vocab_by_lang["zz"].update({"school": {"Evocation": "Invocazione"},
                                            "classes": {"Wizard": "Mago"}})
        rec = {"name": "Fireball", "index": "fireball", "school": "Evocation",
               "classes": ["Wizard", "Sorcerer"],
               "_i18n": {"zz": {"name": "Palla di Fuoco", "description": "..."}}}
        loc = lookup._localize(rec, "zz")
        self.assertEqual(loc["school"], "Invocazione")
        self.assertEqual(loc["classes"], ["Mago", "Sorcerer"])
        self.assertEqual(loc["_name_en"], "Fireball")
        del lookup._vocab_by_lang["zz"]

    def test_english_card_unchanged(self):
        text = lookup.lookup("Fireball", ruleset="2014")
        self.assertTrue(text.startswith("## Fireball  [Level 3"))
        self.assertIn("Casting time : ", text)


if __name__ == "__main__":
    unittest.main()
