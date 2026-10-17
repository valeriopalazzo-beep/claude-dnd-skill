#!/usr/bin/env python3
"""vehicles.py — carts, barges and ships the party travels in.

A boat or a ship has a speed of its own, hit points that only damage above its
threshold takes off, a crew and a hold; a cart, a wagon or a sled goes at the
pace of the animals that pull it and carries five times what they could
(PHB 2014 pp. 155-157, DMG 2014 p. 119).

    vehicles.py kinds                                       # the books' table
    vehicles.py add "Chiatta di Bortolo" --kind keelboat --owner "Bortolo"
    vehicles.py add "Carro" --kind wagon --pulled-by "2 draft horse"
    vehicles.py board "Chiatta"  [--current down|up|none] [--headwind]
    vehicles.py leave                                       # back on foot
    vehicles.py hit "Chiatta" 14                            # below the damage threshold nothing happens
    vehicles.py repair "Chiatta" 6 [--pay fund|"Lyra"]      # in port: 1 HP a day, 20 gp each (DMG p. 119)
    vehicles.py cargo "Chiatta" add "10 barili di vino"     # or: cargo "Chiatta" remove "barili"
    vehicles.py set "Chiatta" --crew 1 --note "fa acqua a poppa"
    vehicles.py remove "Carro"
    vehicles.py list
    vehicles.py show "Chiatta"

Kinds (Italian names too): rowboat, keelboat, longship, sailing-ship, warship,
galley, airship; cart, wagon, carriage, chariot, sled. Aboard a boat or a ship
the region map (region_map.py travel) counts the journey at its speed: a
sailing ship with its full crew goes day and night in shifts, a boat rowed
only 8 hours a day; on a river the current adds 4.5 km/h downstream and is
taken off upstream; a strong wind against the sails halves a sailing ship's
speed (--headwind). Everything lives in <campaign>/vehicles.json; every screen
has a Vehicles panel.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

# PHB 2014 p. 157 (speed), DMG 2014 p. 119 (crew, passengers, cargo in tons, AC, HP, damage threshold).
# "sails": it can go day and night with a full crew; the others are rowed or poled, 8 hours a day.
KINDS = {
    "rowboat": {"it": "barca a remi", "water": True, "kmh": 2.25, "crew": 1, "passengers": 3, "cargo_t": 0,
                "ac": 11, "hp": 50, "threshold": 0, "sails": False, "cost": "50 gp"},
    "keelboat": {"it": "barcone", "water": True, "kmh": 1.5, "crew": 1, "passengers": 6, "cargo_t": 0.5,
                 "ac": 15, "hp": 100, "threshold": 10, "sails": False, "cost": "3,000 gp"},
    "longship": {"it": "nave lunga", "water": True, "kmh": 4.5, "crew": 40, "passengers": 150, "cargo_t": 10,
                 "ac": 15, "hp": 300, "threshold": 15, "sails": True, "cost": "10,000 gp"},
    "sailing-ship": {"it": "nave a vela", "water": True, "kmh": 3, "crew": 20, "passengers": 20, "cargo_t": 100,
                     "ac": 15, "hp": 300, "threshold": 15, "sails": True, "cost": "10,000 gp"},
    "warship": {"it": "nave da guerra", "water": True, "kmh": 3.75, "crew": 60, "passengers": 60, "cargo_t": 200,
                "ac": 15, "hp": 500, "threshold": 20, "sails": True, "cost": "25,000 gp"},
    "galley": {"it": "galea", "water": True, "kmh": 6, "crew": 80, "passengers": 0, "cargo_t": 150,
               "ac": 15, "hp": 500, "threshold": 20, "sails": True, "cost": "30,000 gp"},
    "airship": {"it": "aeronave", "water": False, "air": True, "kmh": 12, "crew": 10, "passengers": 20, "cargo_t": 1,
                "ac": 13, "hp": 300, "threshold": 0, "sails": True, "cost": "20,000 gp"},
    # Drawn by animals (PHB p. 155): their pace, five times their carrying capacity; weight in kg.
    "cart": {"it": "carretto", "water": False, "drawn": True, "weight_kg": 100, "cost": "15 gp"},
    "wagon": {"it": "carro", "water": False, "drawn": True, "weight_kg": 200, "cost": "35 gp"},
    "carriage": {"it": "carrozza", "water": False, "drawn": True, "weight_kg": 300, "cost": "100 gp"},
    "chariot": {"it": "biga", "water": False, "drawn": True, "weight_kg": 50, "cost": "250 gp"},
    "sled": {"it": "slitta", "water": False, "drawn": True, "weight_kg": 150, "cost": "20 gp"},
}
KIND_ALIASES = {"barca": "rowboat", "barca a remi": "rowboat", "barca-a-remi": "rowboat", "row boat": "rowboat",
                "barcone": "keelboat", "chiatta": "keelboat", "barge": "keelboat", "keel boat": "keelboat",
                "nave lunga": "longship", "nave-lunga": "longship", "drakkar": "longship",
                "nave a vela": "sailing-ship", "nave-a-vela": "sailing-ship", "nave": "sailing-ship", "veliero": "sailing-ship",
                "sailing ship": "sailing-ship", "ship": "sailing-ship", "nave da guerra": "warship", "nave-da-guerra": "warship",
                "galea": "galley", "galera": "galley", "aeronave": "airship", "dirigibile": "airship",
                "carretto": "cart", "carro": "wagon", "carrozza": "carriage", "biga": "chariot", "cocchio": "chariot",
                "slitta": "sled", "sledge": "sled", "sleigh": "sled"}
# PHB p. 157: what an animal carries (kg) — a vehicle it pulls takes five times as much, the vehicle included.
ANIMALS = {"mule": 210, "donkey": 210, "camel": 240, "riding horse": 240, "warhorse": 270, "draft horse": 270,
           "elephant": 660, "mastiff": 97.5, "pony": 112.5}
ANIMAL_ALIASES = {"mulo": "mule", "asino": "donkey", "cammello": "camel", "cavallo da galoppo": "riding horse",
                  "cavallo da guerra": "warhorse", "cavallo da tiro": "draft horse", "cavallo": "draft horse",
                  "cavalli da tiro": "draft horse", "cavalli": "draft horse", "muli": "mule", "asini": "donkey",
                  "elefante": "elephant", "mastino": "mastiff", "pony": "pony", "horse": "draft horse", "horses": "draft horse",
                  "mules": "mule"}
CURRENT_KMH = 4.5        # PHB p. 155: a river's current, usually 3 mph
WALK_KM_DAY = 36         # PHB p. 182: a normal pace, 8 hours
REPAIR_GP = 20           # DMG p. 119: 1 hit point a day, 20 gp each, in port
MAX_VEHICLES = 12


class VehicleError(Exception):
    """A request that cannot be applied; nothing was changed."""


# ─── Data ─────────────────────────────────────────────────────────────────────

def _campaign(campaign: str = "") -> str:
    import media
    camp = media.safe_campaign(campaign) if campaign else media.active_campaign()
    if not camp:
        raise VehicleError("no active campaign — pass --campaign or load one first")
    return camp


def data_path(campaign: str = "") -> str:
    import media
    return str(media.find_campaign(_campaign(campaign)) / "vehicles.json")


def blank() -> dict:
    return {"list": [], "aboard": "", "current": "none", "headwind": False}


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        return blank()
    except ValueError as e:
        raise VehicleError(f"vehicles.json is not valid JSON ({e})")
    base = blank()
    if isinstance(data, dict):
        base.update({k: data[k] for k in base if k in data})
    if not isinstance(base["list"], list):
        base["list"] = []
    return base


def save(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _key(text: str) -> str:
    return "".join(ch for ch in (text or "").casefold() if ch.isalnum())


def _clean(text, limit: int) -> str:
    return " ".join(re.sub(r"[`\\$<>]", "", str(text or "")).split())[:limit]


def kind_of(word: str) -> str:
    w = " ".join((word or "").strip().lower().replace("_", " ").split())
    w = KIND_ALIASES.get(w, KIND_ALIASES.get(w.replace(" ", "-"), w.replace(" ", "-")))
    if w not in KINDS:
        raise VehicleError(f"a kind is one of {', '.join(KINDS)} — not {word!r} (vehicles.py kinds)")
    return w


def find(data: dict, name: str) -> dict:
    want = _key(name)
    if not want:
        raise VehicleError("which vehicle?")
    rows = data["list"]
    exact = [v for v in rows if _key(v["name"]) == want]
    hits = exact or [v for v in rows if want in _key(v["name"])]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise VehicleError(f"no vehicle called {name!r}" + (f" — there are {', '.join(v['name'] for v in rows)}" if rows else ""))
    raise VehicleError(f"{name!r} means several: " + ", ".join(v["name"] for v in hits))


def team(text: str) -> tuple:
    """'2 cavalli da tiro, 1 mulo' → ([{"animal", "n"}], carrying capacity in kg of the vehicle they pull)."""
    out, kg = [], 0.0
    for part in re.split(r"[,;+]| e | and ", text or ""):
        part = part.strip().lower()
        if not part:
            continue
        m = re.match(r"^(\d+)\s*[x×]?\s*(.+)$", part)
        n, word = (int(m.group(1)), m.group(2).strip()) if m else (1, part)
        animal = ANIMAL_ALIASES.get(word, word)
        if animal not in ANIMALS:
            raise VehicleError(f"what pulls it? one of {', '.join(ANIMALS)} (Italian too) — not {word!r}")
        out.append({"animal": animal, "n": n})
        kg += ANIMALS[animal] * n * 5
    return out, kg


def add(data: dict, name: str, kind: str, owner: str = "", pulled_by: str = "", note: str = "") -> dict:
    name = _clean(name, 50)
    if not name:
        raise VehicleError("a vehicle needs a name")
    if any(_key(v["name"]) == _key(name) for v in data["list"]):
        raise VehicleError(f"there is already a vehicle called {name!r}")
    if len(data["list"]) >= MAX_VEHICLES:
        raise VehicleError(f"{MAX_VEHICLES} vehicles already — remove the ones left behind")
    k = kind_of(kind)
    spec = KINDS[k]
    entry = {"id": secrets.token_hex(4), "name": name, "kind": k, "owner": _clean(owner, 50), "note": _clean(note, 200),
             "cargo": [], "ts": time.time()}
    if spec.get("drawn"):
        animals, kg = team(pulled_by) if pulled_by else ([], 0.0)
        entry.update({"team": animals, "capacity_kg": round(kg - spec["weight_kg"], 1) if kg else None})
    else:
        if pulled_by:
            raise VehicleError(f"a {k} is not pulled by animals")
        entry.update({"hp": {"current": spec["hp"], "max": spec["hp"]}, "ac": spec["ac"], "threshold": spec["threshold"],
                      "crew": spec["crew"], "kmh": spec["kmh"]})
    data["list"].append(entry)
    return entry


def hit(entry: dict, damage: int) -> dict:
    """Damage to a boat or a ship: below its damage threshold it does nothing (DMG p. 119)."""
    if "hp" not in entry:
        raise VehicleError(f"{entry['name']} has no hit points of its own (a {entry['kind']})")
    if damage < 0:
        raise VehicleError("damage is 0 or more (repair it with `repair`)")
    thr = int(entry.get("threshold") or 0)
    if thr and damage < thr:
        return {"taken": 0, "threshold": thr, "current": entry["hp"]["current"], "sunk": False}
    hp = entry["hp"]
    hp["current"] = max(0, int(hp["current"]) - damage)
    return {"taken": damage, "threshold": thr, "current": hp["current"], "sunk": hp["current"] == 0}


def repair(entry: dict, points: int) -> int:
    """Hit points back (in port: one a day). Returns how many, the maximum counted."""
    if "hp" not in entry:
        raise VehicleError(f"{entry['name']} has no hit points to repair")
    hp = entry["hp"]
    got = max(0, min(int(points), int(hp["max"]) - int(hp["current"])))
    hp["current"] = int(hp["current"]) + got
    return got


def speed(data: dict, entry: dict, wind_strong: bool = False) -> dict:
    """{"kmh", "hours_day", "km_day", "why": [...]} — how far it goes, the current and the wind counted."""
    spec = KINDS[entry["kind"]]
    why = []
    if spec.get("drawn"):
        return {"kmh": None, "hours_day": 8, "km_day": None, "why": ["at the pace of the animals that pull it"]}
    kmh = float(entry.get("kmh") or spec["kmh"])
    crew_ok = int(entry.get("crew", spec["crew"])) >= int(spec["crew"])
    hours = 24 if spec.get("sails") and crew_ok else 8
    if spec.get("sails") and not crew_ok:
        why.append(f"crew {entry.get('crew')} of {spec['crew']}: 8 hours a day, not day and night")
    if spec.get("water") and data.get("current") == "down":
        kmh += CURRENT_KMH
        why.append(f"downstream: +{CURRENT_KMH:g} km/h")
    elif spec.get("water") and data.get("current") == "up":
        kmh -= CURRENT_KMH
        why.append(f"upstream: -{CURRENT_KMH:g} km/h")
    if spec.get("sails") and data.get("headwind") and wind_strong:
        kmh /= 2
        why.append("a strong wind against the sails: half speed")
    kmh = round(max(0.0, kmh), 2)
    return {"kmh": kmh, "hours_day": hours, "km_day": round(kmh * hours, 1), "why": why}


def journey(data: dict, walking_days: float, wind_strong: bool = False) -> dict | None:
    """A road of `walking_days` (normal pace, 36 km a day) taken aboard: {"vehicle", "hours", "days", "km"}; None on foot."""
    if not data.get("aboard"):
        return None
    try:
        v = find(data, data["aboard"])
    except VehicleError:
        return None
    sp = speed(data, v, wind_strong)
    km = walking_days * WALK_KM_DAY
    if sp["kmh"] is None:
        return {"vehicle": v["name"], "drawn": True, "km": km}
    if sp["kmh"] <= 0:
        return {"vehicle": v["name"], "stuck": True, "km": km, "why": sp["why"]}
    hours = km / sp["kmh"]
    return {"vehicle": v["name"], "hours": hours, "days": hours / sp["hours_day"], "km": km,
            "hours_day": sp["hours_day"], "why": sp["why"]}


def public_view(data: dict, wind_strong: bool = False) -> list:
    out = []
    for v in data["list"]:
        spec = KINDS.get(v["kind"], {})
        row = {k: v.get(k) for k in ("id", "name", "kind", "owner", "note", "cargo", "hp", "ac", "threshold", "crew",
                                     "team", "capacity_kg") if v.get(k) not in (None, "")}
        row["aboard"] = _key(v["name"]) == _key(data.get("aboard", ""))
        row["needs_crew"] = spec.get("crew")
        row["passengers"] = spec.get("passengers")
        row["cargo_t"] = spec.get("cargo_t")
        sp = speed(data, v, wind_strong)
        row.update({"kmh": sp["kmh"], "km_day": sp["km_day"], "hours_day": sp["hours_day"]})
        if row["aboard"]:
            row["current"] = data.get("current", "none")
        out.append(row)
    return out


def describe(data: dict, v: dict, wind_strong: bool = False) -> str:
    spec = KINDS[v["kind"]]
    head = f"{v['name']} — {v['kind']} ({spec['it']})" + (f", of {v['owner']}" if v.get("owner") else "") \
        + ("  [ABOARD]" if _key(v["name"]) == _key(data.get("aboard", "")) else "")
    lines = [head]
    if "hp" in v:
        lines.append(f"  HP {v['hp']['current']}/{v['hp']['max']}, AC {v['ac']}"
                     + (f", damage threshold {v['threshold']}" if v.get("threshold") else "")
                     + f"; crew {v.get('crew')}/{spec['crew']}, passengers {spec['passengers']}, hold {spec['cargo_t']:g} t")
    if v.get("team") is not None:
        team_txt = ", ".join(f"{a['n']} {a['animal']}" for a in v.get("team") or []) or "nothing yet (--pulled-by)"
        lines.append(f"  pulled by {team_txt}" + (f"; carries {v['capacity_kg']:g} kg" if v.get("capacity_kg") else ""))
    sp = speed(data, v, wind_strong)
    if sp["kmh"] is not None:
        lines.append(f"  {sp['kmh']:g} km/h, {sp['hours_day']} h a day: {sp['km_day']:g} km a day" + "".join(f"; {w}" for w in sp["why"]))
    lines += [f"  · {c}" for c in v.get("cargo") or []]
    if v.get("note"):
        lines.append(f"  ({v['note']})")
    return "\n".join(lines)


def _wind_strong(camp: str) -> bool:
    try:
        import weather
        return weather.ranged_disadvantage(weather.current(camp))
    except Exception:
        return False


def _announce(event: dict | None = None) -> None:
    """Tell the display. Silent when it is not running."""
    try:
        import send
        send._post(f"{send.BASE_URL}/vehicles", json.dumps({"event": event} if event else {}).encode("utf-8"),
                   send._read_token())
    except Exception:
        pass


# ─── Command line ─────────────────────────────────────────────────────────────

def main(argv: list | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    ap = argparse.ArgumentParser(description="Carts, barges and ships the party travels in.")
    ap.add_argument("--campaign", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("kinds")
    p = sub.add_parser("add")
    p.add_argument("name")
    p.add_argument("--kind", required=True)
    p.add_argument("--owner", default="", help="whose it is (default: the party's)")
    p.add_argument("--pulled-by", default="", help='a cart or a wagon: "2 draft horse", "1 mulo"')
    p.add_argument("--note", default="")
    p = sub.add_parser("board", help="the party travels aboard it")
    p.add_argument("name")
    p.add_argument("--current", default="none", help="on a river: down (with the current), up (against it), none")
    p.add_argument("--headwind", action="store_true", help="the wind blows against the sails")
    sub.add_parser("leave", help="back on foot")
    p = sub.add_parser("hit")
    p.add_argument("name")
    p.add_argument("damage", type=int)
    p = sub.add_parser("repair")
    p.add_argument("name")
    p.add_argument("points", type=int)
    p.add_argument("--pay", default="", help=f"pay {REPAIR_GP} gp a point: fund (treasury.py) or a character")
    p = sub.add_parser("cargo")
    p.add_argument("name")
    p.add_argument("action", choices=["add", "remove"])
    p.add_argument("what")
    p = sub.add_parser("set")
    p.add_argument("name")
    p.add_argument("--crew", type=int)
    p.add_argument("--owner", default=None)
    p.add_argument("--note", default=None)
    p.add_argument("--pulled-by", default=None)
    for cmd in ("remove", "show"):
        sub.add_parser(cmd).add_argument("name")
    sub.add_parser("list")
    args = ap.parse_args(argv)

    if args.cmd == "kinds":
        for k, s in KINDS.items():
            if s.get("drawn"):
                print(f"{k:13} {s['it']:15} {s['cost']:>10}, {s['weight_kg']} kg — at the animals' pace, carries 5× what they could")
            else:
                print(f"{k:13} {s['it']:15} {s['cost']:>10}, {s['kmh']:g} km/h, crew {s['crew']}, passengers {s['passengers']}, "
                      f"hold {s['cargo_t']:g} t, AC {s['ac']}, HP {s['hp']}" + (f", threshold {s['threshold']}" if s["threshold"] else "")
                      + (" — sails: day and night with a full crew" if s["sails"] else " — rowed: 8 hours a day"))
        return 0
    try:
        import media
        camp = _campaign(args.campaign)
        path = data_path(camp)
        data = load(path)
        wind = _wind_strong(camp)
        event = None
        if args.cmd == "list":
            print("\n\n".join(describe(data, v, wind) for v in data["list"]) or "no vehicles")
            return 0
        if args.cmd == "show":
            print(describe(data, find(data, args.name), wind))
            return 0
        if args.cmd == "add":
            v = add(data, args.name, args.kind, args.owner, args.pulled_by, args.note)
            event = {"name": v["name"], "what": "new"}
            print("+ " + describe(data, v, wind))
        elif args.cmd == "board":
            v = find(data, args.name)
            cur = (args.current or "none").lower()
            cur = {"giù": "down", "giu": "down", "valle": "down", "su": "up", "monte": "up", "contro": "up",
                   "nessuna": "none", "no": "none"}.get(cur, cur)
            if cur not in ("down", "up", "none"):
                raise VehicleError("--current is down, up or none")
            data.update({"aboard": v["name"], "current": cur, "headwind": bool(args.headwind)})
            sp = speed(data, v, wind)
            if sp["kmh"] == 0:
                raise VehicleError(f"{v['name']} cannot go against this current — tow it from the bank, or wait")
            event = {"name": v["name"], "what": "board"}
            print(f"aboard {v['name']}: " + (f"{sp['kmh']:g} km/h, {sp['hours_day']} h a day ({sp['km_day']:g} km)"
                                            if sp["kmh"] is not None else "at the animals' pace")
                  + "".join(f"; {w}" for w in sp["why"]) + " — region_map.py travel counts it")
            if KINDS[v["kind"]].get("sails") and not wind and not args.headwind:
                print("  (in a dead calm a sailing ship moves by oars, DMG p. 119)")
        elif args.cmd == "leave":
            if not data.get("aboard"):
                raise VehicleError("the party is not aboard anything")
            event = {"name": data["aboard"], "what": "leave"}
            print(f"off {data['aboard']}: on foot again")
            data.update({"aboard": "", "current": "none", "headwind": False})
        elif args.cmd == "hit":
            v = find(data, args.name)
            r = hit(v, args.damage)
            if not r["taken"]:
                print(f"{v['name']}: {args.damage} is below its damage threshold ({r['threshold']}) — no damage")
            else:
                print(f"{v['name']}: -{r['taken']} HP → {r['current']}/{v['hp']['max']}" + (" — IT SINKS / BREAKS UP" if r["sunk"] else ""))
                event = {"name": v["name"], "what": "sunk" if r["sunk"] else "hit"}
        elif args.cmd == "repair":
            v = find(data, args.name)
            got = repair(v, args.points)
            cost = got * REPAIR_GP
            if got and args.pay:
                if args.pay.lower() in ("fund", "cassa", "party"):
                    import treasury
                    tpath = treasury.data_path(camp)
                    td = treasury.load(tpath)
                    if td is None:
                        raise VehicleError("the party has no common fund (treasury.py) — pay from a character")
                    treasury.take(td, {"gp": cost}, "spend", "", f"repair of {v['name']}")
                    treasury.save(tpath, td)
                    treasury._announce()
                else:
                    import inventory
                    inventory.op_pay(args.pay, cost * 100, camp)
            print(f"{v['name']}: +{got} HP → {v['hp']['current']}/{v['hp']['max']}; {got} day(s) in port, {cost} gp"
                  + (f" paid by {args.pay}" if got and args.pay else "") + " (DMG p. 119)")
            event = {"name": v["name"], "what": "repair"} if got else None
        elif args.cmd == "cargo":
            v = find(data, args.name)
            what = _clean(args.what, 80)
            if args.action == "add":
                if not what:
                    raise VehicleError("what goes in the hold?")
                v.setdefault("cargo", []).append(what)
            else:
                hits = [c for c in v.get("cargo") or [] if _key(what) and _key(what) in _key(c)]
                if len(hits) != 1:
                    raise VehicleError(f"{args.what!r} matches {len(hits)} things in the hold")
                v["cargo"].remove(hits[0])
            print(describe(data, v, wind))
        elif args.cmd == "set":
            v = find(data, args.name)
            if args.crew is not None:
                if "hp" not in v or not 0 <= args.crew <= 500:
                    raise VehicleError("--crew is for a boat or a ship, 0 to 500")
                v["crew"] = args.crew
            if args.owner is not None:
                v["owner"] = _clean(args.owner, 50)
            if args.note is not None:
                v["note"] = _clean(args.note, 200)
            if args.pulled_by is not None:
                if v.get("team") is None:
                    raise VehicleError(f"a {v['kind']} is not pulled by animals")
                animals, kg = team(args.pulled_by)
                v["team"], v["capacity_kg"] = animals, round(kg - KINDS[v["kind"]]["weight_kg"], 1) if kg else None
            print(describe(data, v, wind))
        elif args.cmd == "remove":
            v = find(data, args.name)
            data["list"].remove(v)
            if _key(data.get("aboard", "")) == _key(v["name"]):
                data.update({"aboard": "", "current": "none", "headwind": False})
            print(f"- {v['name']}")
        save(path, data)
        if camp == media.active_campaign():
            _announce(event)
    except VehicleError as e:
        print(f"vehicles: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    except Exception as e:                 # treasury / inventory refused the payment
        if type(e).__name__ in ("TreasuryError", "InventoryError"):
            print(f"vehicles: {e}. Nothing was changed.", file=sys.stderr)
            return 1
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
