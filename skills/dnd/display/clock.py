#!/usr/bin/env python3
"""clock.py — progress clocks the whole table can watch fill up.

A clock is a circle cut in segments: a ritual nearing completion, the guards
closing in, a bridge about to give, the hours left before the tide. Fill a
segment when the thing gets closer; when the last one fills, it happens.

    clock.py add "Il rito della Confraternita" 6 --kind danger --note "alla luna piena"
    clock.py add "Scassinare il caveau" 4 --kind goal
    clock.py add "I rinforzi arrivano" 8 --secret          # yours alone until you reveal it
    clock.py tick "rito"            # one more segment (tick "rito" 2 for two)
    clock.py back "rito"            # the party bought time
    clock.py set "rito" 5
    clock.py reveal "rinforzi"      # the players see it from now on (hide puts it back)
    clock.py remove "caveau"
    clock.py list
    clock.py clear                  # all of them

Kinds: danger (something bad is coming), goal (the party's own progress),
time (a plain countdown). Italian words are understood (pericolo, obiettivo,
tempo). A name can be shortened to any part that matches one clock only.

Clocks live in <campaign>/clocks.json and stay from one session to the next.
Every screen shows the public ones — small in the top bar, large in the
Clocks panel — and is told when one moves. A secret clock never leaves this
machine. Anything that does not match exits non-zero and changes nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

KINDS = ("danger", "goal", "time")
KIND_ALIASES = {"pericolo": "danger", "minaccia": "danger", "threat": "danger",
                "obiettivo": "goal", "progresso": "goal", "progress": "goal",
                "tempo": "time", "countdown": "time", "conto": "time"}
MIN_SEGMENTS, MAX_SEGMENTS = 2, 12
MAX_CLOCKS = 12


class ClockError(Exception):
    """A request that cannot be applied; the clocks are left as they were."""


# ─── Data ─────────────────────────────────────────────────────────────────────

def _campaign_dir(campaign: str = ""):
    import media
    camp = media.safe_campaign(campaign) if campaign else media.active_campaign()
    if not camp:
        raise ClockError("no active campaign — pass --campaign or load one first")
    return media.find_campaign(camp)


def data_path(campaign: str = "") -> str:
    return str(_campaign_dir(campaign) / "clocks.json")


def load(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        return []
    except ValueError as e:
        raise ClockError(f"clocks.json is not valid JSON ({e})")
    clocks = data.get("clocks") if isinstance(data, dict) else data
    return [c for c in clocks or [] if isinstance(c, dict) and c.get("name")]


def save(path: str, clocks: list) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"clocks": clocks}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _key(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def find(clocks: list, name: str) -> dict:
    """The clock `name` means: exact name first, else a unique part of it."""
    want = _key(name)
    if not want:
        raise ClockError("which clock?")
    hits = [c for c in clocks if _key(c["name"]) == want] or [c for c in clocks if want in _key(c["name"])]
    if not hits:
        known = ", ".join(c["name"] for c in clocks) or "none yet"
        raise ClockError(f"no clock called {name!r} (clocks: {known})")
    if len(hits) > 1:
        raise ClockError(f"{name!r} matches several clocks: " + ", ".join(c["name"] for c in hits))
    return hits[0]


def is_full(clock: dict) -> bool:
    return clock["filled"] >= clock["segments"]


# ─── Operations ───────────────────────────────────────────────────────────────

def add(clocks: list, name: str, segments: int, filled: int = 0, kind: str = "", note: str = "",
        secret: bool = False) -> dict:
    name = " ".join((name or "").split())[:60]
    if not name:
        raise ClockError("a clock needs a name")
    if any(_key(c["name"]) == _key(name) for c in clocks):
        raise ClockError(f"there is already a clock called {name!r} (tick it, or remove it first)")
    if len(clocks) >= MAX_CLOCKS:
        raise ClockError(f"{MAX_CLOCKS} clocks are running already — remove the ones that are over")
    if not MIN_SEGMENTS <= segments <= MAX_SEGMENTS:
        raise ClockError(f"a clock has {MIN_SEGMENTS} to {MAX_SEGMENTS} segments, not {segments}")
    if not 0 <= filled <= segments:
        raise ClockError(f"{filled} filled does not fit a clock of {segments}")
    k = (kind or "time").strip().lower()
    k = KIND_ALIASES.get(k, k)
    if k not in KINDS:
        raise ClockError(f"a clock is one of {', '.join(KINDS)} — not {kind!r}")
    clock = {"id": secrets.token_hex(4), "name": name, "segments": segments, "filled": filled, "kind": k,
             "note": " ".join((note or "").split())[:200], "secret": bool(secret), "ts": time.time()}
    clocks.append(clock)
    return clock


def move(clocks: list, name: str, by: int = 0, to: int | None = None) -> tuple:
    """Fill or empty segments. Returns (clock, change actually made)."""
    clock = find(clocks, name)
    before = clock["filled"]
    target = before + by if to is None else to
    if to is not None and not 0 <= to <= clock["segments"]:
        raise ClockError(f"{clock['name']} has {clock['segments']} segments; {to} does not fit")
    clock["filled"] = max(0, min(clock["segments"], target))
    return clock, clock["filled"] - before


def remove(clocks: list, name: str) -> dict:
    clock = find(clocks, name)
    clocks.remove(clock)
    return clock


def public_view(clocks: list) -> list:
    """What the players' screens get: every clock that is not secret."""
    return [{"id": c.get("id", ""), "name": c["name"], "segments": c["segments"], "filled": c["filled"],
             "kind": c.get("kind", "time"), "note": c.get("note", ""), "full": is_full(c)}
            for c in clocks if not c.get("secret")]


def describe(clock: dict) -> str:
    bar = "●" * clock["filled"] + "○" * (clock["segments"] - clock["filled"])
    tags = [clock.get("kind", "time")] + (["secret"] if clock.get("secret") else []) + (["FULL"] if is_full(clock) else [])
    note = f" — {clock['note']}" if clock.get("note") else ""
    return f"{clock['name']}: {bar} {clock['filled']}/{clock['segments']}  [{', '.join(tags)}]{note}"


def _announce(event: dict | None = None) -> None:
    """Tell the display the clocks changed. Silent when it is not running."""
    try:
        import send
        send._post(f"{send.BASE_URL}/clocks", json.dumps({"event": event} if event else {}).encode("utf-8"),
                   send._read_token())
    except Exception:
        pass   # the display is off: the clocks are saved and will be there next time


def _event(clock: dict, delta: int = 0, new: bool = False) -> dict | None:
    if clock.get("secret"):
        return None
    return {"id": clock["id"], "name": clock["name"], "filled": clock["filled"], "segments": clock["segments"],
            "delta": delta, "full": is_full(clock), "new": new}


# ─── Command line ─────────────────────────────────────────────────────────────

def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="Progress clocks the whole table can watch.")
    ap.add_argument("--campaign", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add", help="start a clock")
    p.add_argument("name")
    p.add_argument("segments", type=int)
    p.add_argument("--filled", type=int, default=0)
    p.add_argument("--kind", default="", help="danger, goal or time (default)")
    p.add_argument("--note", default="")
    p.add_argument("--secret", action="store_true", help="the players do not see it until `reveal`")
    for cmd, text in (("tick", "fill segments"), ("back", "empty segments")):
        p = sub.add_parser(cmd, help=text)
        p.add_argument("name")
        p.add_argument("n", type=int, nargs="?", default=1)
    p = sub.add_parser("set", help="set how many segments are filled")
    p.add_argument("name")
    p.add_argument("filled", type=int)
    for cmd in ("reveal", "hide", "remove"):
        sub.add_parser(cmd).add_argument("name")
    sub.add_parser("list", help="every clock, the secret ones too")
    sub.add_parser("clear", help="remove all the clocks")
    args = ap.parse_args(argv)

    try:
        path = data_path(args.campaign)
        clocks = load(path)
        event = None
        if args.cmd == "list":
            print("\n".join(describe(c) for c in clocks) or "no clocks running")
            return 0
        if args.cmd == "add":
            clock = add(clocks, args.name, args.segments, args.filled, args.kind, args.note, args.secret)
            event = _event(clock, new=True)
            print("+ " + describe(clock))
        elif args.cmd in ("tick", "back", "set"):
            if args.cmd != "set" and args.n < 1:
                raise ClockError("by how many segments? (a positive number)")
            clock, delta = (move(clocks, args.name, to=args.filled) if args.cmd == "set"
                            else move(clocks, args.name, by=args.n if args.cmd == "tick" else -args.n))
            event = _event(clock, delta) if delta else None
            print(describe(clock))
            if delta > 0 and is_full(clock):
                print("  ★ FULL — what it was counting down to happens now. Narrate it; remove the clock when it is over.")
            elif not delta:
                print("  (it was already there — nothing moved)")
        elif args.cmd in ("reveal", "hide"):
            clock = find(clocks, args.name)
            clock["secret"] = args.cmd == "hide"
            event = _event(clock, new=True)
            print(describe(clock))
        elif args.cmd == "remove":
            print("- " + remove(clocks, args.name)["name"])
        elif args.cmd == "clear":
            print(f"removed {len(clocks)} clock(s)")
            clocks = []
        save(path, clocks)
        _announce(event)
    except ClockError as e:
        print(f"clock: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
