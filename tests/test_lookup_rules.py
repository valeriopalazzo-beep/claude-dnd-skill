"""
test_lookup_rules.py — the rules of play in the SRD dataset (`lookup.py rule`).

Until 2026-10-15 the bundled SRD had spells, items, conditions and monsters
but none of the rules a turn is made of: actions, bonus actions, reactions,
opportunity attacks, cover, movement, resting. The DM ruled them from memory.
build_srd.py now fetches 5e-bits' Rule Sections and splits each section per
heading, so a lookup returns "Opportunity Attacks", not the 10 KB around it.

Run from repo root:
    python3 -m unittest tests.test_lookup_rules -v
"""
import importlib.util
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))

import lookup  # noqa: E402

_spec = importlib.util.spec_from_file_location("build_srd", SCRIPTS / "build_srd.py")
build_srd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_srd)


# Shaped like 5e-SRD-Rule-Sections.json (2014/en): one markdown text per section.
SECTIONS = [
    {"index": "the-order-of-combat", "name": "The Order of Combat", "desc":
        "## The Order of Combat\n\nRounds and turns.\n\n"
        "### Your Turn\n\nMove and act.\n\n"
        "#### Bonus Actions\n\nOnly when something grants one.\n\n"
        "### Reactions\n\nOne per round."},
    {"index": "making-an-attack", "name": "Making an Attack", "desc":
        "## Making an Attack\n\nChoose a target.\n\n"
        "### Melee Attacks\n\nWithin reach.\n\n"
        "#### Opportunity Attacks\n\nWhen a hostile creature leaves your reach.\n\n"
        "#### Grappling\n\nInstead of one attack."},
    {"index": "casting-a-spell", "name": "Casting a Spell", "desc":
        "## Casting a Spell\n\nTime, range, components.\n\n"
        "### Casting Time\n\nUsually an action.\n\n"
        "#### Bonus Action\n\nOnly a cantrip besides.\n\n"
        "#### Reactions\n\nIn response to an event."},
    {"index": "movement", "name": "Movement", "desc":
        "## Movement\n\nTravel.\n\n### Travel Pace\n\nFast, normal, slow.\n\n"
        "#### Travel Pace\n\n| Pace | Distance |"},
]


def _by_name(records):
    return {r["name"]: r for r in records}


class SplitTests(unittest.TestCase):

    def setUp(self):
        self.recs = build_srd._norm_rule_sections(SECTIONS)
        self.names = _by_name(self.recs)

    def test_each_heading_is_a_record_with_its_own_text(self):
        oa = self.names["Opportunity Attacks"]
        self.assertEqual(oa["description"], "When a hostile creature leaves your reach.")
        self.assertEqual(oa["path"], ["Making an Attack", "Melee Attacks"])
        self.assertEqual(oa["index"], "making-an-attack/melee-attacks/opportunity-attacks")

    def test_a_heading_keeps_its_subheadings_as_bold_lines(self):
        melee = self.names["Melee Attacks"]["description"]
        self.assertIn("**Opportunity Attacks**", melee)
        self.assertIn("**Grappling**", melee)
        self.assertNotIn("#", melee)

    def test_a_table_under_a_subheading_stays_a_table(self):
        # The blank line between the bold heading and the table must survive,
        # or markdown reads the table as part of the heading's paragraph.
        pace = self.names["Travel Pace"]["description"]
        self.assertIn("**Travel Pace**\n\n| Pace | Distance |", pace)

    def test_indexes_are_unique(self):
        idx = [r["index"] for r in self.recs]
        self.assertEqual(len(idx), len(set(idx)))

    def test_the_combat_rule_keeps_the_plain_name(self):
        # "Bonus Actions" (your turn) and "Bonus Action" (casting time) answer
        # the same query; the general rule must win it.
        self.assertIn("Bonus Actions", self.names)
        self.assertIn("Bonus Action (Casting Time)", self.names)
        self.assertIn("Reactions", self.names)
        self.assertEqual(self.names["Reactions"]["path"], ["The Order of Combat"])
        self.assertIn("Reactions (Casting Time)", self.names)

    def test_a_heading_repeating_its_parent_is_dropped(self):
        self.assertEqual(sum(1 for r in self.recs if r["name"].startswith("Travel Pace")), 1)

    def test_plural_headings_answer_the_singular(self):
        self.assertIn("Opportunity Attack", self.names["Opportunity Attacks"]["aliases"])
        self.assertIn("attack of opportunity", self.names["Opportunity Attacks"]["aliases"])
        self.assertIn("grapple", self.names["Grappling"]["aliases"])


class BundledRulesLookupTests(unittest.TestCase):
    """Against the bundled dataset: the queries a DM types mid-fight."""

    def _hit(self, query):
        rec = lookup.lookup_record(query, category="rule", ruleset="2014")
        return (rec or {}).get("name")

    def test_turn_rules_are_there(self):
        for query, name in [
            ("opportunity attack", "Opportunity Attacks"),
            ("bonus action", "Bonus Actions"),
            ("reaction", "Reactions"),
            ("cover", "Cover"),
            ("difficult terrain", "Difficult Terrain"),
            ("prone", "Being Prone"),
            ("grapple", "Grappling"),
            ("shove", "Shoving a Creature"),
            ("two-weapon fighting", "Two-Weapon Fighting"),
            ("disengage", "Disengage"),
            ("dodge", "Dodge"),
            ("ready", "Ready"),
            ("surprise", "Surprise"),
            ("death saves", "Death Saving Throws"),
            ("concentration", "Concentration"),
            ("short rest", "Short Rest"),
            ("long rest", "Long Rest"),
            ("jump", "Jumping"),
            ("falling", "Falling"),
        ]:
            with self.subTest(query=query):
                self.assertEqual(self._hit(query), name)

    def test_italian_queries_find_the_english_rule(self):
        for query, name in [
            ("attacco di opportunità", "Opportunity Attacks"),
            ("azione bonus", "Bonus Actions"),
            ("lottare", "Grappling"),
            ("copertura", "Cover"),
            ("disimpegno", "Disengage"),
            ("riposo lungo", "Long Rest"),
        ]:
            with self.subTest(query=query):
                self.assertEqual(self._hit(query), name)

    def test_card_shows_where_the_rule_sits(self):
        text = lookup.lookup("opportunity attacks", category="rule", ruleset="2014")
        self.assertTrue(text.startswith("## Opportunity Attacks"))
        self.assertIn("Making an Attack › Melee Attacks", text)
        self.assertIn("Disengage", text)

    def test_every_rule_has_an_italian_card(self):
        lookup._load_ruleset("2014")
        rules = lookup._data_by_rs["2014"]["rules"]
        missing = [r["index"] for r in rules if lookup._localize(r, "it") is None]
        self.assertEqual(missing, [])

    def test_italian_card_is_translated_with_the_original_name(self):
        text = lookup.lookup_translated("attacco di opportunità", "it",
                                        category="rule", ruleset="2014")
        self.assertTrue(text.startswith("## Attacchi di opportunità (Opportunity Attacks)"))
        self.assertIn("Effettuare un attacco › Attacchi in mischia", text)
        self.assertIn("Disimpegno", text)
        self.assertNotIn("Disengage", text)

    def test_italian_rules_use_metres(self):
        text = lookup.lookup_translated("squeeze", "it", category="rule", ruleset="2014")
        self.assertIn("1,5 metri", text)
        self.assertNotIn("feet", text)

    def test_rules_stay_out_of_the_uncategorized_search(self):
        # The display's free search sends no category: "Shield" is still the
        # item or the spell, never a rule heading.
        rec = lookup.lookup_record("Dash", ruleset="2014")
        self.assertNotEqual((rec or {}).get("_cat"), "rules")


if __name__ == "__main__":
    unittest.main()
