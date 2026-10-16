#!/usr/bin/env python3
"""
calendar.py — in-world date, time, seasons, moon, weather and coming events

Handles time advancement for any campaign calendar — including fully custom
systems (Harvestmoon, Deepwinter, etc.). Stores the current date/time as a
structured object so arithmetic is always consistent, and keeps the display's
world clock and calendar in step: every change is pushed there.

The calendar is defined per-campaign in world.md under ## World Foundations →
Calendar. Run `calendar.py init` once to register it; all subsequent commands
use the stored definition.

Usage:
    # One-time setup (from the world.md calendar block):
    python3 calendar.py -c <campaign> init \
        --date "15 Harvestmoon 1247" \
        --time "morning" \
        --months "Frostfall,Deepwinter,Thawmonth,Seedtime,Bloomtide,Highsun,Harvestmoon,Duskfall" \
        --month-length 30 \
        --day-names "Sunday,Moonday,Ironday,Windday,Earthday,Fireday,Starday" \
        --era "AR" \
        --seasons "Winter:Frostfall,Deepwinter;Spring:Thawmonth,Seedtime;Summer:Bloomtide,Highsun;Autumn:Harvestmoon,Duskfall" \
        --moon "Selune:28" --full-moon "20 Harvestmoon 1247" \
        --climate temperate

    # Add or change seasons / moon / era / climate later (only what you pass changes):
    python3 calendar.py -c <campaign> setup --moon "Vela:29" --full-moon "16 Brumosa 1147"

    # Advance time
    python3 calendar.py -c <campaign> advance 8 hours
    python3 calendar.py -c <campaign> advance 2 days
    python3 calendar.py -c <campaign> advance 1 week

    # Rest shortcuts
    python3 calendar.py -c <campaign> rest short    # +1 hour
    python3 calendar.py -c <campaign> rest long     # +8 hours

    # Show current date/time
    python3 calendar.py -c <campaign> now

    # Set date/time directly (use after manual world.md edits)
    python3 calendar.py -c <campaign> set "22 Harvestmoon 1247" midday

    # Time of day only
    python3 calendar.py -c <campaign> time <dawn|morning|midday|afternoon|dusk|evening|night|midnight>

    # Weather: roll it for today, set it by hand, or let each new day roll its own
    python3 calendar.py -c <campaign> weather roll
    python3 calendar.py -c <campaign> weather set fog cold --text "nebbia che non si alza"
    python3 calendar.py -c <campaign> weather auto on
    python3 calendar.py -c <campaign> weather show

    # Coming events — shown on the players' calendar unless --secret
    python3 calendar.py -c <campaign> event add "Notte delle Lanterne Basse" "30 Brumosa" --yearly --note "festa di Morwen"
    python3 calendar.py -c <campaign> event add "Dieta d'Inverno" "21 Lungabuio 1147"
    python3 calendar.py -c <campaign> event add "Il rito della Confraternita" "16 Brumosa 1147" --secret
    python3 calendar.py -c <campaign> event remove "Dieta d'Inverno"
    python3 calendar.py -c <campaign> events

    # Push the clock and the calendar to the display again (e.g. after /dm:dnd load)
    python3 calendar.py -c <campaign> push
"""

import json
import os
import random
import re
import sys
import subprocess
import argparse
from paths import find_campaign as _find_campaign, display_dir as _display_dir, runtime_dir as _runtime_dir

SEND_PY = str(_display_dir() / "send.py")
PUSH_STATS = str(_display_dir() / "push_stats.py")

# Time of day labels and approximate hour ranges
TIMES_OF_DAY = [
    ("midnight",    0,  1),
    ("early morning", 1, 5),
    ("morning",     6,  10),
    ("midday",      11, 13),
    ("afternoon",   14, 17),
    ("evening",     18, 21),
    ("night",       22, 23),
]

HOURS_PER_TIME = {
    "midnight":      0,
    "early morning": 3,
    "dawn":          5,
    "morning":       8,
    "midday":        12,
    "afternoon":     15,
    "dusk":          18,
    "evening":       19,
    "night":         22,
}

# What the display's sky and clock icon understand, by hour.
_TOD_KEYS = [(4, "night"), (6, "dawn"), (10, "morning"), (13, "midday"), (17, "afternoon"),
             (18, "dusk"), (20, "evening"), (23, "night")]

WEATHER_KEYS = ("clear", "cloudy", "overcast", "fog", "rain", "storm", "snow", "windy")
TEMP_KEYS = ("freezing", "cold", "mild", "warm", "hot")
CLIMATES = ("cold", "temperate", "warm", "arid")
SEASON_KINDS = ("winter", "spring", "summer", "autumn")

# Weather by kind of season, as weights. Climate shifts them (see _roll_weather).
_WEATHER_WEIGHTS = {
    "winter": {"clear": 20, "cloudy": 20, "overcast": 20, "fog": 10, "rain": 8, "storm": 2, "snow": 15, "windy": 5},
    "spring": {"clear": 30, "cloudy": 25, "overcast": 10, "fog": 5, "rain": 20, "storm": 5, "windy": 5},
    "summer": {"clear": 50, "cloudy": 20, "overcast": 5, "fog": 2, "rain": 8, "storm": 10, "windy": 5},
    "autumn": {"clear": 20, "cloudy": 20, "overcast": 18, "fog": 15, "rain": 16, "storm": 3, "windy": 8},
}
_TEMP_WEIGHTS = {
    "winter": {"freezing": 40, "cold": 55, "mild": 5},
    "spring": {"cold": 20, "mild": 65, "warm": 15},
    "summer": {"mild": 15, "warm": 55, "hot": 30},
    "autumn": {"freezing": 5, "cold": 45, "mild": 50},
}
_SEASON_WORDS = {
    "winter": ("winter", "inverno", "hiver", "invierno"),
    "spring": ("spring", "primavera", "printemps"),
    "summer": ("summer", "estate", "été", "ete", "verano"),
    "autumn": ("autumn", "fall", "autunno", "automne", "otoño", "otono"),
}
_MOON_PHASES = ("full", "waning_gibbous", "last_quarter", "waning_crescent",
                "new", "waxing_crescent", "first_quarter", "waxing_gibbous")


class CalendarError(Exception):
    """A request that cannot be applied; the calendar is left as it was."""


def _cal_path(campaign: str) -> str:
    d = str(_find_campaign(campaign))
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "calendar.json")


def _load(campaign: str) -> dict:
    try:
        with open(_cal_path(campaign), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(campaign: str, cal: dict) -> None:
    with open(_cal_path(campaign), "w", encoding="utf-8") as f:
        json.dump(cal, f, indent=2, ensure_ascii=False)


def _month_length(cal: dict) -> int:
    return cal.get("month_length", 30)


def _month_list(cal: dict) -> list[str]:
    return cal.get("months", [])


def _day_names(cal: dict) -> list[str]:
    return cal.get("day_names", [])


def _num_months(cal: dict) -> int:
    return len(_month_list(cal)) or 12


def _month_name(cal: dict, month: int) -> str:
    months = _month_list(cal)
    return months[month - 1] if months and 1 <= month <= len(months) else f"Month {month}"


def _date_text(cal: dict, day=None, month=None, year=None) -> str:
    """'14 Brumosa 1147 E.C.' — the date alone, with the era when there is one."""
    day = cal.get("day", 1) if day is None else day
    month = cal.get("month", 1) if month is None else month
    year = cal.get("year", 1) if year is None else year
    era = (cal.get("era") or "").strip()
    return f"{day} {_month_name(cal, month)} {year}" + (f" {era}" if era else "")


def _day_name(cal: dict, day=None) -> str:
    days = _day_names(cal)
    day = cal.get("day", 1) if day is None else day
    return days[(day - 1) % len(days)] if days else ""


def _format_date(cal: dict) -> str:
    """Format current date as a human-readable string."""
    day    = cal.get("day", 1)
    month  = cal.get("month", 1)
    year   = cal.get("year", 1)
    hour   = cal.get("hour", 8)

    day_str = _day_name(cal) or f"Day {day}"

    # Map hour to time-of-day label
    tod = "night"
    for label, lo, hi in TIMES_OF_DAY:
        if lo <= hour <= hi:
            tod = label
            break

    return f"{day_str}, {day} {_month_name(cal, month)} {year} — {tod} (hour {hour})"


def _tod_key(hour: int) -> str:
    for top, key in _TOD_KEYS:
        if hour <= top:
            return key
    return "night"


def _abs_day(cal: dict, day=None, month=None, year=None) -> int:
    """Days since the calendar's year 0 — what moon phases and 'in N days' count on."""
    day = cal.get("day", 1) if day is None else day
    month = cal.get("month", 1) if month is None else month
    year = cal.get("year", 1) if year is None else year
    return (year * _num_months(cal) + (month - 1)) * _month_length(cal) + (day - 1)


def _parse_date(cal: dict, text: str) -> tuple:
    """'30 Brumosa' / '30 Brumosa 1147 E.C.' → (day, month, year or None)."""
    parts = (text or "").replace(",", " ").split()
    if len(parts) < 2 or not parts[0].isdigit():
        raise CalendarError(f"not a date: {text!r} (write e.g. '30 Brumosa 1147')")
    day = int(parts[0])
    months = _month_list(cal)
    year = None
    rest = parts[1:]
    for i, tok in enumerate(rest):
        if i > 0 and tok.lstrip("-").isdigit():
            year, rest = int(tok), rest[:i]
            break
    name = " ".join(rest).lower()
    month = next((i + 1 for i, m in enumerate(months) if m.lower() == name), 0)
    if not month and name.isdigit() and 1 <= int(name) <= _num_months(cal):
        month = int(name)
    if not month:
        raise CalendarError(f"no month called {' '.join(rest)!r} in this calendar"
                            + (f" ({', '.join(months)})" if months else ""))
    if not 1 <= day <= _month_length(cal):
        raise CalendarError(f"a month has {_month_length(cal)} days; {day} is not one of them")
    return day, month, year


def _advance_hours(cal: dict, hours: int) -> None:
    """Advance the calendar by a given number of hours."""
    cal.setdefault("hour", 8)
    cal.setdefault("day", 1)
    cal.setdefault("month", 1)
    cal.setdefault("year", 1)

    cal["hour"] += hours
    month_len = _month_length(cal)

    # Roll over hours → days
    while cal["hour"] >= 24:
        cal["hour"] -= 24
        cal["day"] += 1

    # Roll over days → months
    num_months = _num_months(cal)
    while cal["day"] > month_len:
        cal["day"] -= month_len
        cal["month"] += 1
        if cal["month"] > num_months:
            cal["month"] = 1
            cal["year"] += 1


# ─── Seasons, moon, weather, events ──────────────────────────────────────────

def _season(cal: dict, month=None) -> dict:
    month = cal.get("month", 1) if month is None else month
    for s in cal.get("seasons") or []:
        if month in (s.get("months") or []):
            return s
    return {}


def _season_kind(name: str) -> str:
    low = (name or "").lower()
    for kind, words in _SEASON_WORDS.items():
        if any(w in low for w in words):
            return kind
    return ""


def _parse_seasons(cal: dict, text: str) -> list:
    """'Winter:Frostfall,Deepwinter;Estate=summer:Spiga,Solleone' → season dicts."""
    out, taken = [], set()
    months = [m.lower() for m in _month_list(cal)]
    for chunk in (text or "").split(";"):
        if not chunk.strip():
            continue
        if ":" not in chunk:
            raise CalendarError(f"a season is written Name:Month,Month — not {chunk.strip()!r}")
        head, body = chunk.split(":", 1)
        name, _, kind = (p.strip() for p in head.partition("="))
        kind = kind.lower() or _season_kind(name)
        if kind and kind not in SEASON_KINDS:
            raise CalendarError(f"a season is one of {', '.join(SEASON_KINDS)} — not {kind!r}")
        nums = []
        for m in (x.strip() for x in body.split(",") if x.strip()):
            n = months.index(m.lower()) + 1 if m.lower() in months else int(m) if m.isdigit() else 0
            if not 1 <= n <= _num_months(cal):
                raise CalendarError(f"no month called {m!r} in this calendar")
            if n in taken:
                raise CalendarError(f"month {m!r} is in two seasons")
            taken.add(n)
            nums.append(n)
        if not name or not nums:
            raise CalendarError(f"a season needs a name and its months — not {chunk.strip()!r}")
        out.append({"name": name, "kind": kind, "months": nums})
    return out


def _moon(cal: dict, abs_day=None) -> dict:
    """{name, phase, days_to_full} for a day, or {} with no moon configured."""
    moon = cal.get("moon") or {}
    cycle = int(moon.get("cycle") or 0)
    if cycle < 2:
        return {}
    abs_day = _abs_day(cal) if abs_day is None else abs_day
    pos = (abs_day - int(moon.get("full") or 0)) % cycle
    if pos == 0:
        phase = "full"
    elif pos == cycle // 2:
        phase = "new"
    else:
        phase = _MOON_PHASES[round(pos / cycle * 8) % 8]
        if phase == "full":                          # only the night itself is the full moon
            phase = "waning_gibbous" if pos < cycle / 2 else "waxing_gibbous"
        elif phase == "new":
            phase = "waning_crescent" if pos < cycle / 2 else "waxing_crescent"
    return {"name": moon.get("name") or "", "phase": phase, "days_to_full": (-pos) % cycle}


def _pick(rng: random.Random, weights: dict) -> str:
    keys = [k for k, w in weights.items() if w > 0]
    return rng.choices(keys, weights=[weights[k] for k in keys])[0]


def _roll_weather(cal: dict, rng: random.Random) -> dict:
    """Today's weather from the season and the climate; yesterday's often lingers."""
    kind = _season(cal).get("kind") or "spring"
    climate = cal.get("climate") or "temperate"
    today = _abs_day(cal)
    last = cal.get("weather") or {}
    weights = dict(_WEATHER_WEIGHTS[kind])
    if climate == "arid":
        for wet in ("rain", "storm", "fog", "snow", "overcast"):
            weights[wet] = weights.get(wet, 0) * 0.2
        weights["clear"] = weights.get("clear", 0) * 2
    if last.get("key") in WEATHER_KEYS and last.get("day") == today - 1 and rng.random() < 0.5:
        key = last["key"]
    else:
        key = _pick(rng, weights)
    temp = TEMP_KEYS.index(_pick(rng, _TEMP_WEIGHTS[kind]))
    temp += {"cold": -1, "warm": 1, "arid": 1}.get(climate, 0)
    temp = TEMP_KEYS[max(0, min(len(TEMP_KEYS) - 1, temp))]
    if key == "snow" and temp not in ("freezing", "cold"):
        key = "rain"
    elif key == "rain" and temp == "freezing":
        key = "snow"
    return {"key": key, "temp": temp, "text": "", "day": today}


def _weather_text(weather: dict) -> str:
    if not weather or not weather.get("key"):
        return ""
    return weather.get("text") or ", ".join(x for x in (weather.get("key"), weather.get("temp")) if x)


def _event_date(cal: dict, e: dict) -> tuple:
    """(day, month, year or None) of an event in either stored format."""
    if e.get("day") and e.get("month"):
        return int(e["day"]), int(e["month"]), e.get("year")
    return _parse_date(cal, e.get("date", ""))


def _events(cal: dict, secret: bool = True) -> list:
    """Events with the days from today to their next occurrence, soonest first."""
    today = _abs_day(cal)
    year = cal.get("year", 1)
    out = []
    for e in cal.get("events") or []:
        if e.get("secret") and not secret:
            continue
        try:
            day, month, ev_year = _event_date(cal, e)
        except CalendarError:
            continue
        if ev_year is None:                          # every year: this year's, or the next one
            when = _abs_day(cal, day, month, year)
            if when < today:
                when = _abs_day(cal, day, month, year + 1)
        else:
            when = _abs_day(cal, day, month, ev_year)
        out.append({"name": e.get("name", "?"), "day": day, "month": month, "year": ev_year,
                    "note": e.get("note", ""), "secret": bool(e.get("secret")), "in_days": when - today})
    return sorted(out, key=lambda e: (e["in_days"] < 0, abs(e["in_days"]) if e["in_days"] < 0 else e["in_days"]))


def world_time(cal: dict) -> dict:
    """What the display's clock, sky and calendar need — keys, not sentences."""
    weather = cal.get("weather") or {}
    moon = cal.get("moon") or {}
    payload = {
        "date": _date_text(cal),
        "day_name": _day_name(cal),
        "time": _tod_key(cal.get("hour", 8)),
        "hour": cal.get("hour", 8),
        "season": _season(cal).get("name", ""),
        "weather": ", ".join(x for x in (weather.get("key"), weather.get("temp")) if x),
        "weather_key": weather.get("key", ""),
        "temp_key": weather.get("temp", ""),
        "weather_text": weather.get("text", ""),
        "calendar": {
            "day": cal.get("day", 1), "month": cal.get("month", 1), "year": cal.get("year", 1),
            "months": _month_list(cal), "month_length": _month_length(cal),
            "day_names": _day_names(cal), "era": cal.get("era", ""),
            "seasons": [{"name": s.get("name", ""), "months": s.get("months", [])}
                        for s in cal.get("seasons") or []],
            "events": [{k: e[k] for k in ("name", "day", "month", "year", "note", "in_days")}
                       for e in _events(cal, secret=False) if e["in_days"] >= 0],
        },
    }
    if _moon(cal):
        payload["moon"] = _moon(cal)
        payload["calendar"]["moon"] = {"name": moon.get("name", ""), "cycle": int(moon["cycle"]),
                                       "full": int(moon.get("full") or 0)}
    return payload


def _is_active(campaign: str) -> bool:
    """True unless the display is showing another campaign."""
    try:
        active = (_runtime_dir() / ".campaign").read_text(encoding="utf-8").strip()
    except OSError:
        return True
    return not active or active == campaign


def _push_world(campaign: str, cal: dict) -> None:
    """Put the clock and the calendar on the display. Silent when it is not running."""
    if not _is_active(campaign):
        return
    try:
        subprocess.run([sys.executable, PUSH_STATS, "--world-time", json.dumps(world_time(cal), ensure_ascii=False)],
                       capture_output=True, timeout=5)
    except Exception:
        pass


def _send_date(cal: dict) -> None:
    """Push the current date announcement to the display."""
    msg = f"📅 {_format_date(cal)}"
    try:
        # run(), not Popen(): Popen takes neither capture_output nor timeout,
        # and the TypeError it raised was swallowed below, so nothing was sent.
        subprocess.run([sys.executable, SEND_PY, "--dice"],
                       input=msg.encode("utf-8"), capture_output=True, timeout=3)
    except Exception:
        pass


def _after_change(campaign: str, cal: dict, before_day=None, rng=None) -> None:
    """Common tail of every command that moves time: a new day may bring new
    weather and reach an event; then save, announce, and update the display."""
    today = _abs_day(cal)
    if before_day is not None and today != before_day:
        weather = cal.get("weather") or {}
        if weather.get("auto"):
            cal["weather"] = dict(_roll_weather(cal, rng or random.Random()), auto=True)
            print(f"  Weather today: {_weather_text(cal['weather'])}")
        elif weather.get("key"):
            print(f"  Weather is still set to: {_weather_text(weather)} — a new day; "
                  "`weather roll` or `weather set` if it changed")
        for e in _events(cal):
            years = [e["year"]] if e["year"] is not None else [cal.get("year", 1) - 1, cal.get("year", 1)]
            for year in years:
                when = _abs_day(cal, e["day"], e["month"], year)
                tag = "  [secret]" if e["secret"] else ""
                if when == today:
                    print(f"  ★ TODAY: {e['name']}" + (f" — {e['note']}" if e["note"] else "") + tag)
                elif before_day < when < today:
                    print(f"  ★ PASSED while time advanced: {e['name']} "
                          f"({_date_text(cal, e['day'], e['month'], year)}){tag}")
        moon = _moon(cal)
        if moon and moon["phase"] in ("full", "new"):
            print(f"  ☾ {moon['name'] or 'The moon'} is {moon['phase']} tonight")
    _save(campaign, cal)
    _send_date(cal)
    _push_world(campaign, cal)


# ─── Commands ────────────────────────────────────────────────────────────────

def _apply_setup(cal: dict, args) -> None:
    """The optional parts of a calendar, from init or setup. Only what is given changes."""
    if getattr(args, "era", None) is not None:
        cal["era"] = args.era.strip()
    if getattr(args, "climate", None):
        cal["climate"] = args.climate
    if getattr(args, "seasons", None):
        cal["seasons"] = _parse_seasons(cal, args.seasons)
    if getattr(args, "moon", None):
        name, _, cycle = args.moon.rpartition(":")
        if not cycle.strip().isdigit() or int(cycle) < 2:
            raise CalendarError(f"a moon is written Name:DAYS — not {args.moon!r}")
        cal["moon"] = {"name": name.strip(), "cycle": int(cycle), "full": (cal.get("moon") or {}).get("full", 0)}
    if getattr(args, "full_moon", None):
        if not cal.get("moon"):
            raise CalendarError("--full-moon needs a moon: pass --moon Name:DAYS too")
        day, month, year = _parse_date(cal, args.full_moon)
        cal["moon"]["full"] = _abs_day(cal, day, month, cal.get("year", 1) if year is None else year)


def cmd_init(campaign: str, args) -> None:
    cal: dict = {}

    # Parse the starting date string: "15 Harvestmoon 1247"
    date_str = args.date
    try:
        parts = date_str.split()
        if len(parts) >= 3:
            month_name = parts[1]
            cal["day"]  = int(parts[0])
            cal["year"] = int(parts[2])
        elif len(parts) == 2:
            month_name = parts[1]
            cal["day"]  = int(parts[0])
            cal["year"] = 1
        else:
            cal["day"]  = 1
            month_name  = date_str
            cal["year"] = 1

        # Figure out month index
        months = [m.strip() for m in args.months.split(",") if m.strip()] if args.months else []
        cal["months"] = months
        cal["month"] = 1
        for i, m in enumerate(months):
            if m.lower() == month_name.lower():
                cal["month"] = i + 1
                break
    except (ValueError, IndexError):
        cal.update({"day": 1, "month": 1, "year": 1, "months": []})

    cal["hour"]        = HOURS_PER_TIME.get(args.time or "morning", 8)
    cal["month_length"] = int(args.month_length) if args.month_length else 30
    cal["day_names"]   = [d.strip() for d in args.day_names.split(",") if d.strip()] if args.day_names else []
    cal["events"]      = []
    _apply_setup(cal, args)

    _save(campaign, cal)
    print(f"Calendar initialised: {_format_date(cal)}")
    _push_world(campaign, cal)


def _need(campaign: str) -> dict:
    cal = _load(campaign)
    if not cal:
        print("Calendar not initialised. Run `calendar.py -c <campaign> init` first.")
        sys.exit(1)
    return cal


def cmd_setup(campaign: str, args) -> None:
    cal = _need(campaign)
    _apply_setup(cal, args)
    _save(campaign, cal)
    season, moon = _season(cal), _moon(cal)
    print(f"  {_format_date(cal)}")
    if season:
        print(f"  Season: {season['name']}" + (f" ({season['kind']})" if season.get("kind") else
                                              " — kind unknown: weather rolls as spring; name it Name=winter:…"))
    if moon:
        print(f"  Moon: {moon['name']} — {moon['phase'].replace('_', ' ')}, full in {moon['days_to_full']} day(s)")
    _push_world(campaign, cal)


def cmd_advance(campaign: str, amount: int, unit: str) -> None:
    cal = _need(campaign)
    before = _abs_day(cal)

    hours = {"hour": 1, "hours": 1, "day": 24, "days": 24, "week": 168, "weeks": 168}.get(unit, 1)
    total_hours = amount * hours
    _advance_hours(cal, total_hours)

    label = f"+{amount} {unit}"
    print(f"  {label} → {_format_date(cal)}")
    _after_change(campaign, cal, before)
    _spend_supplies(campaign, total_hours, _abs_day(cal) - before)


def cmd_rest(campaign: str, rest_type: str) -> None:
    cal = _need(campaign)
    before = _abs_day(cal)

    if rest_type == "short":
        _advance_hours(cal, 1)
        label = "Short rest (+1 hour)"
    else:
        _advance_hours(cal, 8)
        label = "Long rest (+8 hours)"

    print(f"  {label} → {_format_date(cal)}")
    _after_change(campaign, cal, before)
    _spend_supplies(campaign, 1 if rest_type == "short" else 8, _abs_day(cal) - before)


def _spend_supplies(campaign: str, hours: float, days_crossed: int) -> None:
    """Lit torches and lanterns burn; on the march each new day eats (supplies.py)."""
    try:
        import supplies
        for line in supplies.tick(campaign, int(round(hours * 60)), max(0, days_crossed)):
            print(("  ⚠ Supplies: " if line.split(":")[0].isupper() else "  Supplies: ") + line)
    except Exception:
        pass   # supplies are a courtesy: the time has moved anyway


def cmd_now(campaign: str) -> None:
    cal = _load(campaign)
    if not cal:
        print("Calendar not initialised. Run `calendar.py -c <campaign> init` first.")
        return
    print(_format_date(cal))
    season, moon, weather = _season(cal), _moon(cal), cal.get("weather") or {}
    if season:
        print(f"  Season: {season['name']}")
    if weather.get("key"):
        print(f"  Weather: {_weather_text(weather)}" + ("  (rolled each new day)" if weather.get("auto") else ""))
    if moon:
        print(f"  Moon: {moon['name']} — {moon['phase'].replace('_', ' ')}, full in {moon['days_to_full']} day(s)")
    for e in _events(cal)[:3]:
        if e["in_days"] >= 0:
            print(f"  Coming: {e['name']} — " + ("today" if e["in_days"] == 0 else f"in {e['in_days']} day(s)"))


def cmd_set(campaign: str, date_str: str, time_str: str) -> None:
    cal = _load(campaign)
    if not cal:
        cal = {"months": [], "day_names": [], "month_length": 30, "events": []}
    before = _abs_day(cal)

    months = cal.get("months", [])
    try:
        parts = date_str.split()
        if len(parts) >= 3:
            month_name = parts[1]
            cal["day"]  = int(parts[0])
            cal["year"] = int(parts[2])
            cal["month"] = 1
            for i, m in enumerate(months):
                if m.lower() == month_name.lower():
                    cal["month"] = i + 1
                    break
        elif len(parts) == 1:
            cal["day"] = int(parts[0])
    except (ValueError, IndexError):
        pass

    if time_str:
        cal["hour"] = HOURS_PER_TIME.get(time_str.lower(), cal.get("hour", 8))

    print(f"  Date set: {_format_date(cal)}")
    _after_change(campaign, cal, before)


def cmd_time(campaign: str, time_str: str) -> None:
    cal = _load(campaign)
    if not cal:
        print("Calendar not initialised.")
        sys.exit(1)
    cal["hour"] = HOURS_PER_TIME.get(time_str.lower(), cal.get("hour", 8))
    print(f"  Time set: {_format_date(cal)}")
    _after_change(campaign, cal)


def cmd_weather(campaign: str, args) -> None:
    cal = _need(campaign)
    weather = cal.get("weather") or {}
    auto = bool(weather.get("auto"))
    if args.action == "show":
        print(f"  Weather: {_weather_text(weather) or 'not set'}" + ("  (rolled each new day)" if auto else ""))
        return
    if args.action == "roll":
        if not _season(cal).get("kind"):
            print("  (no season of a known kind for this month — rolling as spring; "
                  "see `setup --seasons Name=winter:Month,…`)")
        rng = random.Random(args.seed) if args.seed is not None else random.Random()
        cal["weather"] = dict(_roll_weather(cal, rng), auto=auto)
    elif args.action == "set":
        key = (args.values[0] if args.values else "").lower()
        temp = (args.values[1] if len(args.values) > 1 else weather.get("temp", "")).lower()
        if key not in WEATHER_KEYS:
            raise CalendarError(f"weather is one of {', '.join(WEATHER_KEYS)} — not {key!r} "
                                "(the words the players read go in --text)")
        if temp and temp not in TEMP_KEYS:
            raise CalendarError(f"temperature is one of {', '.join(TEMP_KEYS)} — not {temp!r}")
        cal["weather"] = {"key": key, "temp": temp, "text": (args.text or "").strip(),
                          "day": _abs_day(cal), "auto": auto}
    elif args.action == "auto":
        want = (args.values[0] if args.values else "").lower()
        if want not in ("on", "off"):
            raise CalendarError("weather auto on | off")
        weather["auto"] = want == "on"
        cal["weather"] = weather
        if weather["auto"] and not weather.get("key"):
            cal["weather"] = dict(_roll_weather(cal, random.Random()), auto=True)
    _save(campaign, cal)
    print(f"  Weather: {_weather_text(cal['weather']) or 'not set'}"
          + ("  (rolled each new day)" if cal["weather"].get("auto") else ""))
    _push_world(campaign, cal)


def cmd_event(campaign: str, args) -> None:
    cal = _need(campaign)
    events = cal.setdefault("events", [])
    name = " ".join((args.name or "").split())
    if not name:
        raise CalendarError("an event needs a name")
    same = [e for e in events if e.get("name", "").lower() == name.lower()]
    if args.action == "add":
        day, month, year = _parse_date(cal, args.date or "")
        if year is None and not args.yearly:
            year = cal.get("year", 1)
            if _abs_day(cal, day, month, year) < _abs_day(cal):
                year += 1                            # "30 Brumosa" said after the 30th means next year's
        entry = {"name": name, "day": day, "month": month, "year": None if args.yearly else year}
        if args.note:
            entry["note"] = args.note.strip()
        if args.secret:
            entry["secret"] = True
        cal["events"] = [e for e in events if e not in same] + [entry]
        when = "every year" if args.yearly else str(entry["year"])
        print(f"  + {name} — {day} {_month_name(cal, month)}, {when}" + ("  [secret]" if args.secret else ""))
    else:
        if not same:
            raise CalendarError(f"no event called {name!r}")
        cal["events"] = [e for e in events if e not in same]
        print(f"  - {name}")
    _save(campaign, cal)
    _push_world(campaign, cal)


def cmd_events(campaign: str) -> None:
    cal = _load(campaign)
    events = _events(cal) if cal else []
    if not events:
        print("  No upcoming events registered.")
        print('  Add one: calendar.py -c <campaign> event add "Festival of Stars" "1 Bloomtide 1248"')
    else:
        print("Upcoming events:")
        for e in events:
            date = f"{e['day']} {_month_name(cal, e['month'])}" + (f" {e['year']}" if e["year"] is not None else " (yearly)")
            when = "today" if e["in_days"] == 0 else f"in {e['in_days']} day(s)" if e["in_days"] > 0 \
                else f"{-e['in_days']} day(s) ago"
            print(f"  {date} — {e['name']} ({when})" + (f" — {e['note']}" if e["note"] else "")
                  + ("  [secret]" if e["secret"] else ""))


# ─── Main ────────────────────────────────────────────────────────────────────

def _setup_options(p) -> None:
    p.add_argument("--era", default=None, help="Written after the year, e.g. 'E.C.'")
    p.add_argument("--seasons", default="",
                   help="'Winter:Frostfall,Deepwinter;Spring:Thawmonth' — add =winter|spring|summer|autumn "
                        "after a name the script cannot recognise")
    p.add_argument("--moon", default="", help="Name:DAYS of the cycle, e.g. 'Selune:28'")
    p.add_argument("--full-moon", default="", help="The date of one full moon, e.g. '16 Brumosa 1147'")
    p.add_argument("--climate", default="", choices=("",) + CLIMATES)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="In-world date and time manager.")
    p.add_argument("-c", "--campaign", required=True, metavar="NAME")
    sub = p.add_subparsers(dest="cmd")

    ini = sub.add_parser("init", help="Initialise campaign calendar (run once)")
    ini.add_argument("--date",         default="1 Month 1",
                     help="Starting date, e.g. '15 Harvestmoon 1247'")
    ini.add_argument("--time",         default="morning",
                     help="Starting time of day (morning/midday/afternoon/evening/night/midnight)")
    ini.add_argument("--months",       default="",
                     help="Comma-separated month names in order")
    ini.add_argument("--month-length", default="30",
                     help="Days per month (uniform, default 30)")
    ini.add_argument("--day-names",    default="",
                     help="Comma-separated day-of-week names")
    _setup_options(ini)

    _setup_options(sub.add_parser("setup", help="Add or change era, seasons, moon, climate"))

    adv = sub.add_parser("advance", help="Advance time by amount")
    adv.add_argument("amount", type=int)
    adv.add_argument("unit",
                     choices=["hour","hours","day","days","week","weeks"])

    rst = sub.add_parser("rest", help="Advance time for a short or long rest")
    rst.add_argument("type", choices=["short","long"])

    sub.add_parser("now", help="Print current date/time")

    st = sub.add_parser("set", help="Set the current date/time directly")
    st.add_argument("date",      help="Date string, e.g. '22 Harvestmoon 1247'")
    st.add_argument("time", nargs="?", default="",
                    help="Time of day (optional)")

    tm = sub.add_parser("time", help="Set time of day without changing the date")
    tm.add_argument("tod",
                    choices=list(HOURS_PER_TIME.keys()),
                    help="Time of day label")

    wx = sub.add_parser("weather", help="Roll, set or show today's weather")
    wx.add_argument("action", choices=["roll", "set", "auto", "show"])
    wx.add_argument("values", nargs="*", help="set: KEY [TEMP] · auto: on|off")
    wx.add_argument("--text", default="", help="set: the words the players read instead of the plain key")
    wx.add_argument("--seed", default=None, help="roll: make it repeatable")

    ev = sub.add_parser("event", help="Add or remove a coming event")
    ev.add_argument("action", choices=["add", "remove"])
    ev.add_argument("name")
    ev.add_argument("date", nargs="?", default="", help="add: '30 Brumosa' or '30 Brumosa 1147'")
    ev.add_argument("--yearly", action="store_true", help="it comes back every year")
    ev.add_argument("--note", default="")
    ev.add_argument("--secret", action="store_true", help="the DM's own: never shown to the players")

    sub.add_parser("events", help="List upcoming calendar events")
    sub.add_parser("push", help="Send the clock and the calendar to the display again")

    args = p.parse_args(argv)

    try:
        if   args.cmd == "init":    cmd_init(args.campaign, args)
        elif args.cmd == "setup":   cmd_setup(args.campaign, args)
        elif args.cmd == "advance": cmd_advance(args.campaign, args.amount, args.unit)
        elif args.cmd == "rest":    cmd_rest(args.campaign, args.type)
        elif args.cmd == "now":     cmd_now(args.campaign)
        elif args.cmd == "set":     cmd_set(args.campaign, args.date, args.time)
        elif args.cmd == "time":    cmd_time(args.campaign, args.tod)
        elif args.cmd == "weather": cmd_weather(args.campaign, args)
        elif args.cmd == "event":   cmd_event(args.campaign, args)
        elif args.cmd == "events":  cmd_events(args.campaign)
        elif args.cmd == "push":
            cal = _need(args.campaign)
            _push_world(args.campaign, cal)
            print(f"  {_format_date(cal)}")
        else:                       p.print_help()
    except CalendarError as e:
        print(f"calendar: {e}. Nothing was changed.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
