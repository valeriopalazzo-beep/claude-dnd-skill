"""
test_weather.py — what the day's weather does to the game (DMG 2014 pp. 109-110).

calendar.py rolls (or the DM sets) the weather with its wind, precipitation
and an extreme cold or heat; scripts/weather.py says what that changes:
a strong wind gives ranged weapons disadvantage (the Attacks panel applies
it), rain, snow and fog cost the lookouts -5 passive Perception (travel.py),
a storm at sea hides the landmarks, and hours out in extreme cold or heat
need a Constitution save each, or a level of exhaustion.

Run from repo root:
    python -m pytest tests/test_weather.py -q
"""
from __future__ import annotations

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

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "dnd" if (ROOT / "skills" / "dnd").is_dir() else ROOT
DISPLAY = SKILL / "display"
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(SKILL / "scripts"))

import attacks  # noqa: E402
import travel  # noqa: E402
import weather  # noqa: E402


class FixedDice:
    def __init__(self, *values):
        self.values = list(values)

    def randbelow(self, n):
        return self.values.pop(0) - 1


class Seq(random.Random):
    """randint() gives back the next value of a list."""
    def __init__(self, *values):
        super().__init__(0)
        self.values = list(values)

    def randint(self, a, b):
        return self.values.pop(0)


class EffectTests(unittest.TestCase):
    def test_the_dmg_dice_for_wind_and_rain(self):
        self.assertEqual(weather.intensity("storm", "mild", "temperate", Seq(1, 1, 1)),
                         {"key": "storm", "wind": "strong", "precip": "heavy", "extreme": ""})
        self.assertEqual(weather.intensity("rain", "mild", "temperate", Seq(12, 18, 1))["precip"], "heavy")
        self.assertEqual(weather.intensity("rain", "mild", "temperate", Seq(13, 17, 1))[("wind")], "light")
        self.assertEqual(weather.intensity("clear", "mild", "temperate", Seq(19, 1, 1))["key"], "windy")   # a strong wind
        self.assertEqual(weather.intensity("fog", "mild", "temperate", Seq(20, 1, 1))["wind"], "light")    # it would blow the fog away

    def test_extreme_days(self):
        self.assertEqual(weather.intensity("clear", "freezing", "cold", Seq(1, 1, 1))["extreme"], "cold")
        self.assertEqual(weather.intensity("clear", "freezing", "temperate", Seq(1, 1, 16))["extreme"], "cold")
        self.assertEqual(weather.intensity("clear", "freezing", "temperate", Seq(1, 1, 18))["extreme"], "")
        self.assertEqual(weather.intensity("clear", "hot", "arid", Seq(1, 1, 1))["extreme"], "heat")
        self.assertEqual(weather.intensity("clear", "mild", "arid", Seq(1, 1, 20))["extreme"], "")

    def test_what_it_does(self):
        self.assertEqual(weather.effects({"key": "storm"}), ["wind", "precip", "rain", "storm"])
        self.assertEqual(weather.effects({"key": "snow", "precip": "heavy"}), ["precip"])
        self.assertEqual(weather.effects({"key": "rain"}), [])                  # a drizzle changes nothing
        self.assertEqual(weather.effects({"key": "clear", "extreme": "cold"}), ["cold"])
        self.assertEqual(weather.effects({}), [])
        self.assertTrue(weather.ranged_disadvantage({"key": "windy"}))
        self.assertEqual((weather.perception_penalty({"key": "fog"}), weather.perception_penalty({"key": "cloudy"})), (-5, 0))


class AttackTests(unittest.TestCase):
    SHEET = """# Lyra

## Attacks
| Name | Attack Bonus | Damage | Type | Notes |
|------|-------------|--------|------|-------|
| Arco lungo | +6 | 1d8+3 | perforante | Munizioni |
| Stocco | +5 | 1d8+3 | perforante | Accurata |
| Giavellotto | +5 | 1d6+3 | perforante | Lancio, a distanza |
"""

    def test_a_strong_wind_blows_the_arrows_off(self):
        bow, rapier, javelin = attacks.read_attacks(self.SHEET)
        r = attacks.roll(bow, rng=FixedDice(15, 4, 3), wind=True)
        self.assertEqual((r["advantage"], r["natural"], r["wind"]), ("disadvantage", 4, True))
        self.assertIn("· vento forte", attacks.action_line("", r, "it"))
        r = attacks.roll(bow, "advantage", rng=FixedDice(15, 3), wind=True)
        self.assertEqual((r["advantage"], r["d20"]), ("normal", [15]))
        self.assertIn("vantaggio e svantaggio si annullano", attacks.action_line("", r, "it"))
        self.assertEqual(attacks.roll(javelin, rng=FixedDice(9, 2, 3), wind=True)["advantage"], "disadvantage")
        r = attacks.roll(rapier, rng=FixedDice(9, 3), wind=True)
        self.assertEqual((r["advantage"], r["wind"]), ("normal", False))
        self.assertNotIn("vento", attacks.action_line("", r, "it"))


class TravelTests(unittest.TestCase):
    def test_the_lookouts_see_less_in_the_rain(self):
        data = travel.blank()
        travel.start(data, ["Lyra", "Hernio"], "normal")
        self.assertEqual(travel.watch(data, {"Lyra": 14, "Hernio": 11}, -5)["best"], 9)
        data["pace"] = "fast"            # the fast pace's -5 and the weather's are the same disadvantage
        self.assertEqual(travel.watch(data, {"Lyra": 14, "Hernio": 11}, -5)["best"], 9)
        self.assertEqual(travel.watch(data, {"Lyra": 14, "Hernio": 11})["best"], 9)
        data["pace"] = "slow"
        self.assertEqual(travel.watch(data, {"Lyra": 14, "Hernio": 11})["best"], 14)


def _calendar():
    spec = importlib.util.spec_from_file_location("dnd_calendar_weather_test", SKILL / "scripts" / "calendar.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SHEET_COLD = """# Berta Tully

## Saving Throws
| STR | DEX | CON | INT | WIS | CHA |
|-----|-----|-----|-----|-----|-----|
| +5 | +0 | +4 | +0 | +1 | -1 |

## Equipment & Inventory
**Armour:**
- Cotta di maglia

**Adventuring Gear:**
- Corda
"""

SHEET_WARM = """# Lyra

## Saving Throws
| STR | DEX | CON | INT | WIS | CHA |
|-----|-----|-----|-----|-----|-----|
| +0 | +5 | +1 | +0 | +1 | +2 |

## Equipment & Inventory
**Adventuring Gear:**
- Abiti da freddo
"""


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._old = os.environ.get("DND_CAMPAIGN_ROOT")
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        chars = self.root / "campaigns" / "prova" / "characters"
        chars.mkdir(parents=True)
        (chars / "berta-tully.md").write_text(SHEET_COLD, encoding="utf-8")
        (chars / "lyra.md").write_text(SHEET_WARM, encoding="utf-8")
        self.cal = _calendar()
        self.pushed = []
        self.cal._push_world = lambda campaign, cal: self.pushed.append(self.cal.world_time(cal))
        self.cal._send_date = lambda cal: None
        self.run_cal("init", "--date", "14 Brumosa 1147", "--months", "Gelida,Brumosa", "--month-length", "30")
        self.levels = {}
        saved = (weather._exhaustion_now, weather._set_exhaustion)
        weather._exhaustion_now = lambda name: self.levels.get(name, 0)
        weather._set_exhaustion = lambda name, before, after: self.levels.__setitem__(name, after)
        self.addCleanup(lambda: (setattr(weather, "_exhaustion_now", saved[0]), setattr(weather, "_set_exhaustion", saved[1])))

    def tearDown(self):
        if self._old is None:
            os.environ.pop("DND_CAMPAIGN_ROOT", None)
        else:
            os.environ["DND_CAMPAIGN_ROOT"] = self._old
        self._tmp.cleanup()

    def run_cal(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                self.cal.main(["-c", "prova", *argv])
            except SystemExit:
                pass
        return out.getvalue()

    def test_set_by_hand_and_on_the_display(self):
        out = self.run_cal("weather", "set", "snow", "freezing", "--wind", "forte", "--precip", "heavy", "--extreme", "cold")
        self.assertIn("⚠ strong wind", out)
        self.assertIn("⚠ extreme cold", out)
        self.assertEqual(self.pushed[-1]["weather_effects"], ["wind", "precip", "storm", "cold"])
        w = weather.current("prova")
        self.assertEqual((w["wind"], w["precip"], w["extreme"]), ("strong", "heavy", "cold"))
        self.assertIn("⚠ extreme cold", self.run_cal("weather", "show"))
        self.run_cal("weather", "set", "rain")                       # without the options: what the key says
        self.assertEqual(weather.current("prova")["precip"], "light")
        self.assertIn("is one of", self.run_cal("weather", "set", "rain", "--wind", "uragano"))

    def test_a_rolled_day_has_its_wind_and_rain(self):
        self.run_cal("weather", "roll", "--seed", "3")
        w = json.loads((self.root / "campaigns" / "prova" / "calendar.json").read_text(encoding="utf-8"))["weather"]
        self.assertIn(w["wind"], weather.WINDS)
        self.assertIn(w["precip"], weather.PRECIPS)
        self.assertIn(w["extreme"], weather.EXTREMES)

    def test_the_hours_in_the_cold_are_counted(self):
        self.run_cal("weather", "set", "snow", "freezing", "--extreme", "cold")
        self.assertIn("weather.py expose --hours 3", self.run_cal("advance", "3", "hours"))
        asked = []
        def ask(name, mod, adv, label, dc, timeout):
            asked.append((name, mod, adv, dc))
            return 8 if len(asked) == 1 else 14
        lines = weather.expose("prova", ["Berta", "Lyra"], 2, "cold", ask=ask)
        self.assertIn("Lyra: succeeds by itself (cold-weather gear)", lines)
        self.assertEqual(asked, [("Berta Tully", 4, "normal", 10), ("Berta Tully", 4, "normal", 10)])
        self.assertIn("hour 1, Berta Tully: 8 vs DC 10 — FAILS: exhaustion 1", lines)
        self.assertIn("hour 2, Berta Tully: 14 vs DC 10 — holds", lines)
        self.assertEqual(self.levels, {"Berta Tully": 1})
        self.assertIn("adapted to it", weather.expose("prova", ["Berta"], 1, "cold", adapted=("berta",), ask=ask)[0])

    def test_the_heat_grows_hour_by_hour(self):
        self.assertEqual([weather.heat_dc(h) for h in (1, 2, 5)], [5, 6, 9])
        asked = []
        weather.expose("prova", ["Berta"], 3, "heat", start=2, ask=lambda *a: asked.append(a) or 20)
        self.assertEqual([(a[2], a[4]) for a in asked], [("disadvantage", 6), ("disadvantage", 7), ("disadvantage", 8)])
        with self.assertRaisesRegex(weather.WeatherError, "not extreme"):
            weather.expose("prova", ["Berta"], 1, "")

    def test_rolled_here_and_to_exhaustion_six(self):
        self.levels["Berta Tully"] = 5
        lines = weather.expose("prova", ["Berta"], 1, "cold", roll_here=True, rng=Seq(1, 1))
        self.assertEqual(lines, ["hour 1, Berta Tully: 5 vs DC 10 — FAILS: exhaustion 6 — DEAD (exhaustion 6)"])


class PageTests(unittest.TestCase):
    def test_strings_and_hooks(self):
        keys = [f"wxfx.{e}" for e in weather.EFFECTS] + [f"wxfx.short.{e}" for e in weather.EFFECTS] + ["atk.wind"]
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))
        html = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("function _weatherEffects(wt)", html)
        self.assertIn("_weatherEffects(wt).forEach", html)
        self.assertIn("weather_effects", (DISPLAY / "templates" / "dm.html").read_text(encoding="utf-8"))
        src = (DISPLAY / "dnd-display-app.py").read_text(encoding="utf-8")
        self.assertIn("wind=_attacks.weather_wind(_active_campaign())", src)
        self.assertIn("weather_mod=_weather_perception()", src)


if __name__ == "__main__":
    unittest.main()
