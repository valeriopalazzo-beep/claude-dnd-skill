"""The printable character sheet (display/sheet_print.py).

Pins what the PDF download relies on: the .md template is read into the
right boxes, live stats from the table win over the file, labels and units
are translated, and nothing from the sheet reaches the page unescaped (the
page is printed by a real browser on the DM's PC).
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"


def _load():
    spec = importlib.util.spec_from_file_location("sheet_print_under_test", DISPLAY / "sheet_print.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


SHEET = """# Mira Vento
**Player:** Anna  **Campaign:** prova  **Last Updated:** 2026-10-03

## Identity
- **Race:** Elfa dei Boschi | **Class:** Ranger | **Level:** 3 | **Background:** Forestiero
- **Alignment:** Neutrale buono | **XP:** 900 / 2700
- **Image look:** tall wood elf ranger
- **Inspiration:** ✓

## Character Pillar
- **Player's sentence:** *"Cerca la sorella scomparsa."*
- **Active hooks:** la sorella è a Porto Gramo

## Campaign History
- **Origin campaign:** prova

## Ability Scores
| STR | DEX | CON | INT | WIS | CHA |
|-----|-----|-----|-----|-----|-----|
| 10 (+0) | 17 (+3) | 14 (+2) | 10 (+0) | 15 (+2) | 8 (-1) |

## Combat Stats
- **HP:** 24 / 28 | **Temp HP:** 0
- **AC:** 15 (cuoio borchiato 12 + DES 3) | **Initiative:** +3 | **Speed:** 35 ft
- **Hit Dice:** 3d10 (remaining: 2)
- **Death Saves:** Successes: 1 | Failures: 0

## Saving Throws
| STR | DEX | CON | INT | WIS | CHA |
|-----|-----|-----|-----|-----|-----|
| +2* | +5* | +2 | +0 | +2 | -1 |

*\\* = proficient (proficiency bonus +2)*

## Skills
| Skill | Ability | Bonus | Proficient |
|-------|---------|-------|-----------|
| Perception | WIS | +4 | ✓ |
| Stealth | DEX | +5 | ✓ |

**Languages:** Comune, Elfico

## Attacks
| Name | Attack Bonus | Damage | Type | Notes |
|------|-------------|--------|------|-------|
| Arco lungo | +7 | 1d8+3 | perforante | Gittata 150/600 ft (45/180 m) |
| <script>x</script> | +1 | 1 | — | — |

## Spell Slots (if applicable)
| Level | Total | Used |
|-------|-------|------|
| 1st | 3 | 1 |

## Known Spells / Cantrips
- **1° livello:** Marchio del cacciatore, Cura ferite

## Features & Traits

### Ranger
- **Nemico prescelto:** goblinoidi.

## Equipment & Inventory
**Weapons:**
- Arco lungo

**Currency:** 12gp 3sp 0cp

## Backstory & Notes
- Cresciuta nei boschi.
"""


class SheetPrintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sp = _load()
        cls.it = cls.sp.Strings(str(DISPLAY / "i18n"), "it")
        cls.en = cls.sp.Strings(str(DISPLAY / "i18n"), "en")

    def test_reads_the_template_into_its_boxes(self):
        sh = self.sp.Sheet(SHEET, None)
        self.assertEqual(sh.name, "Mira Vento")
        self.assertEqual(sh.player, "Anna")
        self.assertEqual(sh.level, 3)
        self.assertEqual(sh.xp, (900, 2700))
        self.assertTrue(sh.inspiration)
        self.assertEqual(sh.scores["dex"], (17, 3))
        self.assertEqual(sh.scores["cha"], (8, -1))
        self.assertEqual(sh.saves["dex"], (5, True))
        self.assertEqual(sh.saves["con"], (2, False))
        self.assertEqual(sh.prof, 2)
        self.assertEqual(sh.hp, [24, 28])
        self.assertEqual(sh.ac, 15)
        self.assertEqual((sh.hit_die, sh.hit_max, sh.hit_left), ("d10", 3, 2))
        self.assertEqual(sh.death, (1, 0))
        self.assertEqual(sh.passive, 14)            # 10 + Perception
        self.assertEqual(sh.slots[1], [3, 1])
        self.assertEqual(sh.attacks[0][0], "Arco lungo")

    def test_live_stats_win_over_the_file(self):
        live = {"name": "Mira Vento", "hp": {"current": 5, "max": 28, "temp": 4},
                "spell_slots": {"1": {"used": 3, "max": 3}}, "inspiration": 0,
                "conditions": ["Avvelenato"], "hit_dice": {"die": "d10", "max": 3, "remaining": 0}}
        sh = self.sp.Sheet(SHEET, live)
        self.assertEqual(sh.hp, [5, 28])
        self.assertEqual(sh.temp_hp, 4)
        self.assertEqual(sh.slots[1], [3, 3])
        self.assertFalse(sh.inspiration)
        self.assertEqual(sh.hit_left, 0)
        page = self.sp.render(SHEET, live, self.it)
        self.assertIn("Avvelenato", page)

    def test_page_is_translated_and_leaves_out_dm_notes(self):
        page = self.sp.render(SHEET, None, self.it, pdf_href="/character/Mira/pdf?lang=it")
        for text in ("Abilità", "Attacchi", "Privilegi e tratti", "Percezione", "FOR", "12 mo",
                     "10,5 m", "35 piedi", "45/180 m (150/600 piedi)", "<h3>Ranger</h3>", "Scarica il PDF"):
            self.assertIn(text, page)
        for hidden in ("tall wood elf", "la sorella è a Porto Gramo", "Origin campaign"):
            self.assertNotIn(hidden, page)
        self.assertNotIn("Scarica il PDF", self.sp.render(SHEET, None, self.it, for_pdf=True))

    def test_english_keeps_the_sheet_words(self):
        page = self.sp.render(SHEET, None, self.en)
        self.assertIn("Skills", page)
        self.assertIn("35 ft", page)

    def test_sheet_text_is_escaped(self):
        page = self.sp.render(SHEET, None, self.it)
        self.assertNotIn("<script>x</script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_feet_ranges_convert_both_numbers(self):
        loc = lambda s: self.sp.localize(s, self.it)  # noqa: E731
        self.assertEqual(loc("Linea 5×30 ft (1,5×9 m)"), "Linea 1,5×9 m (5×30 piedi)")
        self.assertEqual(loc("30 ft"), "9 m (30 piedi)")

    def test_language_choice(self):
        d = str(DISPLAY / "i18n")
        self.assertEqual(self.sp.pick_language(d, "it", ""), "it")
        self.assertEqual(self.sp.pick_language(d, "", "it-IT,it;q=0.9"), "it")
        self.assertEqual(self.sp.pick_language(d, "../x", "xx"), "en")


if __name__ == "__main__":
    unittest.main()
