#!/usr/bin/env python3
"""wander.py — random encounters, a table for each zone of the region.

Each zone (a forest, a stretch of road, the marshes) has its own table of
what can be met there — creatures, events, discoveries — and how often it is
checked. The places of the region map (region_map.py) belong to zones, so
the zone is the one where the party is; when calendar.py moves the clock, the
checks fall due and are rolled by themselves (DMG 2014 pp. 85-87: a d20, an
encounter on 18 or more, then the table — d12+d8 by default, so the middle
rows come up most). The players see nothing: the result is printed for the DM.

    wander.py zone "Bosco di Cenere" --places "Carbonaia, La Rocca Muta" --every 4 << 'DNDEND'
    2-4   | Un orso bruno affamato                 | brown bear
    5-8   | 2d4 lupi a caccia                       | wolf:2d4
    9-13  | Carbonai che tornano al villaggio
    14-16 | Tracce fresche di qualcosa di grosso
    17-20 | Una pattuglia della Confraternita       | cultist:1d4+1, cult fanatic
    DNDEND
    wander.py zones                        # every zone, its places, how often, paused or not
    wander.py show "Bosco di Cenere"       # its table
    wander.py here "Bosco di Cenere"       # the party is in this zone whatever the map says (here --auto: follow the map)
    wander.py check --hours 8              # time passed outside calendar.py: the checks for those hours
    wander.py roll ["Bosco di Cenere"]     # roll on the table now (no d20 check)
    wander.py pause|resume "Bosco di Cenere"
    wander.py remove "Bosco di Cenere"
    wander.py history

Rows: `range | what happens | monsters (optional)`. The ranges must cover the
die (`--die d12+d8` → 2-20, `d20`, `d100`, `d8`…). Dice in the text ("2d4
lupi") are rolled when the row comes up. The monsters column is encounter.py's
`--monsters` (counts may be dice): the result says how hard the group is for
the party at the table. `--every N` is hours between checks (DMG: every hour
in a busy area, 4-8 hours, or `--every 12` for one by day and one at night);
`--on 18` is the d20 threshold. The data lives in <campaign>/wander.json.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import random
import re
import sys
import time

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import paths  # noqa: E402

DEFAULT_DIE = "d12+d8"
DEFAULT_EVERY = 8
DEFAULT_ON = 18
HISTORY_MAX = 60
_DICE_RE = re.compile(r"\b(\d*)d(\d+)(?:\s*([+-])\s*(\d+))?\b", re.I)
_DIE_RE = re.compile(r"^\s*(\d*)d(\d+)(?:\s*\+\s*(\d*)d(\d+))?\s*$", re.I)


class WanderError(Exception):
    """A request that cannot be applied; nothing was changed."""


# ─── Dice ─────────────────────────────────────────────────────────────────────

def die_range(die: str) -> tuple:
    """'d12+d8' → (2, 20); 'd20' → (1, 20); '2d6' → (2, 12)."""
    m = _DIE_RE.match(die or "")
    if not m:
        raise WanderError(f"not a die: {die!r} (write d12+d8, d20, d100…)")
    n1, s1 = int(m.group(1) or 1), int(m.group(2))
    lo, hi = n1, n1 * s1
    if m.group(4):
        n2, s2 = int(m.group(3) or 1), int(m.group(4))
        lo, hi = lo + n2, hi + n2 * s2
    if s1 < 2 or hi > 1000:
        raise WanderError(f"not a die for a table: {die!r}")
    return lo, hi


def roll_die(die: str, rng: random.Random) -> int:
    m = _DIE_RE.match(die)
    total = sum(rng.randint(1, int(m.group(2))) for _ in range(int(m.group(1) or 1)))
    if m.group(4):
        total += sum(rng.randint(1, int(m.group(4))) for _ in range(int(m.group(3) or 1)))
    return total


def roll_text(text: str, rng: random.Random, memo: dict | None = None) -> str:
    """'2d4 lupi' → '5 lupi' (dice written in a row are rolled when it comes up).

    `memo` keeps one result per dice expression, so the same '2d4' in the text
    and in the monsters column is the same number of wolves."""
    memo = {} if memo is None else memo

    def one(m):
        n, sides = int(m.group(1) or 1), int(m.group(2))
        if sides < 2 or n > 50:
            return m.group(0)
        key = re.sub(r"\s+", "", m.group(0).lower())
        if key not in memo:
            total = sum(rng.randint(1, sides) for _ in range(n))
            if m.group(3):
                total = total + int(m.group(4)) if m.group(3) == "+" else total - int(m.group(4))
            memo[key] = max(1, total)
        return str(memo[key])
    return _DICE_RE.sub(one, text or "")


# ─── Data ─────────────────────────────────────────────────────────────────────

def _active_campaign() -> str:
    try:
        return (paths.runtime_dir() / ".campaign").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _campaign(campaign: str = "") -> str:
    camp = campaign or _active_campaign()
    if not camp:
        raise WanderError("no active campaign — pass --campaign or load one first")
    return camp


def data_path(campaign: str = "") -> str:
    return str(paths.find_campaign(_campaign(campaign)) / "wander.json")


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        data = {}
    except ValueError as e:
        raise WanderError(f"wander.json is not valid JSON ({e})")
    if not isinstance(data, dict):
        data = {}
    data.setdefault("zones", [])
    data.setdefault("pin", "")
    data.setdefault("history", [])
    return data


def save(path: str, data: dict) -> None:
    data["history"] = data["history"][-HISTORY_MAX:]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _key(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def find_zone(data: dict, name: str) -> dict:
    want = _key(name)
    hits = [z for z in data["zones"] if _key(z["name"]) == want] or [z for z in data["zones"] if want and want in _key(z["name"])]
    if len(hits) != 1:
        known = ", ".join(z["name"] for z in data["zones"]) or "none yet"
        raise WanderError(f"{'several zones match' if hits else 'no zone called'} {name!r} (zones: {known})")
    return hits[0]


def parse_rows(text: str, die: str) -> list:
    """`range | text | monsters` lines → rows; the ranges must cover the die exactly once."""
    lo, hi = die_range(die)
    rows = []
    for n, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2 or not parts[1]:
            raise WanderError(f"line {n}: write `range | what happens | monsters`")
        m = re.match(r"^(\d+)\s*(?:[-–]\s*(\d+))?$", parts[0])
        if not m:
            raise WanderError(f"line {n}: {parts[0]!r} is not a range like 5-8 or 12")
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        if a > b:
            raise WanderError(f"line {n}: the range {a}-{b} runs backwards")
        rows.append({"lo": a, "hi": b, "text": parts[1][:200], "monsters": (parts[2] if len(parts) > 2 else "")[:200]})
    if not rows:
        raise WanderError("an empty table")
    covered = []
    for r in sorted(rows, key=lambda r: r["lo"]):
        covered.extend(range(r["lo"], r["hi"] + 1))
    if covered != list(range(lo, hi + 1)):
        missing = sorted(set(range(lo, hi + 1)) - set(covered))
        twice = sorted({x for x in covered if covered.count(x) > 1})
        bits = ([f"missing {', '.join(map(str, missing[:8]))}"] if missing else []) + \
               ([f"twice {', '.join(map(str, twice[:8]))}"] if twice else []) + \
               ([f"outside {lo}-{hi}"] if any(x < lo or x > hi for x in covered) else [])
        raise WanderError(f"the ranges do not cover {die} ({lo}-{hi}) exactly: " + "; ".join(bits))
    return sorted(rows, key=lambda r: r["lo"])


def set_zone(data: dict, name: str, rows_text: str, places: list, every: float, on: int, die: str) -> dict:
    name = " ".join((name or "").split())[:80]
    if not name:
        raise WanderError("the zone needs a name")
    if not 0.5 <= every <= 72:
        raise WanderError("--every is hours between checks, 0.5 to 72")
    if not 2 <= on <= 20:
        raise WanderError("--on is the d20 threshold, 2 to 20")
    rows = parse_rows(rows_text, die)
    old = next((z for z in data["zones"] if _key(z["name"]) == _key(name)), None)
    zone = {"name": name, "places": [p for p in dict.fromkeys(" ".join(p.split()) for p in places) if p],
            "every": every, "on": on, "die": die.replace(" ", "").lower(), "rows": rows,
            "hours": old["hours"] if old else 0.0, "paused": old["paused"] if old else False}
    for other in data["zones"]:                          # a place belongs to one zone
        if other is not old:
            other["places"] = [p for p in other["places"] if _key(p) not in {_key(q) for q in zone["places"]}]
    if old:
        data["zones"][data["zones"].index(old)] = zone
    else:
        data["zones"].append(zone)
    return zone


# ─── Where the party is ───────────────────────────────────────────────────────

def party_place(campaign: str) -> str:
    try:
        with open(paths.find_campaign(campaign) / "region.json", encoding="utf-8") as f:
            return str(json.load(f).get("party") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def current_zone(data: dict, campaign: str) -> dict | None:
    if data.get("pin"):
        return next((z for z in data["zones"] if _key(z["name"]) == _key(data["pin"])), None)
    here = _key(party_place(campaign))
    return next((z for z in data["zones"] if here and here in {_key(p) for p in z["places"]}), None) if here else None


# ─── Rolling ──────────────────────────────────────────────────────────────────

def _difficulty(monsters: str, campaign: str, rng: random.Random, memo: dict | None = None) -> str:
    """'wolf:2d4, dire wolf' rolled and rated for the party at the table ('' when it cannot be)."""
    if not monsters:
        return ""
    try:
        import encounter
        pool = encounter.load_monsters()
        group = []
        for entry in monsters.split(","):
            parts = [p.strip() for p in entry.split(":") if p.strip()]
            if not parts:
                continue
            count = int(roll_text(parts[1], rng, memo)) if len(parts) > 1 else 1
            group.append((encounter.find_monster(parts[0], pool), max(1, count)))
        levels = [lv for _, lv in encounter.campaign_party(campaign)]
        m = encounter.measure(group, levels)
        lang = paths.campaign_language(campaign)
        who = ", ".join(f"{encounter.local_name(r, lang)} ×{n}" if n > 1 else encounter.local_name(r, lang) for r, n in group)
        flag = " — too much: soften it, or let them flee or talk" if m["difficulty"] in ("deadly",) or (
            m["adjusted"] > m["thresholds"][3] * 1.5) else ""
        mons = ",".join(f"{r.get('name', '')}:{n}" for r, n in group)
        return f"{who}: {m['difficulty']} for the party ({m['adjusted']} adjusted XP){flag}. encounter.py rate --monsters \"{mons}\""
    except Exception as e:
        return f"(could not rate {monsters!r}: {e})"


def roll_table(zone: dict, campaign: str = "", rng: random.Random | None = None, value: int | None = None) -> dict:
    rng = rng or random.Random()
    n = value if value is not None else roll_die(zone["die"], rng)
    row = next(r for r in zone["rows"] if r["lo"] <= n <= r["hi"])
    memo: dict = {}
    out = {"zone": zone["name"], "roll": n, "text": roll_text(row["text"], rng, memo), "monsters": row.get("monsters", "")}
    out["rating"] = _difficulty(out["monsters"], campaign, rng, memo) if campaign else ""
    return out


def pass_hours(data: dict, campaign: str, hours: float, rng: random.Random | None = None, at: str = "") -> list:
    """Time passed in the party's zone: one d20 check per --every hours. Returns the encounters (and misses)."""
    rng = rng or random.Random()
    zone = current_zone(data, campaign)
    if zone is None or zone.get("paused") or hours <= 0:
        return []
    zone["hours"] = float(zone.get("hours", 0)) + hours
    results = []
    while zone["hours"] >= zone["every"] - 1e-9:
        zone["hours"] -= zone["every"]
        d20 = rng.randint(1, 20)
        if d20 >= zone["on"]:
            hit = roll_table(zone, campaign, rng)
            hit["d20"] = d20
            results.append(hit)
            data["history"].append({"t": int(time.time()), "at": at, **hit})
        else:
            results.append({"zone": zone["name"], "d20": d20, "miss": True})
    zone["hours"] = round(zone["hours"], 4)
    return results


def format_hit(hit: dict, remind: bool = True) -> str:
    if hit.get("miss"):
        return f"check in {hit['zone']}: d20 {hit['d20']} — nothing"
    head = f"ENCOUNTER in {hit['zone']}" + (f" (d20 {hit['d20']})" if hit.get("d20") else "") + f", table {hit['roll']}: {hit['text']}"
    return (head + (f" — {hit['rating']}" if hit.get("rating") else "")
            + (" — not a fight yet: show the party the signs at a distance and ask what they do"
               " (only hidden creatures that beat the watch's passive Perception surprise them)" if remind else ""))


def tick(campaign: str, hours: float, at: str = "") -> list:
    """calendar.py moved the clock. Lines for the DM; nothing at all without a wander.json."""
    try:
        path = data_path(campaign)
    except Exception:
        return []
    if hours <= 0 or not os.path.isfile(path):
        return []
    data = load(path)
    results = pass_hours(data, campaign, hours, at=at)
    if results:
        save(path, data)
    return [format_hit(r) for r in results if not r.get("miss")] + \
           ([f"{sum(1 for r in results if r.get('miss'))} quiet check(s) in {results[0]['zone']}"]
            if any(r.get("miss") for r in results) else [])


def dm_view(data: dict, campaign: str) -> dict:
    zone = current_zone(data, campaign)
    return {"zone": zone["name"] if zone else "", "pinned": bool(data.get("pin")), "paused": bool(zone and zone.get("paused")),
            "every": zone["every"] if zone else None, "on": zone["on"] if zone else None,
            "next_in": round(zone["every"] - zone.get("hours", 0), 1) if zone else None,
            "rows": [{"range": f"{r['lo']}-{r['hi']}" if r["lo"] != r["hi"] else str(r["lo"]), "text": r["text"],
                      "monsters": r.get("monsters", "")} for r in (zone["rows"] if zone else [])],
            "zones": [z["name"] for z in data["zones"]],
            "history": [{"zone": h.get("zone"), "text": h.get("text"), "rating": h.get("rating", ""), "t": h.get("t")}
                        for h in reversed(data["history"][-6:])]}


# ─── Command line ─────────────────────────────────────────────────────────────

def main(argv: list | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    ap = argparse.ArgumentParser(description="Random encounters, a table for each zone.")
    ap.add_argument("--campaign", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("zone", help="create or replace a zone's table (rows on stdin)")
    p.add_argument("name")
    p.add_argument("--places", default="", help='places of the region map in this zone, "A, B"')
    p.add_argument("--every", type=float, default=DEFAULT_EVERY, help="hours between checks (default 8)")
    p.add_argument("--on", type=int, default=DEFAULT_ON, help="d20 threshold (default 18)")
    p.add_argument("--die", default=DEFAULT_DIE, help="the table's die (default d12+d8)")
    p.add_argument("--file", default="", help="read the rows from this file instead of stdin")
    sub.add_parser("zones")
    p = sub.add_parser("show")
    p.add_argument("name", nargs="?", default="")
    p = sub.add_parser("here", help="pin the party's zone (--auto: follow the region map)")
    p.add_argument("name", nargs="?", default="")
    p.add_argument("--auto", action="store_true")
    p = sub.add_parser("check", help="time passed outside calendar.py")
    p.add_argument("--hours", type=float, required=True)
    p = sub.add_parser("roll", help="roll on a table now")
    p.add_argument("name", nargs="?", default="")
    for cmd in ("pause", "resume", "remove"):
        p = sub.add_parser(cmd)
        p.add_argument("name")
    sub.add_parser("history")
    args = ap.parse_args(argv)

    try:
        camp = _campaign(args.campaign)
        path = data_path(camp)
        data = load(path)
        if args.cmd == "zone":
            text = open(args.file, encoding="utf-8").read() if args.file else sys.stdin.read()
            zone = set_zone(data, args.name, text, [p for p in args.places.split(",")], args.every, args.on, args.die)
            save(path, data)
            print(f"{zone['name']}: {len(zone['rows'])} rows on {zone['die']}, a d20 check every {zone['every']:g} h "
                  f"(encounter on {zone['on']}+)" + (f"; places: {', '.join(zone['places'])}" if zone["places"] else
                                                      "; no places — pin it with `here` when the party is there"))
            return 0
        if args.cmd == "zones":
            here = current_zone(data, camp)
            for z in data["zones"]:
                print(f"{'→ ' if here is z else '  '}{z['name']}: every {z['every']:g} h, {z['on']}+, {len(z['rows'])} rows"
                      + (" (paused)" if z.get("paused") else "") + (f" — {', '.join(z['places'])}" if z["places"] else ""))
            if not data["zones"]:
                print("no zones yet")
            return 0
        if args.cmd == "show":
            zone = find_zone(data, args.name) if args.name else current_zone(data, camp)
            if zone is None:
                raise WanderError("the party is in no zone — name one")
            print(f"{zone['name']} ({zone['die']}; check every {zone['every']:g} h, {zone['on']}+)")
            for r in zone["rows"]:
                rng_txt = f"{r['lo']}-{r['hi']}" if r["lo"] != r["hi"] else str(r["lo"])
                print(f"  {rng_txt:>6}  {r['text']}" + (f"  [{r['monsters']}]" if r.get("monsters") else ""))
            return 0
        if args.cmd == "here":
            if args.auto or not args.name:
                data["pin"] = ""
                zone = current_zone(data, camp)
                print("following the region map: " + (zone["name"] if zone else f"no zone for {party_place(camp) or 'the party'}"))
            else:
                data["pin"] = find_zone(data, args.name)["name"]
                print(f"the party is in {data['pin']}")
        elif args.cmd == "check":
            results = pass_hours(data, camp, args.hours)
            print("\n".join(format_hit(r) for r in results) if results else "no zone, paused, or no check due")
        elif args.cmd == "roll":
            zone = find_zone(data, args.name) if args.name else current_zone(data, camp)
            if zone is None:
                raise WanderError("the party is in no zone — name one")
            hit = roll_table(zone, camp)
            data["history"].append({"t": int(time.time()), **hit})
            print(format_hit(hit))
        elif args.cmd in ("pause", "resume"):
            zone = find_zone(data, args.name)
            zone["paused"] = args.cmd == "pause"
            print(f"{zone['name']}: " + ("paused" if zone["paused"] else "checks resume"))
        elif args.cmd == "remove":
            zone = find_zone(data, args.name)
            data["zones"].remove(zone)
            if _key(data.get("pin")) == _key(zone["name"]):
                data["pin"] = ""
            print(f"{zone['name']} removed")
        elif args.cmd == "history":
            for h in reversed(data["history"][-20:]):
                print(f"{time.strftime('%d/%m %H:%M', time.localtime(h.get('t', 0)))}  {format_hit(h, remind=False)}")
            if not data["history"]:
                print("no encounters yet")
            return 0
        save(path, data)
    except WanderError as e:
        print(f"wander: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
