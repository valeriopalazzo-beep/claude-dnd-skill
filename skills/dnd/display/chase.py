#!/usr/bin/env python3
"""chase.py — a chase on the players' phones (DMG 2014 pp. 252-255).

A quarry and its pursuers run along one line; the display keeps the
distances, the order, the dashes and the exhaustion they cost. On their turn
each character chooses on their own phone: move (their speed), dash (twice
it: 3 + CON modifier dashes are free, every one after asks a DC 10
Constitution save or a level of exhaustion) or stop. At the end of every
turn a d20 is rolled for complications (urban or wilderness table, 1-10 = a
complication), which falls on the NEXT one in the order. At the end of each
round a quarry out of the lead pursuer's sight may slip away: Dexterity
(Stealth) against the pursuers' best passive Perception. The chase ends when
a quarry is caught (a pursuer reaches it), escapes, or everyone gives up.

The DM plays the creatures from the terminal and hears of the players' turns
as `[[CHASE: …]]` lines (check_input.py, autorun_wait.py).

    chase.py start --quarry "Ladro:speed=9,con=1,dex=3,stealth=5" --pursuers "Lyra, Hernio, Guardia" --gap 18 --setting urban
    chase.py start --quarry "Lyra, Hernio" --pursuers "Thug:2" --gap 12 --setting wild     # the party runs
    chase.py act "Ladro" dash          # a creature's turn: move | dash | stop
    chase.py adjust "Hernio" -3        # a complication cost 3 m (or +3 for a shortcut)
    chase.py sight off                 # the quarry turned a corner: it may try to escape this round
    chase.py cover crowded|normal|open # advantage / none / disadvantage on the escape checks
    chase.py show
    chase.py end

Characters at the table are read from their sheets (speed, CON, DEX,
Stealth, passive Perception). A creature is `Name:key=value,…` (speed in
metres, con, dex, stealth, passive); a name the SRD knows fills in the rest
("Thug:2" = two thugs). --setting urban|wild picks the complications.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import secrets
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from runtime_paths import rt          # writable runtime dir; also puts scripts/ on the path

NOTES_FILE = rt("chase_notes.json")
REACH_M = 1.5                         # a pursuer this close has caught the quarry
DASH_DC = 10
EXHAUSTION_OUT = 5                    # DMG: at 5 levels a participant drops out (speed 0)
SETTINGS = ("urban", "wild")
COVERS = ("crowded", "normal", "open")

# DMG 2014 p. 254-255: complications, d20 1-10 (11-20 none). Each: key, what to roll, what a failure costs.
COMPLICATIONS = {
    "urban": [
        ("cart", "DC 15 Dexterity (Acrobatics)", "the obstacle is 3 m of difficult terrain"),
        ("crowd", "DC 10 Strength (Athletics) or Dexterity (Acrobatics)", "3 m of difficult terrain"),
        ("window", "DC 10 Strength save to smash through", "bounces off and falls prone"),
        ("crates", "DC 10 Dexterity (Acrobatics) or Intelligence check", "3 m of difficult terrain"),
        ("slippery", "DC 10 Dexterity save", "falls prone"),
        ("dogs", "DC 10 Dexterity (Acrobatics)", "1d4 piercing and 1.5 m of difficult terrain"),
        ("brawl", "DC 15 Strength (Athletics), Dexterity (Acrobatics) or Charisma (Intimidation)", "2d4 bludgeoning and 3 m of difficult terrain"),
        ("beggar", "DC 10 Strength (Athletics), Dexterity (Acrobatics) or Charisma (Intimidation) — or toss a coin", "1.5 m of difficult terrain"),
        ("guard", "none: moving 6 m or more this turn draws an attack", "opportunity attack +3, 1d6+1 bludgeoning"),
        ("turn", "DC 10 Dexterity save", "collides with something: 1d4 bludgeoning"),
    ],
    "wild": [
        ("brush", "DC 10 Strength (Athletics) or Dexterity (Acrobatics)", "1.5 m of difficult terrain"),
        ("uneven", "DC 10 Dexterity (Acrobatics)", "3 m of difficult terrain"),
        ("insects", "none: the swarm attacks", "opportunity attack +3, 4d4 piercing"),
        ("stream", "DC 10 Strength (Athletics) or Dexterity (Acrobatics)", "3 m of difficult terrain"),
        ("dust", "DC 10 Constitution save", "blinded until the end of the turn: speed halved"),
        ("drop", "DC 10 Dexterity save", "falls 1d4 × 1.5 m (1d6 bludgeoning per 3 m) and lands prone"),
        ("snare", "DC 15 Dexterity save", "restrained in a net (DC 10 Strength to break free)"),
        ("stampede", "DC 10 Dexterity save", "1d4 bludgeoning and 1d4 piercing"),
        ("razorvine", "DC 15 Dexterity save, or spend 3 m of movement to avoid it", "1d10 slashing"),
        ("predator", "none", "a creature of the place joins the chase — the DM chooses it"),
    ],
}


class ChaseError(Exception):
    """A request that cannot be applied; nothing was changed."""


# ─── The state ────────────────────────────────────────────────────────────────

def _clean(text, limit: int = 50) -> str:
    return " ".join(re.sub(r"[`\\$<>]", "", str(text or "")).split())[:limit]


def _key(name: str) -> str:
    return "".join(ch for ch in (name or "").casefold() if ch.isalnum())


def runner(name: str, side: str, speed: float = 9.0, con: int = 0, dex: int = 0, stealth: int | None = None,
           passive: int = 10, pc: bool = False) -> dict:
    return {"name": _clean(name), "side": side, "speed": max(0.0, float(speed)), "con": int(con), "dex": int(dex),
            "stealth": int(dex if stealth is None else stealth), "passive": int(passive), "pc": bool(pc),
            "pos": 0.0, "dashes": 0, "free": max(0, 3 + int(con)), "exhaustion": 0, "status": "in", "init": 0}


def start(quarry: list, pursuers: list, gap: float, setting: str = "urban", rng: random.Random | None = None,
          campaign: str = "") -> dict:
    rng = rng or random.Random()
    if setting not in SETTINGS:
        raise ChaseError(f"setting is {' or '.join(SETTINGS)}")
    if not quarry or not pursuers:
        raise ChaseError("a chase needs a quarry and at least one pursuer")
    if not 0 < float(gap) <= 300:
        raise ChaseError("the gap is the quarry's lead in metres, 1 to 300")
    seen = set()
    for r in quarry + pursuers:
        if _key(r["name"]) in seen or not r["name"]:
            raise ChaseError(f"{r['name'] or 'a nameless runner'} is listed twice")
        seen.add(_key(r["name"]))
    for r in quarry:
        r["side"], r["pos"] = "quarry", float(gap)
    for r in pursuers:
        r["side"], r["pos"] = "pursuer", 0.0
    runners = quarry + pursuers
    for r in runners:                       # initiative: d20 + DEX, ties to the higher DEX
        r["init"] = rng.randint(1, 20) + r["dex"]
    runners.sort(key=lambda r: (-r["init"], -r["dex"], r["name"]))
    return {"id": secrets.token_hex(4), "campaign": campaign, "setting": setting, "round": 1, "turn": 0,
            "runners": runners, "sight": True, "cover": "normal", "over": False, "log": []}


def find(state: dict, name: str) -> dict:
    hit = next((r for r in state["runners"] if _key(r["name"]) == _key(name)), None)
    if hit is None:
        raise ChaseError(f"{name} is not in the chase ({', '.join(r['name'] for r in state['runners'])})")
    return hit


def active(state: dict, side: str | None = None) -> list:
    return [r for r in state["runners"] if r["status"] == "in" and (side is None or r["side"] == side)]


def current(state: dict) -> dict | None:
    if state.get("over"):
        return None
    return state["runners"][state["turn"]] if state["runners"] else None


def speed_now(r: dict) -> float:
    """PHB exhaustion: level 2 halves the speed, level 5 makes it 0."""
    if r["exhaustion"] >= EXHAUSTION_OUT:
        return 0.0
    return r["speed"] / 2 if r["exhaustion"] >= 2 else r["speed"]


def gap_of(state: dict, r: dict) -> float | None:
    """A pursuer: metres to the nearest quarry still running. A quarry: metres to the nearest pursuer."""
    others = active(state, "quarry" if r["side"] == "pursuer" else "pursuer")
    if not others:
        return None
    return round(min(abs(o["pos"] - r["pos"]) for o in others), 1)


def lead_pursuer(state: dict) -> dict | None:
    ps = active(state, "pursuer")
    return max(ps, key=lambda r: r["pos"]) if ps else None


def _say(state: dict, key: str, text: str, **values) -> dict:
    ev = {"key": key, "text": text, "vars": {k: str(v) for k, v in values.items()}, "round": state["round"]}
    state["log"].append(ev)
    del state["log"][:-40]
    return ev


# ─── A turn ──────────────────────────────────────────────────────────────────

def _caught(state: dict, who: dict) -> list:
    """After the DM moved `who`: the pairs it is part of, where a pursuer now reaches a quarry."""
    events = []
    for q in active(state, "quarry"):
        if not q.get("acted"):                # nobody is caught before their first turn
            continue
        for p in active(state, "pursuer"):
            if who not in (p, q):
                continue
            if p["pos"] >= q["pos"] - REACH_M:
                q["status"] = "caught"
                p["pos"] = q["pos"]
                events.append(_say(state, "caught", f"{p['name']} catches {q['name']}", who=p["name"], quarry=q["name"]))
                break
    return events


def _maybe_over(state: dict) -> list:
    if state["over"]:
        return []
    if not active(state, "quarry"):
        state["over"] = True
        return [_say(state, "over.quarry", "the chase is over: no quarry is running")]
    if not active(state, "pursuer"):
        state["over"] = True
        for q in active(state, "quarry"):
            q["status"] = "escaped"
        return [_say(state, "over.pursuers", "the chase is over: no pursuer is left")]
    return []


def _complication(state: dict, r: dict, rng: random.Random) -> list:
    """End of r's turn: d20, 1-10 is a complication for the next one in the order."""
    d20 = rng.randint(1, 20)
    if d20 > 10:
        return []
    nxt = _next_index(state, state["runners"].index(r))
    if nxt is None:
        return []
    target = state["runners"][nxt]
    key, check, fail = COMPLICATIONS[state["setting"]][d20 - 1]
    target["complication"] = {"key": f"{state['setting']}.{key}", "check": check, "fail": fail, "round": state["round"]}
    return [_say(state, "complication", f"complication for {target['name']} ({key}): {check}; on a failure, {fail}",
                 who=target["name"], cx=f"{state['setting']}.{key}")]


def _next_index(state: dict, i: int) -> int | None:
    n = len(state["runners"])
    for step in range(1, n + 1):
        j = (i + step) % n
        if state["runners"][j]["status"] == "in":
            return j
    return None


def _escapes(state: dict, rng: random.Random) -> list:
    """End of the round: each quarry out of sight tries to slip away (NPCs rolled here, PCs asked)."""
    events = []
    pursuers = active(state, "pursuer")
    if not pursuers:
        return events
    dc = max(p["passive"] for p in pursuers)
    for q in active(state, "quarry"):
        if state["sight"]:
            continue
        if q["pc"]:
            events.append(_say(state, "escape.ask", f"{q['name']} may slip away: Dexterity (Stealth) against {dc}"
                               + {"crowded": " with advantage", "open": " with disadvantage"}.get(state["cover"], ""),
                               who=q["name"], dc=dc))
            continue
        a, b = rng.randint(1, 20), rng.randint(1, 20)
        d20 = max(a, b) if state["cover"] == "crowded" else min(a, b) if state["cover"] == "open" else a
        total = d20 + q["stealth"]
        if total >= dc:
            q["status"] = "escaped"
            events.append(_say(state, "escaped", f"{q['name']} slips away (Stealth {total} against {dc})",
                               who=q["name"], roll=total, dc=dc))
        else:
            events.append(_say(state, "spotted", f"{q['name']} does not shake them off (Stealth {total} against {dc})",
                               who=q["name"], roll=total, dc=dc))
    return events


def act(state: dict, name: str, action: str, rng: random.Random | None = None) -> list:
    """The runner whose turn it is moves, dashes or stops. Returns the events, in order."""
    rng = rng or random.Random()
    if state.get("over"):
        raise ChaseError("the chase is over")
    r = find(state, name)
    if r is not current(state):
        raise ChaseError(f"it is {current(state)['name']}'s turn, not {r['name']}'s")
    if action not in ("move", "dash", "stop"):
        raise ChaseError("move, dash or stop")
    events = []
    r.pop("complication", None)
    if action == "stop":
        r["status"] = "stopped"
        events.append(_say(state, "stop", f"{r['name']} gives up the chase", who=r["name"]))
    else:
        step = speed_now(r) * (2 if action == "dash" else 1)
        reached = None
        if r["side"] == "pursuer":            # a pursuer stops at the first quarry it reaches
            ahead = sorted((q for q in active(state, "quarry") if q["pos"] >= r["pos"]), key=lambda q: q["pos"])
            if ahead and r["pos"] + step >= ahead[0]["pos"] - REACH_M:
                reached = ahead[0]
        if reached is None:
            r["pos"] = round(r["pos"] + step, 1)
        elif reached.get("acted"):
            r["pos"] = reached["pos"]
        else:
            r["pos"] = round(max(r["pos"], reached["pos"] - REACH_M), 1)
        if action == "dash":
            r["dashes"] += 1
            if r["dashes"] > r["free"]:
                save = rng.randint(1, 20) + r["con"]
                if save < DASH_DC:
                    r["exhaustion"] += 1
                    events.append(_say(state, "dash.fail", f"{r['name']} dashes again: Constitution {save}, fails — "
                                       f"exhaustion level {r['exhaustion']}", who=r["name"], roll=save, level=r["exhaustion"]))
                else:
                    events.append(_say(state, "dash.ok", f"{r['name']} dashes again: Constitution {save}, holds on",
                                       who=r["name"], roll=save))
        events.insert(0, _say(state, "dash" if action == "dash" else "move",
                              f"{r['name']} {'dashes' if action == 'dash' else 'moves'} {step:g} m", who=r["name"], m=f"{step:g}"))
        if r["exhaustion"] >= EXHAUSTION_OUT:
            r["status"] = "out"
            events.append(_say(state, "out", f"{r['name']} collapses, exhausted, and drops out", who=r["name"]))
        if reached is not None and r["status"] == "in":
            if reached.get("acted"):
                reached["status"] = "caught"
                events.append(_say(state, "caught", f"{r['name']} catches {reached['name']}",
                                   who=r["name"], quarry=reached["name"]))
            else:
                events.append(_say(state, "heels", f"{r['name']} is on {reached['name']}'s heels, who has not run yet",
                                   who=r["name"], quarry=reached["name"]))
    if r["side"] == "quarry":
        r["acted"] = True
    if r["status"] == "in":
        events += _complication(state, r, rng)
    events += _maybe_over(state)
    if not state["over"]:
        events += _advance(state, rng)
    return events


def _advance(state: dict, rng: random.Random) -> list:
    events = []
    i = state["turn"]
    nxt = _next_index(state, i)
    if nxt is None:
        state["over"] = True
        return [_say(state, "over.quarry", "the chase is over")]
    if nxt <= i:                              # the order wrapped: the round ends
        events += _escapes(state, rng)
        events += _maybe_over(state)
        state["round"] += 1
        state["sight"] = True
        if state["over"]:
            return events
        nxt = _next_index(state, -1)          # the first one still running
        if nxt is None:
            state["over"] = True
            return events
    state["turn"] = nxt
    return events


def adjust(state: dict, name: str, metres: float) -> dict:
    r = find(state, name)
    if not -60 <= metres <= 60:
        raise ChaseError("adjust by -60 to +60 metres")
    r["pos"] = round(max(0.0, r["pos"] + metres), 1)
    ev = _say(state, "adjust", f"{r['name']} {'+' if metres >= 0 else ''}{metres:g} m", who=r["name"], m=f"{metres:+g}")
    _caught(state, r)
    _maybe_over(state)
    return ev


def mark(state: dict, name: str, status: str) -> dict:
    """The DM settles a PC quarry's escape roll, or takes someone out: escaped | caught | out | in."""
    r = find(state, name)
    if status not in ("escaped", "caught", "out", "in"):
        raise ChaseError("escaped, caught, out or in")
    r["status"] = status
    ev = _say(state, "mark." + status, f"{r['name']}: {status}", who=r["name"])
    _maybe_over(state)
    if not state["over"] and current(state) is not None and current(state)["status"] != "in":
        _advance(state, random.Random())
    return ev


# ─── What the screens see, what the DM is told ─────────────────────────────────

def public_view(state: dict | None) -> dict | None:
    if not state:
        return None
    cur = current(state)
    rows = []
    for r in state["runners"]:
        rows.append({"name": r["name"], "side": r["side"], "pc": r["pc"], "pos": r["pos"], "status": r["status"],
                     "speed": speed_now(r), "dashes": r["dashes"], "free": r["free"], "exhaustion": r["exhaustion"],
                     "gap": gap_of(state, r), "complication": r.get("complication")})
    return {"id": state["id"], "setting": state["setting"], "round": state["round"], "over": state["over"],
            "turn": cur["name"] if cur else "", "sight": state["sight"], "cover": state["cover"], "runners": rows,
            "log": list(reversed(state["log"][-10:]))}


def format_note(events: list, state: dict) -> str:
    cur = current(state)
    tail = ("The chase is over." if state["over"] else
            f"Now {cur['name']}'s turn" + ("" if cur["pc"] else f": chase.py act \"{cur['name']}\" move|dash|stop") + ".")
    return "[[CHASE: " + "; ".join(e["text"] for e in events) + ". " + tail + "]]"


def notes_add(line: str) -> None:
    try:
        with open(NOTES_FILE, encoding="utf-8") as f:
            notes = json.load(f)
        if not isinstance(notes, list):
            notes = []
    except (OSError, ValueError):
        notes = []
    notes.append(line)
    try:
        with open(NOTES_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(notes, f, ensure_ascii=False)
        os.replace(NOTES_FILE + ".tmp", NOTES_FILE)
    except OSError:
        pass


def notes_pending() -> bool:
    return os.path.exists(NOTES_FILE)


def drain_notes() -> list:
    try:
        with open(NOTES_FILE, encoding="utf-8") as f:
            notes = json.load(f)
        os.remove(NOTES_FILE)
    except (OSError, ValueError):
        return []
    return [n for n in notes if isinstance(n, str)]


# ─── Who runs: sheets and the SRD ─────────────────────────────────────────────

_SPEED_RE = re.compile(r"\*\*Speed:\*\*\s*(\d+(?:[.,]\d+)?)\s*(ft|feet|piedi|m)?", re.I)
_PASSIVE_RE = re.compile(r"Passive Perception:\**\s*(\d+)", re.I)


def _ability_mod(text: str, names: tuple) -> int:
    m = re.search(r"^\|\s*(?:STR|FOR)\s*\|.*$", text, re.M | re.I)
    if not m:
        return 0
    rows = [ln for ln in text[m.start():].splitlines() if ln.strip().startswith("|")][:3]
    if len(rows) < 3:
        return 0
    head = [c.strip().upper() for c in rows[0].strip().strip("|").split("|")]
    vals = [c.strip() for c in rows[2].strip().strip("|").split("|")]
    for n in names:
        if n in head and head.index(n) < len(vals):
            mod = re.search(r"\(\s*([+-−]?\d+)\s*\)", vals[head.index(n)])
            if mod:
                return int(mod.group(1).replace("−", "-"))
            score = re.match(r"\d+", vals[head.index(n)])
            if score:
                return (int(score.group()) - 10) // 2
    return 0


def from_sheet(name: str, campaign: str = "") -> dict:
    import inventory
    path = inventory.find_sheet(name, campaign)
    text = inventory._read(path)
    sp = _SPEED_RE.search(text)
    speed = 9.0
    if sp:
        value = float(sp.group(1).replace(",", "."))
        speed = value if (sp.group(2) or "").lower() == "m" else round(value * 0.3, 1)
    dex = _ability_mod(text, ("DEX", "DES"))
    stealth = dex
    m = re.search(r"^\|\s*(?:Stealth|Furtività)\s*\|[^\n]*?([+-−]\d+)", text, re.M | re.I)
    if m:
        stealth = int(m.group(1).replace("−", "-"))
    passive = _PASSIVE_RE.search(text)
    return runner(inventory.display_name(path), "", speed, _ability_mod(text, ("CON", "COS")), dex, stealth,
                  int(passive.group(1)) if passive else 10, pc=True)


def from_spec(spec: str) -> list:
    """'Ladro:speed=9,con=1' / 'Thug:2' / 'Guardia' → creatures (the SRD fills what it knows)."""
    name, _, rest = spec.partition(":")
    name = name.strip()
    opts, count = {}, 1
    for part in [p.strip() for p in rest.split(",") if p.strip()]:
        if part.isdigit():
            count = max(1, min(12, int(part)))
            continue
        k, _, v = part.partition("=")
        try:
            opts[k.strip().lower()] = float(v.replace(",", "."))
        except ValueError:
            raise ChaseError(f"{part!r}: write key=number (speed, con, dex, stealth, passive)")
    base = {"speed": 9.0, "con": 0, "dex": 0, "stealth": None, "passive": 10}
    try:
        import encounter
        rec = encounter.find_monster(name, encounter.load_monsters())
        walk = re.search(r"walk\s*(\d+)\s*ft", str(rec.get("speed") or "")) or re.search(r"(\d+)\s*ft", str(rec.get("speed") or ""))
        base["speed"] = round(int(walk.group(1)) * 0.3, 1) if walk else 9.0
        base["con"] = (int(rec.get("con", 10)) - 10) // 2
        base["dex"] = (int(rec.get("dex", 10)) - 10) // 2
        passive = re.search(r"passive Perception\s*(\d+)", str(rec.get("senses") or ""), re.I)
        if passive:
            base["passive"] = int(passive.group(1))
    except Exception:
        pass
    base.update({k: v for k, v in opts.items() if k in base})
    names = [name] if count == 1 else [f"{name} {i}" for i in range(1, count + 1)]
    return [runner(n, "", base["speed"], int(base["con"]), int(base["dex"]),
                   None if base["stealth"] is None else int(base["stealth"]), int(base["passive"])) for n in names]


def _party() -> list:
    try:
        with open(rt("stats.json"), encoding="utf-8") as f:
            return [str(p.get("name")) for p in (json.load(f).get("players") or []) if p.get("name")]
    except (OSError, ValueError, AttributeError):
        return []


def resolve(names: str, campaign: str = "") -> list:
    """A comma-separated list: characters with a sheet become PCs, the rest creatures."""
    out = []
    party = {_key(n) for n in _party()}
    import inventory
    specs = []
    for part in [s.strip() for s in (names or "").split(",") if s.strip()]:
        if specs and ":" in specs[-1] and ":" not in part and "=" in part:
            specs[-1] += "," + part           # 'Ladro:speed=9,con=1': the keys belong to the creature before
        else:
            specs.append(part)
    for spec in specs:
        bare = spec.partition(":")[0].strip()
        if ":" not in spec:
            try:
                out.append(from_sheet(bare, campaign))
                continue
            except inventory.InventoryError:
                if _key(bare) in party:
                    raise ChaseError(f"{bare} is at the table but has no sheet")
        out.extend(from_spec(spec))
    return out


# ─── Command line ─────────────────────────────────────────────────────────────

def _request(path: str, body: dict | None = None):
    import ssl
    import urllib.error
    import urllib.request
    import send
    headers = {"Content-Type": "application/json"}
    token = send._read_token()
    if token:
        headers["X-DND-Token"] = token
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(send.BASE_URL + path, data=data, headers=headers,
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=5, context=send._SSL_CTX) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except (OSError, ssl.SSLError):
        return 0, None
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, None


def describe(view: dict | None) -> str:
    if not view:
        return "No chase."
    lines = [f"Chase ({view['setting']}), round {view['round']}" + (" — OVER" if view["over"] else f", {view['turn']}'s turn")
             + ("" if view["sight"] else " — the quarry is out of sight") + f", cover {view['cover']}"]
    for r in sorted(view["runners"], key=lambda r: -r["pos"]):
        bits = [f"{r['pos']:g} m", f"speed {r['speed']:g}", f"dashes {r['dashes']}/{r['free']} free"]
        if r["exhaustion"]:
            bits.append(f"exhaustion {r['exhaustion']}")
        if r["gap"] is not None:
            bits.append(f"gap {r['gap']:g} m")
        if r["status"] != "in":
            bits.append(r["status"].upper())
        if r.get("complication"):
            bits.append(f"complication: {r['complication']['check']} / {r['complication']['fail']}")
        lines.append(f"  {'Q' if r['side'] == 'quarry' else 'P'} {r['name']}: " + ", ".join(bits))
    return "\n".join(lines)


def main(argv: list | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    ap = argparse.ArgumentParser(description="A chase on the players' phones.")
    ap.add_argument("--campaign", default="", help="campaign the sheets are read from (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("start")
    p.add_argument("--quarry", required=True)
    p.add_argument("--pursuers", required=True)
    p.add_argument("--gap", type=float, required=True, help="the quarry's lead in metres")
    p.add_argument("--setting", default="urban", choices=SETTINGS)
    p = sub.add_parser("act", help="a creature's turn")
    p.add_argument("name")
    p.add_argument("action", choices=["move", "dash", "stop"])
    p = sub.add_parser("adjust")
    p.add_argument("name")
    p.add_argument("metres", type=float)
    p = sub.add_parser("mark", help="escaped | caught | out | in")
    p.add_argument("name")
    p.add_argument("status", choices=["escaped", "caught", "out", "in"])
    p = sub.add_parser("sight")
    p.add_argument("state", choices=["on", "off"])
    p = sub.add_parser("cover")
    p.add_argument("kind", choices=COVERS)
    sub.add_parser("show")
    sub.add_parser("end")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "start":
            import media
            camp = media.safe_campaign(args.campaign) if args.campaign else (media.active_campaign() or "")
            body = {"op": "start", "quarry": resolve(args.quarry, camp), "pursuers": resolve(args.pursuers, camp),
                    "gap": args.gap, "setting": args.setting}
        elif args.cmd == "show":
            body = None
        elif args.cmd == "end":
            body = {"op": "end"}
        elif args.cmd == "act":
            body = {"op": "act", "name": args.name, "action": args.action}
        elif args.cmd == "adjust":
            body = {"op": "adjust", "name": args.name, "metres": args.metres}
        elif args.cmd == "mark":
            body = {"op": "mark", "name": args.name, "status": args.status}
        elif args.cmd == "sight":
            body = {"op": "sight", "on": args.state == "on"}
        else:
            body = {"op": "cover", "cover": args.kind}
    except ChaseError as e:
        print(f"chase: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    status, out = _request("/chase", body)
    if status != 200 or not isinstance(out, dict):
        why = out.get("error") if isinstance(out, dict) else ""
        print("chase: " + ("the display is not running" if status == 0 else f"HTTP {status}{': ' + why if why else ''}")
              + ". Nothing was changed.", file=sys.stderr)
        return 1
    for ev in out.get("events") or []:
        print(ev)
    print(describe(out.get("chase")))
    if args.cmd == "start":
        fast = [p["name"] for p in body["pursuers"] if p["speed"] * 2 >= args.gap - REACH_M]
        if fast:
            print(f"⚠ {', '.join(fast)} can cover the {args.gap:g} m lead in one dash: whoever has not run yet "
                  "only has a pursuer on their heels, but the chase will be short — a bigger --gap gives it room.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
