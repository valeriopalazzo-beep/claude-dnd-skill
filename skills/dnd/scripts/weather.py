#!/usr/bin/env python3
"""weather.py — what the day's weather does to the game (DMG 2014 pp. 109-110).

calendar.py rolls or sets the weather; this says what it changes, and runs
the hours spent out in extreme cold or heat.

    weather.py show                                  # today's weather and its effects
    weather.py expose --hours 3                      # every PC at the table, out in it for 3 hours
    weather.py expose --hours 2 --only "Lyra, Hernio" --gear "Lyra" --adapted "Berta"
    weather.py expose --hours 4 --no-water --start 3 # heat: the 3rd hour on, no drinkable water
    weather.py expose --hours 1 --roll               # roll the saves here instead of on the phones

Effects:
- strong wind: disadvantage on ranged weapon attacks and on Perception that
  relies on hearing; open flames go out, fog is blown away; a creature flying
  without magic must land at the end of its turn or fall.
- heavy rain or snow: lightly obscured, disadvantage on Perception by sight;
  heavy rain puts out open flames and disadvantages hearing too.
- fog: lightly obscured (PHB p. 183; dense fog is heavily obscured — say so).
- a storm (strong wind and heavy rain): at sea, no landmarks and disadvantage
  to find the way.
- extreme cold: at the end of every hour exposed, a DC 10 Constitution save
  or one level of exhaustion; resistance or immunity to cold, cold-weather
  gear and creatures adapted to it succeed by themselves.
- extreme heat, without drinkable water: at the end of every hour, a
  Constitution save, DC 5 the first hour and 1 more each hour after, or one
  level of exhaustion; disadvantage in medium or heavy armour or heavy
  clothing; resistance or immunity to fire and adapted creatures succeed.

The Attacks panel gives ranged weapons disadvantage in a strong wind by
itself, and travel.py counts it in the lookouts' passive Perception (-5).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import paths  # noqa: E402  (also forces UTF-8 output on Windows)

WINDS = ("none", "light", "strong")
PRECIPS = ("none", "light", "heavy")
EXTREMES = ("", "cold", "heat")
WIND_ALIASES = {"nessuno": "none", "calmo": "none", "calma": "none", "calm": "none", "leggero": "light",
                "debole": "light", "forte": "strong", "high": "strong"}
PRECIP_ALIASES = {"nessuna": "none", "niente": "none", "leggera": "light", "pioggerella": "light",
                  "abbondante": "heavy", "battente": "heavy", "forte": "heavy"}
EXTREME_ALIASES = {"freddo": "cold", "caldo": "heat", "hot": "heat", "none": "", "nessuno": "", "off": ""}
EFFECTS = ("wind", "precip", "rain", "fog", "storm", "cold", "heat")
TEXT = {
    "wind": "strong wind: disadvantage on ranged weapon attacks and on Perception by hearing; open flames go out, "
            "fog is blown away; flying without magic: land at the end of the turn or fall",
    "precip": "heavy precipitation: lightly obscured, disadvantage on Perception by sight",
    "rain": "pouring rain: open flames go out, disadvantage on Perception by hearing too",
    "fog": "fog: lightly obscured, disadvantage on Perception by sight (dense fog: heavily obscured — say so)",
    "storm": "storm: at sea no landmarks (but a lighthouse) and disadvantage to find the way",
    "cold": "extreme cold: DC 10 Constitution save at the end of every hour exposed, or a level of exhaustion "
            "(resistance/immunity to cold, cold-weather gear and adapted creatures succeed) — weather.py expose",
    "heat": "extreme heat without drinkable water: Constitution save at the end of every hour (DC 5, +1 each hour "
            "after the first) or a level of exhaustion; disadvantage in medium/heavy armour or heavy clothing — weather.py expose",
}
EXHAUSTION = [f"Exhaustion ({n})" for n in range(1, 7)]       # as the display writes it
_MEDIUM_HEAVY = re.compile(r"\b(?:pelle grezza|hide|giaco di maglia|chain shirt|corazza di scaglie|scale mail|"
                           r"corazza di piastre|breastplate|mezza armatura|half plate|corazza ad anelli|ring mail|"
                           r"cotta di maglia|chain mail|corazza a strisce|splint|armatura completa|plate armou?r)\b", re.I)
_HEAVY_CLOTHES = re.compile(r"abiti pesanti|vestiti pesanti|heavy cloth|pelliccia|furs?\b", re.I)
_COLD_GEAR = re.compile(r"abiti da freddo|abiti invernali|vestiti invernali|vestiti pesanti|abiti pesanti|pelliccia|"
                        r"cold[- ]weather|winter cloth", re.I)


class WeatherError(Exception):
    """A request that cannot be applied; nothing was changed."""


# ─── The day's weather ───────────────────────────────────────────────────────

def _one(value: str, table: tuple, aliases: dict, what: str) -> str:
    v = (value or "").strip().lower()
    v = aliases.get(v, v)
    if v not in table:
        raise WeatherError(f"{what} is one of {', '.join(t or '(none)' for t in table)} — not {value!r}")
    return v


def wind_of(v: str) -> str:
    return _one(v, WINDS, WIND_ALIASES, "the wind")


def precip_of(v: str) -> str:
    return _one(v, PRECIPS, PRECIP_ALIASES, "the precipitation")


def extreme_of(v: str) -> str:
    return _one(v, EXTREMES, EXTREME_ALIASES, "--extreme")


def intensity(key: str, temp: str, climate: str, rng: random.Random) -> dict:
    """Wind, precipitation and extreme temperature for a day of `key`/`temp` weather, with the DMG's d20s (p. 109).

    Returns {"key", "wind", "precip", "extreme"}; a strong wind on a dry day makes it a windy day."""
    d = rng.randint(1, 20)
    wind = "strong" if key in ("windy", "storm") else "none" if d <= 12 else "light" if d <= 17 else "strong"
    if key == "fog" and wind == "strong":
        wind = "light"                     # a strong wind blows fog away
    d = rng.randint(1, 20)
    precip = "heavy" if key == "storm" else ("heavy" if d >= 18 else "light") if key in ("rain", "snow") else "none"
    d = rng.randint(1, 20)
    extreme = ""
    if temp == "freezing" and (climate == "cold" or 15 <= d <= 17):
        extreme = "cold"
    elif temp == "hot" and (climate == "arid" or d >= 18):
        extreme = "heat"
    if wind == "strong" and key in ("clear", "cloudy", "overcast"):
        key = "windy"
    return {"key": key, "wind": wind, "precip": precip, "extreme": extreme}


def filled(weather: dict) -> dict:
    """The weather with wind, precipitation and extreme filled in — older days were saved without them."""
    w = dict(weather or {})
    key = w.get("key", "")
    if w.get("wind") not in WINDS:
        w["wind"] = "strong" if key in ("windy", "storm") else "none"
    if w.get("precip") not in PRECIPS:
        w["precip"] = "heavy" if key == "storm" else "light" if key in ("rain", "snow") else "none"
    if w.get("extreme") not in EXTREMES:
        w["extreme"] = ""
    return w


def effects(weather: dict) -> list:
    """The codes of what the weather does to the game, in EFFECTS order."""
    w = filled(weather)
    if not w.get("key") and not w.get("extreme"):
        return []
    out = []
    if w["wind"] == "strong":
        out.append("wind")
    if w["precip"] == "heavy":
        out.append("precip")
        if w.get("key") in ("rain", "storm"):
            out.append("rain")
    if w.get("key") == "fog":
        out.append("fog")
    if w["wind"] == "strong" and w["precip"] == "heavy":
        out.append("storm")
    if w["extreme"]:
        out.append(w["extreme"])
    return out


def ranged_disadvantage(weather: dict) -> bool:
    return "wind" in effects(weather)


def perception_penalty(weather: dict) -> int:
    """What the weather takes off a passive Perception: -5 when it gives disadvantage (PHB p. 175), else 0."""
    return -5 if set(effects(weather)) & {"wind", "precip", "fog"} else 0


def current(campaign: str) -> dict:
    """The campaign's weather today, filled in; {} when its calendar keeps none."""
    try:
        if str(paths.display_dir()) not in sys.path:
            sys.path.insert(0, str(paths.display_dir()))
        import timeline
        _lib, cal = timeline.calendar_of(campaign)
    except Exception:
        return {}
    w = (cal or {}).get("weather") or {}
    return filled(w) if w.get("key") or w.get("extreme") else {}


def describe(weather: dict) -> list:
    return [TEXT[e] for e in effects(weather)]


# ─── Out in extreme cold or heat ──────────────────────────────────────────────

def protected(text: str, extreme: str) -> str:
    """Why the sheet's character succeeds by itself, or ''."""
    if extreme == "cold":
        if re.search(r"(?:resisten|immun)\w*[^\n|]{0,60}\b(?:freddo|cold)\b|\b(?:freddo|cold)\b[^\n|]{0,20}(?:resisten|immun)",
                     text, re.I):
            return "resistant to cold"
        if _COLD_GEAR.search(text):
            return "cold-weather gear"
    if extreme == "heat":
        if re.search(r"(?:resisten|immun)\w*[^\n|]{0,60}\b(?:fuoco|fire)\b|\b(?:fuoco|fire)\b[^\n|]{0,20}(?:resisten|immun)",
                     text, re.I):
            return "resistant to fire"
    return ""


def heat_disadvantage(text: str) -> str:
    """'medium or heavy armour' / 'heavy clothing' when the sheet wears them, else ''."""
    m = re.search(r"\*\*Armou?r:?\*\*:?(.*?)(?=\n\*\*|\n##|\Z)", text, re.S | re.I)
    worn = (m.group(1) if m else "") + "\n" + "\n".join(re.findall(r"^\*\*AC:?\*\*.*$|^.*\bCA\b.*$", text, re.M))
    if _MEDIUM_HEAVY.search(worn):
        return "medium or heavy armour"
    if _HEAVY_CLOTHES.search(text):
        return "heavy clothing"
    return ""


def heat_dc(hour: int) -> int:
    """DC 5 at the end of the first hour, one more for each hour after it (DMG p. 110)."""
    return 5 + max(0, hour - 1)


def _exhaustion_now(name: str) -> int:
    from runtime_paths import rt
    try:
        with open(rt("stats.json"), encoding="utf-8") as f:
            players = json.load(f).get("players") or []
    except (OSError, ValueError, AttributeError):
        return 0
    for p in players:
        if isinstance(p, dict) and str(p.get("name", "")).casefold() == name.casefold():
            levels = [int(m.group(1)) for c in p.get("conditions") or []
                      for m in [re.match(r"exhaustion\s*\((\d)\)", str(c), re.I)] if m]
            return max(levels) if levels else 0
    return 0


def _set_exhaustion(name: str, before: int, after: int) -> None:
    """One level more on the display (its conditions are where exhaustion lives)."""
    args = [sys.executable, str(paths.display_dir() / "push_stats.py"), "--player", name,
            "--conditions-add", EXHAUSTION[min(6, after) - 1]]
    if before:
        args += ["--conditions-remove", EXHAUSTION[before - 1]]
    try:
        subprocess.run(args, capture_output=True, timeout=10, env=dict(os.environ, PYTHONUTF8="1"))
    except Exception:
        pass


def _ask(name: str, mod: int, adv: str, label: str, dc: int, timeout: int):
    """One Constitution save on the character's phone: its total, None when nobody rolled. Raises when the display is off."""
    sys.path.insert(0, str(paths.display_dir()))
    import group_check
    status, body = group_check._request("/dice-request", {"characters": [name], "spec": "1d20", "modifier": mod,
                                                          "advantage": adv, "label": label[:60], "dc": dc})
    if status != 200 or not isinstance(body, dict):
        raise WeatherError("the display is not running — use --roll to roll the saves here" if status == 0
                           else f"the display said HTTP {status}")
    rid = body["request_id"]
    deadline = time.time() + max(5, timeout)
    while time.time() < deadline:
        status, st = group_check._request(f"/dice-request/{rid}")
        if status == 200 and isinstance(st, dict) and st.get("complete"):
            res = next((r for r in st.get("results") or [] if str(r.get("character", "")).lower() == name.lower()), None)
            return int(res["total"]) if res else None
        time.sleep(0.5)
    return None


def _roll_here(mod: int, adv: str, rng: random.Random) -> int:
    a, b = rng.randint(1, 20), rng.randint(1, 20)
    return (max(a, b) if adv == "advantage" else min(a, b) if adv == "disadvantage" else a) + mod


def expose(campaign: str, names: list, hours: int, extreme: str, start: int = 1, roll_here: bool = False,
           gear: tuple = (), adapted: tuple = (), lang: str = "en", timeout: int = 180, rng=None,
           ask=None) -> list:
    """Hours out in extreme cold or heat: a save at the end of each, a level of exhaustion for each failure."""
    if extreme not in ("cold", "heat"):
        raise WeatherError("the weather is not extreme today — calendar.py weather set … --extreme cold|heat")
    if not 1 <= hours <= 24:
        raise WeatherError("--hours between 1 and 24")
    import inventory
    sys.path.insert(0, str(paths.display_dir()))
    import afflictions
    rng = rng or random.Random()
    lines, people = [], []
    import spellbook                      # finds a sheet by any part of the name
    for raw in names:
        try:
            path = spellbook.find_sheet(raw, campaign)
        except (inventory.InventoryError, spellbook.SpellError):
            lines.append(f"{raw}: no sheet — skipped")
            continue
        text, name = inventory._read(path), inventory.display_name(path)
        why = ("adapted to it" if any(inventory._key(a) in inventory._key(name) for a in adapted if a.strip()) else
               "cold-weather gear" if extreme == "cold" and any(inventory._key(g) in inventory._key(name) for g in gear if g.strip())
               else protected(text, extreme))
        if why:
            lines.append(f"{name}: succeeds by itself ({why})")
            continue
        adv = "disadvantage" if extreme == "heat" and heat_disadvantage(text) else "normal"
        people.append((name, afflictions.save_bonus(text, "CON"), adv, heat_disadvantage(text) if extreme == "heat" else ""))
    for hour in range(start, start + hours):
        dc = 10 if extreme == "cold" else heat_dc(hour)
        for name, mod, adv, why in people:
            label = ((f"Freddo estremo, ora {hour} (TS Costituzione)" if extreme == "cold" else
                      f"Caldo estremo, ora {hour} (TS Costituzione)") if lang == "it" else
                     f"Extreme {extreme}, hour {hour} (CON save)")
            total = _roll_here(mod, adv, rng) if roll_here else (ask or _ask)(name, mod, adv, label, dc, timeout)
            if total is None:
                lines.append(f"hour {hour}, {name}: no roll — nothing applied")
                continue
            if total >= dc:
                lines.append(f"hour {hour}, {name}: {total} vs DC {dc} — holds")
                continue
            before = _exhaustion_now(name)
            after = min(6, before + 1)
            _set_exhaustion(name, before, after)
            lines.append(f"hour {hour}, {name}: {total} vs DC {dc} — FAILS: exhaustion {after}"
                         + (f" ({why}: disadvantage)" if adv == "disadvantage" else "")
                         + (" — DEAD (exhaustion 6)" if after >= 6 else ""))
    return lines


def _party() -> list:
    from runtime_paths import rt
    try:
        with open(rt("stats.json"), encoding="utf-8") as f:
            players = json.load(f).get("players") or []
    except (OSError, ValueError, AttributeError):
        return []
    return [str(p.get("name")) for p in players if isinstance(p, dict) and p.get("name")]


def _water(campaign: str) -> bool | None:
    """True when the party's supplies (supplies.py) still hold water, False when they are out, None when not counted."""
    try:
        import supplies
        path = supplies.data_path(campaign)
        if not os.path.isfile(path):
            return None
        return int(supplies.load(path)["stock"].get("water", 0)) > 0
    except Exception:
        return None


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="What the day's weather does to the game; hours out in extreme cold or heat.")
    ap.add_argument("--campaign", "-c", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    p = sub.add_parser("expose", help="hours spent out in extreme cold or heat")
    p.add_argument("--hours", type=int, required=True)
    p.add_argument("--only", default="", help='only these characters, "A, B" (default: everyone at the table)')
    p.add_argument("--start", type=int, default=1, help="heat: the hour of exposure it starts from (the DC grows)")
    p.add_argument("--gear", default="", help="cold: who has cold-weather gear the sheet does not show")
    p.add_argument("--adapted", default="", help="who is adapted to this climate")
    p.add_argument("--no-water", action="store_true", help="heat: they have no drinkable water")
    p.add_argument("--roll", action="store_true", help="roll the saves here, not on the phones")
    p.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args(argv)
    try:
        import inventory
        camp = args.campaign or inventory.active_campaign()
        if not camp:
            raise WeatherError("no active campaign — pass --campaign")
        w = current(camp)
        if args.cmd == "show":
            if not w:
                print("no weather on the calendar — calendar.py weather roll / set")
                return 0
            print(f"weather: {w.get('key', '?')}" + (f", {w['temp']}" if w.get("temp") else "")
                  + f" · wind {w['wind']} · precipitation {w['precip']}" + (f" · EXTREME {w['extreme'].upper()}" if w["extreme"] else ""))
            for line in describe(w) or ["no effect on the game"]:
                print("  " + line)
            return 0
        extreme = (w or {}).get("extreme", "")
        if extreme == "heat" and not args.no_water:
            water = _water(camp)
            if water is not False:
                print("extreme heat, but the party has drinkable water" + (" (supplies)" if water else "")
                      + ": no saves (DMG p. 110) — --no-water when they have none")
                return 0
        names = [n.strip() for n in args.only.split(",") if n.strip()] or _party()
        if not names:
            raise WeatherError("nobody at the table (use --only)")
        lang = paths.campaign_language(camp)
        for line in expose(camp, names, args.hours, extreme, args.start, args.roll,
                           tuple(args.gear.split(",")), tuple(args.adapted.split(",")), lang, args.timeout):
            print(line)
    except WeatherError as e:
        print(f"weather: {e}.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
