"""
test_srd_features_2014.py — the 2014 dataset holds the 2014 class features.

Until 2026-10-15 build_srd.py took every class feature from foundry's
classes24 folder, whatever the ruleset: a 2014 campaign looking up its
paladin got Weapon Mastery and Epic Boon, and found no Divine Smite, no Ki,
no Pact Boon. A 2014 build now reads 5e-bits' 5e-SRD-Features.json and
5e-SRD-Traits.json (SRD 5.1); foundry only feeds a 2024 build.

Run from repo root:
    python3 -m unittest tests.test_srd_features_2014 -v
"""
import importlib.util
import json
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
SCRIPTS = SKILL / "scripts"
DATASET = SKILL / "data" / "dnd5e_srd.json"
sys.path.insert(0, str(SCRIPTS))

_spec = importlib.util.spec_from_file_location("build_srd", SCRIPTS / "build_srd.py")
build_srd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_srd)


class TestNormalisers(unittest.TestCase):
    def test_class_feature_keeps_class_subclass_and_level(self):
        r = build_srd._norm_bits_feature({
            "index": "life-disciple-of-life", "name": "Disciple of Life", "level": 1,
            "class": {"index": "cleric", "name": "Cleric"},
            "subclass": {"index": "life", "name": "Life"},
            "desc": ["Healing spells restore more.", "2 + the spell's level."],
        })
        self.assertEqual(r["class"], "cleric")
        self.assertEqual(r["subclass"], "Life")
        self.assertEqual(r["level_req"], 1)
        self.assertEqual(r["type"], "class")
        self.assertIn("2 + the spell's level.", r["description"])

    def test_base_class_feature_has_no_subclass(self):
        r = build_srd._norm_bits_feature({
            "index": "rage", "name": "Rage", "level": 1,
            "class": {"index": "barbarian"}, "desc": ["Bonus action."],
        })
        self.assertIsNone(r["subclass"])

    def test_trait_lists_its_races_and_subraces(self):
        r = build_srd._norm_bits_trait({
            "index": "dwarven-toughness", "name": "Dwarven Toughness",
            "races": [], "subraces": [{"index": "hill-dwarf", "name": "Hill Dwarf"}],
            "desc": ["+1 hit point per level."],
        })
        self.assertEqual(r["type"], "race")
        self.assertIsNone(r["class"])
        self.assertEqual(r["races"], ["Hill Dwarf"])

    def test_a_2014_build_reads_features_from_5e_bits(self):
        self.assertIn("features", build_srd.BITS_FILES_2014)
        self.assertIn("traits", build_srd.BITS_FILES_2014)


class TestBundledDataset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(DATASET, encoding="utf-8") as f:
            cls.data = json.load(f)
        cls.names = {r["name"] for r in cls.data["features"]}

    def test_dataset_is_2014(self):
        self.assertEqual(self.data["_meta"]["ruleset"], "2014")

    def test_2014_features_are_there(self):
        for name in ("Rage", "Divine Smite", "Ki", "Pact Boon", "Darkvision"):
            self.assertIn(name, self.names)
        # 5e-bits names each step of a scaling feature: "Song of Rest (d6)"
        for prefix in ("Brutal Critical (", "Song of Rest (", "Natural Explorer (",
                       "Channel Divinity: Turn Undead"):
            self.assertTrue(any(n.startswith(prefix) for n in self.names), prefix)

    def test_no_2024_features(self):
        for name in ("Weapon Mastery", "Epic Boon", "Brutal Strike", "Monk's Focus"):
            self.assertNotIn(name, self.names)


if __name__ == "__main__":
    unittest.main()
