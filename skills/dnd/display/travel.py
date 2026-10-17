#!/usr/bin/env python3
"""travel.py — the pace of the march, the roles on the road, and getting lost.

A journey played hour by hour (PHB 2014 pp. 181-183, DMG 2014 p. 111): the DM
sets the pace — fast, normal or slow — and every player picks, from the phone,
what their character does on the road: guide (finds the way), lookout (keeps
watch), mapper (draws the map, no check) or forager (looks for food and
water). Whoever guides, maps or forages is not watching: only the others
count for noticing threats. A fast pace gives -5 to their passive
Perception; a slow one lets the party move stealthily.

When the party leaves the roads, the guide makes a Wisdom (Survival) check on
the phone (DC 5 to 15 by terrain, +5 at a slow pace, -5 at a fast one,
advantage with an accurate map or the sun and stars to steer by). On a failure
the party is lost and does not know it: the DM sees it, the players do not,
until the DM says they realise it — then their region map shows them lost, and
the guide may try again after 1d6 hours spent finding the way.

    travel.py start --pace normal --to "Cripta di Vessar" --terrain forest
    travel.py pace fast                       # veloce | normale | lento too
    travel.py role "Lyra" guide               # the DM may set a role too (guida, vedetta, cartografo, foraggiatore, nessuno)
    travel.py navigate                        # the guide's Survival check, DC from the terrain given at start
    travel.py navigate --terrain mountain --map
    travel.py navigate --dc 12 --total 9      # the roll is already made (no display, or rolled behind the screen)
    travel.py reveal                          # the players realise they are lost: their map shows it
    travel.py found --at "Carbonaia"          # back on the way (the guide succeeded, or the DM says so)
    travel.py forage --dc 15                  # each forager's Survival: food and water into supplies.py
    travel.py watch                           # the lookouts' passive Perception, the pace counted
    travel.py show
    travel.py stop

Terrains: grassland/prairie/fields (DC 5), arctic/desert/hills/sea-clear (DC
10), forest/jungle/swamp/mountain/sea-overcast (DC 15); Italian words too.
Everything lives in <campaign>/travel.json. The choices reach the DM as a
`[[MARCH: …]]` line from check_input.py once everybody has picked a role.
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
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

# PHB 2014 p. 182: distance by pace, and what it changes.
PACES = {"fast": {"kmh": 6, "km_day": 48, "perception": -5, "stealth": False, "forage": False, "nav": -5},
         "normal": {"kmh": 4.5, "km_day": 36, "perception": 0, "stealth": False, "forage": True, "nav": 0},
         "slow": {"kmh": 3, "km_day": 24, "perception": 0, "stealth": True, "forage": True, "nav": 5}}
PACE_ALIASES = {"veloce": "fast", "rapido": "fast", "normale": "normal", "lento": "slow", "prudente": "slow"}
ROLES = ("guide", "lookout", "mapper", "forager")
ROLE_ALIASES = {"guida": "guide", "navigatore": "guide", "navigator": "guide", "navigate": "guide",
                "vedetta": "lookout", "sentinella": "lookout", "watch": "lookout", "guardia": "lookout",
                "cartografo": "mapper", "mappa": "mapper", "map": "mapper", "cartographer": "mapper",
                "foraggiatore": "forager", "foraggiare": "forager", "forage": "forager",
                "nessuno": "none", "niente": "none", "nothing": "none", "": "none"}
# DMG 2014 p. 111: the navigator's DC by terrain.
TERRAIN_DC = {"grassland": 5, "prairie": 5, "meadow": 5, "fields": 5, "farmland": 5,
              "arctic": 10, "desert": 10, "hills": 10, "sea-clear": 10,
              "forest": 15, "jungle": 15, "swamp": 15, "mountain": 15, "mountains": 15, "sea-overcast": 15}
TERRAIN_ALIASES = {"prateria": "prairie", "pascoli": "meadow", "pascolo": "meadow", "campi": "fields",
                   "campagna": "farmland", "artico": "arctic", "deserto": "desert", "colline": "hills",
                   "collina": "hills", "mare": "sea-clear", "mare-sereno": "sea-clear", "foresta": "forest",
                   "bosco": "forest", "giungla": "jungle", "palude": "swamp", "acquitrino": "swamp",
                   "montagna": "mountain", "monti": "mountain", "mare-coperto": "sea-overcast"}
# DMG 2014 p. 111: what a successful forager finds, per success.
FORAGE_DC = {"abundant": 10, "limited": 15, "scarce": 20, "abbondante": 10, "limitate": 15, "scarse": 20}
HISTORY_KEPT = 20


class TravelError(Exception):
    """A request that cannot be applied; nothing was changed."""


# ─── Data ─────────────────────────────────────────────────────────────────────

def _active_campaign() -> str:
    import media
    return media.active_campaign() or ""


def _campaign(campaign: str = "") -> str:
    import media
    camp = media.safe_campaign(campaign) if campaign else media.active_campaign()
    if not camp:
        raise TravelError("no active campaign — pass --campaign or load one first")
    return camp


def data_path(campaign: str = "") -> str:
    import media
    return str(media.find_campaign(_campaign(campaign)) / "travel.json")


def blank() -> dict:
    return {"on": False, "pace": "normal", "roles": {}, "players": [], "to": "", "terrain": "", "lost": None,
            "told": False, "history": []}


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        return blank()
    except ValueError as e:
        raise TravelError(f"travel.json is not valid JSON ({e})")
    base = blank()
    if isinstance(data, dict):
        base.update({k: data[k] for k in base if k in data})
    if base["pace"] not in PACES:
        base["pace"] = "normal"
    if not isinstance(base["roles"], dict):
        base["roles"] = {}
    return base


def save(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _key(name: str) -> str:
    return "".join(ch for ch in (name or "").casefold() if ch.isalnum())


def _clean(text, limit: int) -> str:
    return " ".join(re.sub(r"[`\\$<>]", "", str(text or "")).split())[:limit]


def pace_of(word: str) -> str:
    w = (word or "").strip().lower()
    w = PACE_ALIASES.get(w, w)
    if w not in PACES:
        raise TravelError(f"the pace is fast, normal or slow — not {word!r}")
    return w


def role_of(word: str) -> str:
    w = (word or "").strip().lower()
    w = ROLE_ALIASES.get(w, w)
    if w != "none" and w not in ROLES:
        raise TravelError(f"a role is one of {', '.join(ROLES)} or none — not {word!r}")
    return w


def terrain_dc(word: str) -> int:
    w = (word or "").strip().lower().replace(" ", "-")
    w = TERRAIN_ALIASES.get(w, w)
    if w not in TERRAIN_DC:
        raise TravelError(f"unknown terrain {word!r} — one of {', '.join(sorted(TERRAIN_DC))}, or give --dc")
    return TERRAIN_DC[w]


def _log(data: dict, text: str) -> None:
    data["history"] = (data.get("history") or [])[-(HISTORY_KEPT - 1):] + [text]


def start(data: dict, players: list, pace: str = "normal", to: str = "", terrain: str = "") -> dict:
    if not players:
        raise TravelError("nobody at the table (use --only)")
    if terrain:
        terrain_dc(terrain)
    keep = {n: r for n, r in (data.get("roles") or {}).items() if any(_key(n) == _key(p) for p in players)}
    data.update({"on": True, "pace": pace_of(pace), "players": list(players), "roles": keep,
                 "to": _clean(to, 80), "terrain": _clean(terrain, 30), "lost": None, "told": False})
    _log(data, "set out" + (f" towards {data['to']}" if data["to"] else ""))
    return data


def set_role(data: dict, character: str, role: str) -> tuple:
    """(the character's name as the party writes it, everybody has a role now)."""
    if not data.get("on"):
        raise TravelError("the party is not on the road — travel.py start first")
    name = next((p for p in data["players"] if _key(p) == _key(character)), None)
    if not name:
        raise TravelError(f"{character!r} is not travelling with the party")
    role = role_of(role)
    if role == "guide":
        other = next((n for n, r in data["roles"].items() if r == "guide" and _key(n) != _key(name)), None)
        if other:
            raise TravelError(f"{other} is already the guide")
    for n in [n for n in data["roles"] if _key(n) == _key(name)]:
        del data["roles"][n]
    if role != "none":
        data["roles"][name] = role
    return name, all(any(_key(n) == _key(p) for n in data["roles"]) for p in data["players"])


def with_role(data: dict, role: str) -> list:
    return [n for n, r in (data.get("roles") or {}).items() if r == role]


def watch(data: dict, passives: dict, weather_mod: int = 0) -> dict:
    """The party's watch: the best passive Perception of those keeping watch, the pace counted (PHB p. 183).

    Lookouts keep watch, and so does whoever has picked no other task;
    whoever guides, maps or forages is busy and does not count. `weather_mod`
    is -5 when the weather gives disadvantage on Perception (wind, rain, fog —
    weather.py); it does not add to the fast pace's -5, both being the same
    disadvantage's worth (PHB p. 175)."""
    busy = {_key(n) for n, r in (data.get("roles") or {}).items() if r in ("guide", "mapper", "forager")}
    lookouts = [n for n in data.get("players") or [] if _key(n) not in busy]
    pace = PACES[data.get("pace", "normal")]["perception"]
    mod = min(pace, weather_mod) if pace and weather_mod else pace + weather_mod
    scores = {n: int(passives[n]) + mod for n in lookouts if passives.get(n) is not None}
    best = max(scores.items(), key=lambda kv: kv[1]) if scores else None
    return {"lookouts": scores, "best": best[1] if best else None, "by": best[0] if best else "", "mod": mod,
            "weather": weather_mod}


def ready_line(data: dict) -> str:
    roles = data.get("roles") or {}
    parts = [f"{n}: {roles.get(n, 'no role')}" for n in data["players"]]
    return f"[[MARCH: everyone has a role ({data['pace']} pace) — " + "; ".join(parts) + "]]"


def navigation_dc(data: dict, dc=None, terrain: str = "") -> int:
    if dc is not None:
        if not 1 <= int(dc) <= 40:
            raise TravelError("--dc between 1 and 40")
        return int(dc)
    t = terrain or data.get("terrain") or ""
    if not t:
        raise TravelError("say the terrain (--terrain forest) or the DC (--dc 15)")
    return terrain_dc(t)


def get_lost(data: dict, place: str, now_h, rng: random.Random) -> int:
    """The guide failed: the party goes the wrong way without knowing it. Hours before the next try."""
    hours = rng.randint(1, 6)
    data["lost"] = {"from": place, "known": False, "since_h": now_h, "retry_hours": hours,
                    "retry_h": None if now_h is None else now_h + hours}
    _log(data, f"lost near {place or 'the road'}")
    return hours


def found(data: dict) -> None:
    data["lost"] = None
    _log(data, "back on the way")


def forage_yield(wis_mod: int, rng: random.Random) -> dict:
    """A forager who made it: (1d6 + WIS) × 0.5 kg of food and (1d6 + WIS) × 4 l of water (DMG p. 111).

    Half a kilo of food is one person-day of rations and 4 litres one of water."""
    food = max(0, rng.randint(1, 6) + wis_mod)
    water = max(0, rng.randint(1, 6) + wis_mod)
    return {"rations": food, "water": water, "kg": food * 0.5, "litres": water * 4}


def public_view(data: dict, me: str = "", dm: bool = False, passives: dict | None = None, weather_mod: int = 0) -> dict | None:
    """What a screen sees. The players learn they are lost only when the DM reveals it."""
    if not data.get("on"):
        return None
    pace = data.get("pace", "normal")
    lost = data.get("lost") or None
    view = {"pace": pace, "km_day": PACES[pace]["km_day"], "kmh": PACES[pace]["kmh"],
            "perception": PACES[pace]["perception"], "stealth": PACES[pace]["stealth"], "forage": PACES[pace]["forage"],
            "players": list(data["players"]), "roles": dict(data["roles"]), "to": data.get("to", ""),
            "roles_list": list(ROLES), "lost": bool(lost and (lost.get("known") or dm)),
            "mine": next((r for n, r in data["roles"].items() if me and _key(n) == _key(me)), None),
            "asked": bool(me) and any(_key(p) == _key(me) for p in data["players"])}
    if passives is not None:
        view["watch"] = watch(data, passives, weather_mod)
    if dm:
        view.update({"terrain": data.get("terrain", ""), "lost_detail": lost, "history": list(data.get("history") or [])})
    return view


def describe(data: dict, passives: dict | None = None, weather_mod: int = 0) -> str:
    if not data.get("on"):
        return "the party is not on the road"
    pace = data["pace"]
    p = PACES[pace]
    lines = [f"On the road, {pace} pace ({p['kmh']:g} km/h, {p['km_day']} km a day)"
             + (f" towards {data['to']}" if data.get("to") else "") + (f", terrain {data['terrain']}" if data.get("terrain") else "")]
    if p["perception"]:
        lines.append(f"  fast pace: {p['perception']} to the lookouts' passive Perception")
    if p["stealth"]:
        lines.append("  slow pace: the party can move stealthily")
    if not p["forage"]:
        lines.append("  fast pace: nobody can forage")
    for n in data["players"]:
        lines.append(f"  {n}: {data['roles'].get(n, 'no role yet')}")
    if passives is not None:
        w = watch(data, passives, weather_mod)
        lines.append("  watch: " + (f"best passive Perception {w['best']} ({w['by']})" if w["by"] else "NOBODY is keeping watch")
                     + (" — the weather gives disadvantage: -5 counted" if weather_mod and w["by"] else ""))
    lost = data.get("lost")
    if lost:
        lines.append(f"  LOST near {lost.get('from') or 'the road'} — the players "
                     + ("know it" if lost.get("known") else "do NOT know it (travel.py reveal when they realise it)")
                     + f"; the guide may try again after {lost.get('retry_hours')} h of searching")
    return "\n".join(lines)


# ─── The region map (region_map.py) ───────────────────────────────────────────

def _region(camp: str, lost: dict | None, here: str = "") -> str:
    """Mark the party lost (or found) on the region map and redraw it. Returns the place it was near."""
    try:
        import region_map
        path = region_map.data_path(camp)
        if not os.path.isfile(path):
            return ""
        data = region_map.load(path)
        if here:
            region_map.set_here(data, here)
        near = data.get("party", "")
        data["lost"] = dict(lost, near=lost.get("from") or near) if lost else None
        data["file"] = region_map.write_image(data, camp)
        region_map.save(path, data)
        if camp == _active_campaign():
            region_map._announce(data, False)
        return near
    except Exception as e:
        print(f"  (region map not updated: {e})", file=sys.stderr)
        return ""


def _region_party(camp: str) -> str:
    try:
        import region_map
        path = region_map.data_path(camp)
        return region_map.load(path).get("party", "") if os.path.isfile(path) else ""
    except Exception:
        return ""


# ─── Time (calendar.py) ───────────────────────────────────────────────────────

def now_hours(camp: str):
    """Hours since the start of the campaign's calendar, or None when it keeps none."""
    try:
        import timeline
        lib, cal = timeline.calendar_of(camp)
        if not lib:
            return None
        return lib._abs_day(cal) * 24 + int(cal.get("hour", 8))
    except Exception:
        return None


def tick(campaign: str, now_h=None) -> list:
    """calendar.py moved the clock: say when a lost party's guide may try again. Nothing without travel.json."""
    try:
        path = data_path(campaign)
    except Exception:
        return []
    if not os.path.isfile(path):
        return []
    data = load(path)
    lost = data.get("lost")
    if not data.get("on") or not lost or lost.get("retry_h") is None or now_h is None or lost.get("retry_due"):
        return []
    if now_h >= lost["retry_h"]:
        lost["retry_due"] = True
        save(path, data)
        return ["the party has spent its hours finding the way: the guide may try again — travel.py navigate"
                + ("" if lost.get("known") else " (the players still do not know they are lost: travel.py reveal)")]
    return []


# ─── The weather (scripts/weather.py) ────────────────────────────────────────

def _weather(camp: str) -> dict:
    """{"perception": 0 or -5, "storm": bool} from today's weather; nothing without a calendar."""
    try:
        import weather
        w = weather.current(camp)
        return {"perception": weather.perception_penalty(w), "storm": "storm" in weather.effects(w)}
    except Exception:
        return {"perception": 0, "storm": False}


# ─── The sheets ───────────────────────────────────────────────────────────────

def sheet_numbers(name: str, camp: str) -> dict:
    """{'name', 'survival', 'wis', 'passive', 'why'} from the character's sheet."""
    import group_check
    import inventory
    try:
        path = inventory.find_sheet(name, camp)
        text = inventory._read(path)
        real = inventory.display_name(path)
    except inventory.InventoryError:
        raise TravelError(f"no sheet for {name}")
    m = re.search(r"Passive Perception:\**\s*(\d+)", text)
    return {"name": real, "survival": group_check.bonus(text, "Survival", "WIS"),
            "wis": group_check._ability_mod(text, "WIS"),
            "passive": int(m.group(1)) if m else 10 + group_check.bonus(text, "Perception", "WIS"),
            "why": group_check.disadvantage(text, "Survival")}


def passives_of(data: dict, camp: str) -> dict:
    out = {}
    for n in data.get("players") or []:
        try:
            out[n] = sheet_numbers(n, camp)["passive"]
        except TravelError:
            pass
    return out


def _ask(name: str, mod: int, adv: str, label: str, dc: int, timeout: int):
    """One roll on a phone; its total, or None when nobody rolled. Raises when the display is off."""
    import group_check
    status, body = group_check._request("/dice-request", {"characters": [name], "spec": "1d20", "modifier": mod,
                                                          "advantage": adv, "label": label[:60], "dc": dc})
    if status != 200 or not isinstance(body, dict):
        raise TravelError("the display is not running — roll it yourself and pass --total" if status == 0
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


def _story(line: str) -> None:
    try:
        subprocess.run([sys.executable, os.path.join(_HERE, "send.py"), "--dice"], input=line, text=True,
                       encoding="utf-8", capture_output=True, timeout=15, env=dict(os.environ, PYTHONUTF8="1"))
    except Exception:
        pass


# ─── Telling the DM (written by the display server, drained by check_input.py) ─

def _notes_file() -> str:
    from runtime_paths import rt
    return rt("travel_notes.json")


def notes_add(line: str) -> None:
    path = _notes_file()
    try:
        with open(path, encoding="utf-8") as f:
            notes = json.load(f)
        if not isinstance(notes, list):
            notes = []
    except (OSError, ValueError):
        notes = []
    notes.append(line)
    try:
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(notes, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def drain_notes() -> list:
    path = _notes_file()
    try:
        with open(path, encoding="utf-8") as f:
            notes = json.load(f)
        os.remove(path)
    except (OSError, ValueError):
        return []
    return [n for n in notes if isinstance(n, str)]


def _announce(event: dict) -> None:
    """Tell the display. Silent when it is not running."""
    try:
        import send
        send._post(f"{send.BASE_URL}/travel", json.dumps({"event": event}).encode("utf-8"), send._read_token())
    except Exception:
        pass


def _party() -> list:
    from runtime_paths import rt
    try:
        with open(rt("stats.json"), encoding="utf-8") as f:
            players = json.load(f).get("players") or []
    except (OSError, ValueError, AttributeError):
        return []
    return [str(p.get("name")) for p in players if isinstance(p, dict) and p.get("name")]


# ─── Command line ─────────────────────────────────────────────────────────────

def main(argv: list | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    ap = argparse.ArgumentParser(description="The pace of the march, the roles on the road, getting lost.")
    ap.add_argument("--campaign", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("start", help="the party sets out")
    p.add_argument("--pace", default="normal")
    p.add_argument("--to", default="", help="where they are heading")
    p.add_argument("--terrain", default="", help="for the guide's checks")
    p.add_argument("--only", default="", help='only these characters, "A, B" (default: everyone at the table)')
    p = sub.add_parser("pace", help="fast, normal or slow")
    p.add_argument("pace")
    p = sub.add_parser("role", help="set a character's role (players choose theirs on the phone)")
    p.add_argument("character")
    p.add_argument("role")
    p = sub.add_parser("navigate", help="the guide's Wisdom (Survival) check")
    p.add_argument("--terrain", default="")
    p.add_argument("--dc", type=int)
    p.add_argument("--map", action="store_true", help="an accurate map, or the sun and stars to steer by: advantage")
    p.add_argument("--who", default="", help="who guides (default: the character with the guide role)")
    p.add_argument("--total", type=int, help="the roll is already made: its total")
    p.add_argument("--force", action="store_true", help="try again before the hours of searching are over")
    p.add_argument("--timeout", type=int, default=180)
    sub.add_parser("reveal", help="the players realise they are lost")
    p = sub.add_parser("found", help="back on the way")
    p.add_argument("--at", default="", help="the place of the region map where they come out")
    p = sub.add_parser("forage", help="each forager's Wisdom (Survival) check")
    p.add_argument("--dc", default="15", help="10 abundant, 15 limited, 20 scarce (or those words)")
    p.add_argument("--timeout", type=int, default=180)
    for cmd in ("watch", "show", "stop"):
        sub.add_parser(cmd)
    args = ap.parse_args(argv)

    try:
        camp = _campaign(args.campaign)
        path = data_path(camp)
        data = load(path)
        event = None
        rng = random.Random()
        import paths
        it = paths.campaign_language(camp) == "it"
        if args.cmd in ("show", "watch"):
            passives = passives_of(data, camp)
            wmod = _weather(camp)["perception"]
            if args.cmd == "watch":
                if not data.get("on"):
                    raise TravelError("the party is not on the road")
                w = watch(data, passives, wmod)
                for n, s in w["lookouts"].items():
                    print(f"  {n}: passive Perception {s}" + (f" ({w['mod']} for the pace and the weather)" if w["mod"] else ""))
                print(f"watch: {w['best']} ({w['by']})" if w["by"] else "NOBODY is keeping watch: nobody notices a threat passively")
            else:
                print(describe(data, passives, wmod))
            return 0
        if args.cmd == "start":
            players = [n.strip() for n in args.only.split(",") if n.strip()] or _party()
            start(data, players, args.pace, args.to, args.terrain)
            event = {"what": "start", "pace": data["pace"]}
            print(describe(data))
            print("Every phone now asks its player for a role on the road.")
        else:
            if args.cmd != "stop" and not data.get("on"):
                raise TravelError("the party is not on the road — travel.py start first")
            if args.cmd == "stop":
                if data.get("lost"):
                    _region(camp, None)
                data["on"], data["lost"] = False, None
                _log(data, "journey over")
                event = {"what": "stop"}
                print("journey over: the panel is gone from the phones")
            elif args.cmd == "pace":
                data["pace"] = pace_of(args.pace)
                _log(data, f"{data['pace']} pace")
                event = {"what": "pace", "pace": data["pace"]}
                print(describe(data))
            elif args.cmd == "role":
                name, everyone = set_role(data, args.character, args.role)
                event = {"what": "role", "character": name}
                print(f"{name}: {data['roles'].get(name, 'no role')}")
            elif args.cmd == "reveal":
                if not data.get("lost"):
                    raise TravelError("the party is not lost")
                data["lost"]["known"] = True
                _region(camp, data["lost"])
                event = {"what": "lost"}
                _story("🧭 " + ("Vi siete persi: il sentiero non porta dove pensavate." if it
                               else "You are lost: the trail does not lead where you thought."))
                print("the players know they are lost: their region map shows it")
            elif args.cmd == "found":
                was = data.get("lost")
                found(data)
                if was or args.at:
                    _region(camp, None, args.at)
                event = {"what": "found"}
                print("back on the way" + (f" at {args.at}" if args.at else ""))
            elif args.cmd == "navigate":
                dc = navigation_dc(data, args.dc, args.terrain)
                guide = args.who.strip() or next(iter(with_role(data, "guide")), "")
                if not guide:
                    raise TravelError("nobody is guiding — a player picks guide on the phone, or pass --who")
                lost = data.get("lost")
                now_h = now_hours(camp)
                if lost and not args.force and lost.get("retry_h") is not None and now_h is not None and now_h < lost["retry_h"]:
                    raise TravelError(f"the party is still searching for the way for {lost['retry_h'] - now_h} more hour(s) "
                                      "(calendar.py advance, or --force)")
                nums = sheet_numbers(guide, camp)
                pace_mod = PACES[data["pace"]]["nav"]
                mod = nums["survival"] + pace_mod
                where = (args.terrain or data.get("terrain") or "").lower()
                storm = _weather(camp)["storm"] and ("sea" in where or "mare" in where)
                if storm:                                             # a storm at sea (DMG p. 110)
                    print("  a storm at sea: no landmarks — disadvantage to find the way")
                # Any advantage and any disadvantage cancel out, however many of each (PHB p. 173).
                has_dis = bool(nums["why"]) or storm
                adv = ("normal" if has_dis else "advantage") if args.map else ("disadvantage" if has_dis else "normal")
                label = (f"Orientarsi (Sopravvivenza)" if it else "Find the way (Survival)")
                print(f"{nums['name']} guides: Survival {nums['survival']:+d}"
                      + (f", {pace_mod:+d} for the {data['pace']} pace" if pace_mod else "")
                      + (f", {adv}" if adv != "normal" else "") + f" — DC {dc}", flush=True)
                total = args.total if args.total is not None else _ask(nums["name"], mod, adv, label, dc, args.timeout)
                if total is None:
                    raise TravelError(f"{nums['name']} did not roll in time — nothing changed; ask again or pass --total")
                if total >= dc:
                    if lost:
                        found(data)
                        _region(camp, None)
                        event = {"what": "found"}
                        print(f"{total} vs DC {dc}: the guide finds the way again — back on course")
                    else:
                        _log(data, f"{nums['name']} keeps the way ({total})")
                        print(f"{total} vs DC {dc}: on course")
                else:
                    place = (lost or {}).get("from") or _region_party(camp)
                    known = bool(lost and lost.get("known"))
                    hours = get_lost(data, place, now_h, rng)
                    if known:
                        data["lost"]["known"] = True
                    _region(camp, data["lost"])
                    print(f"{total} vs DC {dc}: LOST — the party heads the wrong way"
                          + ("" if known else " and does NOT know it") + f". Describe the wrong direction; after {hours} "
                          "hour(s) of searching the guide may try again (travel.py navigate)."
                          + ("" if known else " When they realise it: travel.py reveal."))
            elif args.cmd == "forage":
                if not PACES[data["pace"]]["forage"]:
                    raise TravelError("nobody can forage at a fast pace (DMG p. 111) — travel.py pace normal first")
                dc_word = str(args.dc).strip().lower()
                dc = FORAGE_DC.get(dc_word) or (int(dc_word) if dc_word.isdigit() else None)
                if not dc or not 1 <= dc <= 40:
                    raise TravelError("--dc is 10, 15, 20 or abundant, limited, scarce")
                foragers = with_role(data, "forager")
                if not foragers:
                    raise TravelError("nobody is foraging — a player picks forager on the phone")
                found_food = {"rations": 0, "water": 0}
                for name in foragers:
                    nums = sheet_numbers(name, camp)
                    adv = "disadvantage" if nums["why"] else "normal"
                    total = _ask(nums["name"], nums["survival"], adv,
                                 "Foraggiare (Sopravvivenza)" if it else "Forage (Survival)", dc, args.timeout)
                    if total is None:
                        print(f"  {nums['name']}: no roll")
                    elif total < dc:
                        print(f"  {nums['name']}: {total} vs DC {dc} — finds nothing")
                    else:
                        got = forage_yield(nums["wis"], rng)
                        found_food["rations"] += got["rations"]
                        found_food["water"] += got["water"]
                        print(f"  {nums['name']}: {total} vs DC {dc} — {got['kg']:g} kg of food ({got['rations']} rations) "
                              f"and {got['litres']} l of water ({got['water']} days)")
                if found_food["rations"] or found_food["water"]:
                    try:
                        import supplies
                        spath = supplies.data_path(camp)
                        if os.path.isfile(spath):
                            sd = supplies.load(spath)
                            for k in ("rations", "water"):
                                sd["stock"][k] = min(supplies.MAX_STOCK, int(sd["stock"].get(k, 0)) + found_food[k])
                            supplies.save(spath, sd)
                            if camp == _active_campaign():
                                supplies._announce(sd)
                            print(f"supplies: +{found_food['rations']} rations, +{found_food['water']} water")
                        else:
                            print("(no supplies.json: nothing added — supplies.py add rations/water by hand if you count them)")
                    except Exception as e:
                        print(f"(supplies not updated: {e})")
                _log(data, f"foraged: {found_food['rations']} rations, {found_food['water']} water")
        save(path, data)
        if camp == _active_campaign() and event:
            _announce(event)
    except TravelError as e:
        print(f"travel: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
