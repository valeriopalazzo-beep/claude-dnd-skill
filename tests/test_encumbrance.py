"""
test_encumbrance.py — what a character carries, and what it costs them.

scripts/encumbrance.py weighs every item of a sheet's `## Equipment &
Inventory` from the SRD equipment (English or Italian names, packs by their
contents), adds the coins (50 to the pound) and compares the total with the
limits Strength sets — the standard rule, or the variant one when state.md
says `encumbrance: variant`. inventory.py tells the DM when a change costs
speed, and the character card on the display shows the load.

Run from repo root:
    python -m pytest tests/test_encumbrance.py -q
"""
import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"
sys.path.insert(0, str(SKILL / "scripts"))

import encumbrance as enc  # noqa: E402
import inventory  # noqa: E402

SHEET = """# {name}
**Player:** Test

## Ability Scores
| STR | DEX | CON | INT | WIS | CHA |
|-----|-----|-----|-----|-----|-----|
| {str} (+0) | 14 (+2) | 12 (+1) | 10 (+0) | 10 (+0) | 10 (+0) |

## Features & Traits
{traits}

## Equipment & Inventory
**Weapons:**
- Spadone
- 2 pugnali

**Armour:**
- Cotta di maglia

**Adventuring Gear:**
- Zaino da dungeon (zaino, piede di porco, 10 torce, 10 razioni)
- Fiaschetta di grappa di mele (presa all'incappucciato)
- Cimelio misterioso

**Currency:** 100gp 0sp 0cp

## Backstory & Notes
- —
"""


class WeightTests(unittest.TestCase):
    def test_names_lose_accents_brackets_and_counts(self):
        self.assertEqual(enc.norm("Cotta di Maglia (indossata) ×2"), "cotta di maglia")
        self.assertEqual(enc.norm("Arnesi da Scasso [Ajeje]"), "arnesi da scasso")
        self.assertEqual(enc.split_qty("10 torce"), ("torce", 10))
        self.assertEqual(enc.split_qty("Ascia (1) (l'altra persa)"), ("Ascia (l'altra persa)", 1))
        self.assertEqual(enc.split_qty("Corda di canapa (15 m)"), ("Corda di canapa (15 m)", 1))

    def test_english_and_italian_names_weigh_the_same(self):
        for en, it in (("Greatsword", "Spadone"), ("Chain Mail", "Cotta di maglia"), ("Crowbar", "Piede di porco"),
                       ("Thieves' Tools", "Grimaldelli"), ("Waterskin", "Borraccia")):
            with self.subTest(item=it):
                self.assertIsNotNone(enc.weight_of(en))
                self.assertEqual(enc.weight_of(en), enc.weight_of(it))

    def test_packs_plurals_ammunition_and_longer_names(self):
        self.assertEqual(enc.weight_of("Zaino da dungeon (zaino, piede di porco)"), 61.5)
        self.assertEqual(enc.weight_of("Dotazione da Esploratore"), 59)
        self.assertEqual(enc.weight_of("Pacchetto del diplomatico"), 36)
        self.assertEqual(enc.weight_of("pugnali"), enc.weight_of("Pugnale"))
        self.assertEqual(enc.weight_of("giavellotti"), enc.weight_of("Giavellotto"))
        self.assertEqual(enc.weight_of("frecce"), 1 / 20)
        self.assertEqual(enc.weight_of("Ascia da battaglia di Orm (nanica, rune)"), enc.weight_of("Battleaxe"))
        self.assertEqual(enc.weight_of("Pozione di guarigione"), 0.5)
        self.assertIsNone(enc.weight_of("Cimelio misterioso"))
        self.assertEqual(enc.weight_of("Cimelio misterioso", {"cimelio misterioso": 3.0}), 3.0)

    def test_weights_are_read_in_pounds_or_kilograms(self):
        self.assertEqual(enc.parse_weight("2lb"), 2)
        self.assertEqual(enc.parse_weight("0,5 kg"), 1)
        self.assertEqual(enc.parse_weight("3"), 3)
        self.assertEqual(enc.parse_weight("3", "it"), 6)
        with self.assertRaises(enc.EncumbranceError):
            enc.parse_weight("tanto")
        self.assertEqual((enc.fmt_weight(63, "it"), enc.fmt_weight(63.5)), ("31,5 kg", "63.5 lb"))


class ReportTests(unittest.TestCase):
    def sheet(self, strength=19, traits="- —"):
        return SHEET.format(name="Prova", str=strength, traits=traits)

    def test_the_total_counts_items_quantities_and_coins(self):
        rep = enc.report(self.sheet(), rule="standard", weights={})
        expected = 6 + 2 * 1 + 55 + 61.5 + 1 + 100 / 50          # the flask counts as a fiaschetta
        self.assertAlmostEqual(rep["total"], expected)
        self.assertEqual(rep["unknown"], ["Cimelio misterioso"])
        self.assertEqual((rep["strength"], rep["limits"]["capacity"], rep["status"], rep["speed"]), (19, 285, "ok", 0))

    def test_the_variant_rule_slows_a_weak_character(self):
        self.assertEqual(enc.report(self.sheet(13), rule="standard", weights={})["status"], "ok")
        rep = enc.report(self.sheet(13), rule="variant", weights={})
        self.assertEqual((rep["status"], rep["speed"]), ("encumbered", -10))   # 127.5 lb: over 65, not over 130
        rep = enc.report(self.sheet(12), rule="variant", weights={})
        self.assertEqual((rep["status"], rep["speed"]), ("heavy", -30))        # over 120, and chain mail wants STR 13
        rep = enc.report(self.sheet(19), rule="variant", weights={})
        self.assertEqual((rep["status"], rep["speed"]), ("encumbered", -10))   # 127.5 > 95, ≤ 190
        self.assertIn("ENCUMBERED", enc.warning(rep))

    def test_beyond_capacity_and_heavy_armour_without_strength(self):
        rep = enc.report(self.sheet(8), rule="standard", weights={})
        self.assertEqual((rep["status"], rep["speed"]), ("over", None))
        self.assertEqual(rep["armour"], [("Cotta di maglia", 13)])
        self.assertIn("OVER CAPACITY", enc.warning(rep, "it"))
        self.assertIn("needs STR 13", enc.warning(rep))

    def test_powerful_build_doubles_the_limits(self):
        rep = enc.report(self.sheet(8, "- **Powerful Build:** You count as one size larger."), rule="variant", weights={})
        self.assertEqual((rep["limits"]["capacity"], rep["powerful"]), (240, True))

    def test_the_card_gets_numbers_not_the_list(self):
        load = enc.display_load(enc.report(self.sheet(19), rule="variant", weights={}))
        self.assertEqual(set(load), {"lb", "capacity", "encumbered", "heavy", "status", "rule", "speed", "unknown", "armour"})
        self.assertEqual((load["capacity"], load["encumbered"], load["heavy"], load["unknown"]), (285, 95, 190, 1))


class CommandTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        self.camp = self.root / "campaigns" / "prova"
        (self.camp / "characters").mkdir(parents=True)
        (self.root / "runtime").mkdir()
        (self.camp / "state.md").write_text("# State\n- language: it\n- encumbrance: variant\n", encoding="utf-8")
        (self.camp / "characters" / "ajeje.md").write_text(SHEET.format(name="Ajeje", str=12, traits="- —"), encoding="utf-8")
        for var, value in (("DND_CAMPAIGN_ROOT", str(self.root)), ("DND_RUNTIME_DIR", str(self.root / "runtime"))):
            old = os.environ.get(var)
            os.environ[var] = value
            self.addCleanup(lambda var=var, old=old: os.environ.pop(var, None) if old is None else os.environ.__setitem__(var, old))
        self.pushed = []
        saved = (enc.push, inventory._push)
        enc.push = lambda name, rep: self.pushed.append((name, rep["status"]))
        inventory._push = lambda args: None
        self.addCleanup(lambda: (setattr(enc, "push", saved[0]), setattr(inventory, "_push", saved[1])))

    def run_cli(self, mod, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = mod.main(["--campaign", "prova", *argv])
        return code, out.getvalue() + err.getvalue()

    def test_show_reads_the_campaigns_rule_and_language(self):
        code, out = self.run_cli(enc, "show", "Ajeje")
        self.assertEqual(code, 0, out)
        self.assertIn("[variant rule]", out)
        self.assertIn("kg", out)
        self.assertIn("HEAVILY ENCUMBERED", out)
        self.assertIn("Cimelio misterioso: ? (unknown", out)
        self.assertEqual(self.pushed, [("Ajeje", "heavy")])

    def test_a_weight_given_once_counts_for_everyone(self):
        code, out = self.run_cli(enc, "weight", "Cimelio misterioso", "1,5")
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads((self.camp / "weights.json").read_text(encoding="utf-8")), {"cimelio misterioso": 3.0})
        self.assertNotIn("unknown", self.run_cli(enc, "show", "Ajeje")[1])
        self.assertEqual(self.run_cli(enc, "weight", "cimelio misterioso", "--forget")[0], 0)
        self.assertEqual(self.run_cli(enc, "weight", "cimelio misterioso", "--forget")[0], 1)
        self.assertEqual(self.run_cli(enc, "weight", "Altro", "tanto")[0], 1)

    def test_party_lists_every_character_of_the_campaign(self):
        code, out = self.run_cli(enc, "party")
        self.assertEqual(code, 0, out)
        self.assertIn("Ajeje:", out)
        self.assertIn("no weight for: Cimelio misterioso", out)

    def test_inventory_warns_when_a_change_costs_speed(self):
        code, out = self.run_cli(inventory, "remove", "Ajeje", "Cotta di maglia")
        self.assertEqual(code, 0, out)
        self.assertIn("⚠ Ajeje: ENCUMBERED", out)        # 72.5 lb: over 12 × 5, under 12 × 10
        code, out = self.run_cli(inventory, "add", "Ajeje", "Cotta di maglia", "--section", "armour")
        self.assertIn("⚠ Ajeje: HEAVILY ENCUMBERED", out)
        self.assertEqual(self.pushed[-1], ("Ajeje", "heavy"))

    def test_it_runs_as_a_script(self):
        env = dict(os.environ, PYTHONUTF8="1")
        r = subprocess.run([sys.executable, str(SKILL / "scripts" / "encumbrance.py"), "-c", "prova", "party"],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn("Ajeje:", r.stdout)


class PageTests(unittest.TestCase):
    def test_the_card_draws_the_load(self):
        html = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("p.load && p.load.capacity", html)
        self.assertIn("function _loadWeight", html)

    def test_strings_exist_in_every_language(self):
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("stat.load", "load.encumbered", "load.heavy", "load.over", "load.armour"):
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
