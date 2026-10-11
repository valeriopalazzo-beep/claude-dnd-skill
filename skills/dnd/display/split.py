#!/usr/bin/env python3
"""split.py — the party is in more than one place.

When the characters separate, each player should read the scene their own
character is in, and not the others'. This keeps who is with whom; send.py
then addresses a piece of narration to one group and the display shows it
only to those players (the DM's screen shows everything).

    split.py set "Collina: Hernio, Lyra" "Cappella: Lio, Vardamir" "Rocca: Ajeje"
    split.py move "Ajeje" "Collina"          # someone reaches another group — or goes off alone: move "Lio" "Sentiero"
    split.py rename "Rocca" "Scala della Rocca"
    split.py show
    split.py join                            # everyone is together again

    send.py --group Collina << 'DNDEND'      # only Hernio and Lyra read this
    I morti si voltano tutti insieme verso la montagna.
    DNDEND
    send.py --only "Ajeje" --npc "L'adepto" << 'DNDEND'      # or name the characters, split or not
    «La Custode ti vuole vedere. Giù. Subito.»
    DNDEND

A group is a name (usually the place) and its characters, written by any part
of their name that means one character only. What is sent with no --group and
no --only is for the whole table, as always. A character left out of every
group reads only that.

The groups live in <campaign>/split.json, so a session that ends with the
party divided starts again the same way. Anything that does not match exits
non-zero and changes nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

MAX_GROUPS = 8
NAME = 40


class SplitError(Exception):
    """A request that cannot be applied; the groups are left as they were."""


# ─── Data ─────────────────────────────────────────────────────────────────────

def _campaign(campaign: str = "") -> str:
    import media
    camp = media.safe_campaign(campaign) if campaign else media.active_campaign()
    if not camp:
        raise SplitError("no active campaign — pass --campaign or load one first")
    return camp


def data_path(campaign: str = "") -> str:
    import media
    return str(media.find_campaign(_campaign(campaign)) / "split.json")


def load(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        return []
    except ValueError as e:
        raise SplitError(f"split.json is not valid JSON ({e})")
    rows = data.get("groups") if isinstance(data, dict) else data
    return [{"name": str(g["name"]), "members": [str(m) for m in g.get("members") or [] if m]}
            for g in rows or [] if isinstance(g, dict) and g.get("name") and g.get("members")]


def save(path: str, groups: list) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"groups": groups, "ts": time.time()}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _key(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def party(campaign: str) -> list:
    """The campaign's player characters, by the name on their sheet."""
    import media
    folder = media.find_campaign(campaign) / "characters"
    names = []
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        try:
            first = path.read_text(encoding="utf-8", errors="replace").splitlines()[0]
        except (OSError, IndexError):
            continue
        name = first[2:].strip() if first.startswith("# ") else path.stem
        if name:
            names.append(name)
    return names


def character(names: list, text: str) -> str:
    """The character `text` means: the exact name first, else a part only one has."""
    want = _key(text)
    if not want:
        raise SplitError("which character?")
    hits = [n for n in names if _key(n) == want] or [n for n in names if want in _key(n)]
    if not hits:
        raise SplitError(f"no character called {text!r} (party: {', '.join(names) or 'nobody'})")
    if len(hits) > 1:
        raise SplitError(f"{text!r} matches several characters: " + ", ".join(hits))
    return hits[0]


def characters(names: list, text: str) -> list:
    """'Hernio, Lyra' → the two sheet names, each once."""
    out = []
    for part in (text or "").replace(";", ",").split(","):
        if part.strip():
            name = character(names, part)
            if name not in out:
                out.append(name)
    if not out:
        raise SplitError("which characters?")
    return out


def group(groups: list, name: str) -> dict:
    """The group `name` means: the exact name first, else a part only one has."""
    want = _key(name)
    if not want:
        raise SplitError("which group?")
    hits = [g for g in groups if _key(g["name"]) == want] or [g for g in groups if want in _key(g["name"])]
    if not hits:
        known = ", ".join(g["name"] for g in groups)
        raise SplitError(f"no group called {name!r}" + (f" (groups: {known})" if known else " — the party is not split"))
    if len(hits) > 1:
        raise SplitError(f"{name!r} matches several groups: " + ", ".join(g["name"] for g in hits))
    return hits[0]


# ─── Operations ───────────────────────────────────────────────────────────────

def _group_name(text: str) -> str:
    name = " ".join((text or "").split())[:NAME]
    if not name:
        raise SplitError("a group needs a name (usually where it is)")
    return name


def split(names: list, specs: list) -> list:
    """['Collina: Hernio, Lyra', 'Rocca: Ajeje'] → the groups. A character is in one group only."""
    groups, taken = [], {}
    for spec in specs:
        if ":" not in spec:
            raise SplitError(f'write a group as "Place: Character, Character" (got {spec!r})')
        label, _, members = spec.partition(":")
        label = _group_name(label)
        if any(_key(g["name"]) == _key(label) for g in groups):
            raise SplitError(f"two groups are called {label!r}")
        people = characters(names, members)
        for person in people:
            if person in taken:
                raise SplitError(f"{person} is in {taken[person]} and in {label}")
            taken[person] = label
        groups.append({"name": label, "members": people})
    if len(groups) < 2:
        raise SplitError("a split needs at least two groups (join puts everyone together)")
    if len(groups) > MAX_GROUPS:
        raise SplitError(f"at most {MAX_GROUPS} groups")
    return groups


def move(groups: list, names: list, who: str, where: str) -> tuple:
    """Put a character in another group, new or not. Returns (groups, character, group name)."""
    if not groups:
        raise SplitError("the party is not split — use set first")
    person = character(names, who)
    try:
        target = group(groups, where)["name"]
    except SplitError as e:
        if "matches several" in str(e):
            raise
        target = _group_name(where)
    out = []
    for g in groups:
        members = [m for m in g["members"] if m != person]
        if _key(g["name"]) == _key(target):
            members.append(person)
        if members:
            out.append({"name": g["name"], "members": members})
    if not any(_key(g["name"]) == _key(target) for g in out):
        out.append({"name": target, "members": [person]})
    if len(out) > MAX_GROUPS:
        raise SplitError(f"at most {MAX_GROUPS} groups")
    return out, person, target


def left_out(groups: list, names: list) -> list:
    inside = {m for g in groups for m in g["members"]}
    return [n for n in names if n not in inside] if groups else []


def public_view(groups: list) -> list:
    return [{"name": g["name"], "members": list(g["members"])} for g in groups]


def audience(campaign: str, group_name: str = "", only: str = "") -> tuple:
    """Who a piece of narration is for: (characters, label). send.py calls this for --group / --only."""
    if group_name and only:
        raise SplitError("give --group or --only, not both")
    if group_name:
        g = group(load(data_path(campaign)), group_name)
        return list(g["members"]), g["name"]
    return characters(party(campaign), only), ""


def describe(groups: list, names: list) -> str:
    if not groups:
        return "the party is together"
    lines = [f"{g['name']}: {', '.join(g['members'])}" for g in groups]
    out = left_out(groups, names)
    if out:
        lines.append(f"in no group (they read only what is sent to everyone): {', '.join(out)}")
    return "\n".join(lines)


def _announce(event: str = "") -> None:
    """Tell the display who is with whom. Silent when it is not running."""
    try:
        import send
        send._post(f"{send.BASE_URL}/split", json.dumps({"event": event}).encode("utf-8"), send._read_token())
    except Exception:
        pass   # the display is off: the groups are saved and will be there next time


# ─── Command line ─────────────────────────────────────────────────────────────

def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="The party is in more than one place.")
    ap.add_argument("--campaign", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("set", help="the party splits (replaces any split there was)")
    p.add_argument("groups", nargs="+", help='"Place: Character, Character"')
    p = sub.add_parser("move", help="a character goes to another group, or off alone")
    p.add_argument("character")
    p.add_argument("group")
    p = sub.add_parser("rename", help="a group moves on, or gets a better name")
    p.add_argument("group")
    p.add_argument("new_name")
    sub.add_parser("join", help="everyone is together again")
    sub.add_parser("show")
    args = ap.parse_args(argv)

    try:
        import media
        camp = _campaign(args.campaign)
        path = data_path(camp)
        groups = load(path)
        names = party(camp)
        event = ""
        if args.cmd == "show":
            print(describe(groups, names))
            return 0
        if args.cmd == "set":
            groups = split(names, args.groups)
            event = "split"
        elif args.cmd == "move":
            groups, person, where = move(groups, names, args.character, args.group)
            event = "moved"
            if len(groups) < 2:
                groups, event = [], "joined"       # the last two groups met: nobody is apart any more
        elif args.cmd == "rename":
            target = group(groups, args.group)
            new_name = _group_name(args.new_name)
            if any(g is not target and _key(g["name"]) == _key(new_name) for g in groups):
                raise SplitError(f"another group is called {new_name!r}")
            target["name"] = new_name
            event = "moved"
        elif args.cmd == "join":
            if not groups:
                raise SplitError("the party is together already")
            groups, event = [], "joined"
        save(path, groups)
        print(describe(groups, names))
        if camp == media.active_campaign():
            _announce(event)
    except SplitError as e:
        print(f"split: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
