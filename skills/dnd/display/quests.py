#!/usr/bin/env python3
"""quests.py — the party's quests, kept with the campaign.

The Quests panel used to be a list pushed by hand at every load and lost with
the display's memory. Here the quests live in <campaign>/quests.json: change
one and every screen follows; load the campaign and they are back.

    quests.py add "La veglia di Oderico" --giver "Padre Oderico" --location "Cimitero della Collina" \
        --summary "Vegliare una notte con il prete sulla Collina." --reward "Cose mai dette a nessuno" \
        --objective "Vegliare una notte sulla Collina" --clue "Si alzano solo i morti col martellino"
    quests.py add "Il sesto nome" --status threat --deadline "Luna piena: la notte del 16"
    quests.py set "veglia" --summary "…" --deadline "Stanotte"      # "" empties a field; --name renames
    quests.py objective "veglia" "Leggere i registri col martello"  # one more step
    quests.py done "veglia" "vegliare"                              # tick a step (undo: untick)
    quests.py clue "veglia" "Stanotte ci sarà anche Ser Bertrand"   # one more lead
    quests.py drop "veglia" --objective "registri"                  # or --clue: take one away
    quests.py resolve "veglia"                                      # also: fail, reopen, threat
    quests.py remove "veglia"
    quests.py list
    quests.py show "veglia"
    quests.py push                    # send the list to the display again (after a --clear)
    quests.py import                  # take in the quests the display holds (pushed by hand before)

Write only what the party knows: every player reads this panel. A quest can be
named by any part of its name that means one quest only. Anything that does
not match exits non-zero and changes nothing.
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
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

STATUSES = ("active", "threat", "resolved", "failed")
OPEN = ("active", "threat")
FIELDS = ("summary", "giver", "location", "reward", "deadline")
LIMITS = {"name": 80, "summary": 600, "giver": 80, "location": 120, "reward": 160, "deadline": 120}
MAX_LINES = 40          # objectives or clues in one quest
LINE = 240


class QuestError(Exception):
    """A request that cannot be applied; the quests are left as they were."""


# ─── Data ─────────────────────────────────────────────────────────────────────

def _campaign(campaign: str = "") -> str:
    import media
    camp = media.safe_campaign(campaign) if campaign else media.active_campaign()
    if not camp:
        raise QuestError("no active campaign — pass --campaign or load one first")
    return camp


def data_path(campaign: str = "") -> str:
    import media
    return str(media.find_campaign(_campaign(campaign)) / "quests.json")


def load(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        return []
    except ValueError as e:
        raise QuestError(f"quests.json is not valid JSON ({e})")
    rows = data.get("quests") if isinstance(data, dict) else data
    return [q for q in rows or [] if isinstance(q, dict) and q.get("name")]


def save(path: str, quests: list) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"quests": quests}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _key(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def _clean(text: str, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def find(quests: list, name: str) -> dict:
    """The quest `name` means: its exact name first, else a part only one quest has."""
    want = _key(name)
    if not want:
        raise QuestError("which quest?")
    hits = [q for q in quests if _key(q["name"]) == want] or [q for q in quests if want in _key(q["name"])]
    if not hits:
        known = ", ".join(q["name"] for q in quests) or "none yet"
        raise QuestError(f"no quest called {name!r} (quests: {known})")
    if len(hits) > 1:
        raise QuestError(f"{name!r} matches several quests: " + ", ".join(q["name"] for q in hits))
    return hits[0]


def _find_line(lines: list, part: str, what: str, text=lambda x: x) -> int:
    want = _key(part)
    if not want:
        raise QuestError(f"which {what}?")
    hits = [i for i, ln in enumerate(lines) if _key(text(ln)) == want] or \
           [i for i, ln in enumerate(lines) if want in _key(text(ln))]
    if not hits:
        raise QuestError(f"no {what} like {part!r}")
    if len(hits) > 1:
        raise QuestError(f"{part!r} matches several {what}s: " + " | ".join(text(lines[i]) for i in hits))
    return hits[0]


# ─── Operations ───────────────────────────────────────────────────────────────

def _status(value: str) -> str:
    value = (value or "active").strip().lower()
    if value not in STATUSES:
        raise QuestError(f"status is one of {', '.join(STATUSES)} — not {value!r}")
    return value


def add(quests: list, name: str, status: str = "active", objectives: list | None = None,
        clues: list | None = None, **fields) -> dict:
    name = _clean(name, LIMITS["name"])
    if not name:
        raise QuestError("a quest needs a name")
    if any(_key(q["name"]) == _key(name) for q in quests):
        raise QuestError(f"there is a quest called {name!r} already — change it with set, objective, clue")
    quest = {"id": secrets.token_hex(4), "name": name, "status": _status(status),
             "objectives": [], "clues": [], "ts": time.time()}
    for field in FIELDS:
        quest[field] = _clean(fields.get(field, ""), LIMITS[field])
    for text in objectives or []:
        add_objective(quest, text)
    for text in clues or []:
        add_clue(quest, text)
    quests.append(quest)
    return quest


def change(quests: list, quest: dict, name: str | None = None, status: str | None = None, **fields) -> list:
    """Set what was given (None = leave alone, "" = empty it). Returns what changed."""
    changed = []
    if name is not None:
        name = _clean(name, LIMITS["name"])
        if not name or any(q is not quest and _key(q["name"]) == _key(name) for q in quests):
            raise QuestError(f"cannot rename to {name!r}: empty, or another quest has that name")
        if name != quest["name"]:
            quest["name"] = name
            changed.append("name")
    if status is not None and _status(status) != quest.get("status"):
        quest["status"] = _status(status)
        changed.append("status")
    for field in FIELDS:
        if fields.get(field) is not None:
            value = _clean(fields[field], LIMITS[field])
            if value != quest.get(field, ""):
                quest[field] = value
                changed.append(field)
    return changed


def add_objective(quest: dict, text: str, done: bool = False) -> bool:
    text = _clean(text, LINE)
    if not text:
        raise QuestError("an objective needs words")
    rows = quest.setdefault("objectives", [])
    if any(_key(o["text"]) == _key(text) for o in rows):
        return False
    if len(rows) >= MAX_LINES:
        raise QuestError(f"{quest['name']} has {MAX_LINES} objectives already")
    rows.append({"text": text, "done": bool(done)})
    return True


def mark(quest: dict, part: str, done: bool = True) -> dict:
    rows = quest.get("objectives") or []
    row = rows[_find_line(rows, part, "objective", lambda o: o["text"])]
    row["done"] = bool(done)
    return row


def add_clue(quest: dict, text: str) -> bool:
    text = _clean(text, LINE)
    if not text:
        raise QuestError("a clue needs words")
    rows = quest.setdefault("clues", [])
    if any(_key(c) == _key(text) for c in rows):
        return False
    if len(rows) >= MAX_LINES:
        raise QuestError(f"{quest['name']} has {MAX_LINES} clues already")
    rows.append(text)
    return True


def drop(quest: dict, objective: str = "", clue: str = "") -> str:
    if bool(objective) == bool(clue):
        raise QuestError("drop what? give --objective or --clue (one of them)")
    if objective:
        rows = quest.get("objectives") or []
        return rows.pop(_find_line(rows, objective, "objective", lambda o: o["text"]))["text"]
    rows = quest.get("clues") or []
    return rows.pop(_find_line(rows, clue, "clue"))


def take_in(quests: list, rows: list) -> list:
    """Add the display's quests this file does not have yet. Returns their names."""
    new = []
    for row in rows or []:
        if not isinstance(row, dict) or not _clean(row.get("name", ""), LIMITS["name"]):
            continue
        if any(_key(q["name"]) == _key(row["name"]) for q in quests):
            continue
        status = str(row.get("status") or "active").lower()
        quest = add(quests, row["name"], status if status in STATUSES else "active",
                    **{f: row.get(f, "") for f in FIELDS})
        for o in row.get("objectives") or []:
            if isinstance(o, dict) and _clean(o.get("text", ""), LINE):
                add_objective(quest, o["text"], bool(o.get("done")))
            elif isinstance(o, str) and _clean(o, LINE):
                add_objective(quest, o)
        for c in row.get("clues") or []:
            if _clean(c, LINE):
                add_clue(quest, c)
        new.append(quest["name"])
    return new


def public_view(quests: list) -> list:
    """What the Quests panel gets: open quests first, in the order they were taken on."""
    out = []
    for q in sorted(quests, key=lambda q: q.get("status") not in OPEN):
        row = {"name": q["name"], "status": q.get("status") if q.get("status") in STATUSES else "active"}
        row.update({f: q[f] for f in FIELDS if q.get(f)})
        if q.get("objectives"):
            row["objectives"] = [{"text": o["text"], "done": bool(o.get("done"))} for o in q["objectives"]]
        if q.get("clues"):
            row["clues"] = list(q["clues"])
        out.append(row)
    return out


def marked_line(quest: dict, row: dict) -> str:
    """The closing line of done/undo: which step changed, and whether any is left open."""
    line = f"{'✓ ticked' if row.get('done') else '○ unticked'}: {quest['name']} — {row['text']}"
    rows = quest.get("objectives") or []
    if row.get("done") and quest.get("status") in OPEN and all(o.get("done") for o in rows):
        line += " (no step left open: resolve it, or add the next one)"
    return line


def describe(quest: dict) -> str:
    lines = [f"{quest['name']} [{quest.get('status', 'active')}]"]
    lines += [f"  {f}: {quest[f]}" for f in FIELDS if quest.get(f)]
    lines += [f"  [{'x' if o.get('done') else ' '}] {o['text']}" for o in quest.get("objectives") or []]
    lines += [f"  · {c}" for c in quest.get("clues") or []]
    return "\n".join(lines)


def reminder(quests: list, limit: int = 8) -> str:
    """One line for the DM with every step still open, so the panel keeps up with play.

    check_input.py and autorun_wait.py print it after the players' actions:
    the DM ticks in the same turn what those actions achieved.
    """
    parts = []
    for q in [q for q in quests if q.get("status") in OPEN][:limit]:
        steps = [_clean(o["text"], LINE) for o in q.get("objectives") or [] if not o.get("done")]
        steps = [t if len(t) <= 90 else t[:89].rstrip() + "…" for t in steps]
        parts.append(f"«{q['name']}» → " + ("; ".join(steps) if steps else "no step left open: resolve it, or add the next step"))
    if not parts:
        return ""
    return ("[[QUESTS — still open: " + " | ".join(parts) + ". If this turn achieved, failed or changed any of it, "
            "or gave a lead, update it in the same Bash as the narration (quests.py done / objective / clue / "
            "set / resolve / fail). Not a player action.]]")


def reminder_line(campaign: str = "") -> str:
    """reminder() for the active campaign; '' when there is none or nothing is open."""
    try:
        return reminder(load(data_path(campaign)))
    except Exception:
        return ""


def _display_quests() -> list:
    """The quests the display is showing, from its saved stats (works with the display off)."""
    from runtime_paths import rt
    try:
        with open(rt("stats.json"), encoding="utf-8") as f:
            rows = json.load(f).get("quests")
    except (OSError, ValueError, AttributeError):
        return []
    return rows if isinstance(rows, list) else []


def _announce(event: dict | None = None) -> None:
    """Tell the display the quests changed. Silent when it is not running."""
    try:
        import send
        send._post(f"{send.BASE_URL}/quests", json.dumps({"event": event} if event else {}).encode("utf-8"),
                   send._read_token())
    except Exception:
        pass   # the display is off: the quests are saved and will be there at the next load


# ─── Command line ─────────────────────────────────────────────────────────────

def _add_fields(parser, default) -> None:
    for field in FIELDS:
        parser.add_argument("--" + field, default=default)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="The party's quests, kept with the campaign.")
    ap.add_argument("--campaign", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add", help="the party takes on a quest")
    p.add_argument("name")
    p.add_argument("--status", default="active", help="active (default) or threat")
    _add_fields(p, "")
    p.add_argument("--objective", action="append", default=[])
    p.add_argument("--clue", action="append", default=[])
    p = sub.add_parser("set", help="change a quest's fields")
    p.add_argument("name")
    p.add_argument("--name", dest="new_name", default=None)
    p.add_argument("--status", default=None)
    _add_fields(p, None)
    for cmd, what in (("objective", "one more step"), ("clue", "one more lead"),
                      ("done", "tick a step"), ("undo", "untick a step")):
        p = sub.add_parser(cmd, help=what)
        p.add_argument("name")
        p.add_argument("text")
    p = sub.add_parser("drop", help="take an objective or a clue away")
    p.add_argument("name")
    p.add_argument("--objective", default="")
    p.add_argument("--clue", default="")
    for cmd in ("resolve", "fail", "reopen", "threat", "remove", "show"):
        sub.add_parser(cmd).add_argument("name")
    for cmd in ("list", "push", "import"):
        sub.add_parser(cmd)
    args = ap.parse_args(argv)

    try:
        import media
        camp = _campaign(args.campaign)
        path = data_path(camp)
        quests = load(path)
        event = None
        if args.cmd == "list":
            print("\n".join(f"[{q.get('status', 'active')}] {q['name']} — "
                            f"{sum(1 for o in q.get('objectives') or [] if o.get('done'))}/{len(q.get('objectives') or [])} "
                            f"objectives, {len(q.get('clues') or [])} clue(s)" for q in quests) or "no quests yet")
            return 0
        if args.cmd == "show":
            print(describe(find(quests, args.name)))
            return 0
        if args.cmd == "push":
            if camp != media.active_campaign():
                raise QuestError(f"{camp} is not the campaign on the display")
            _announce()
            print(f"{len(quests)} quest(s) sent to the display")
            return 0
        if args.cmd == "import":
            if camp != media.active_campaign():
                raise QuestError(f"{camp} is not the campaign on the display")
            new = take_in(quests, _display_quests())
            print("\n".join("+ " + n for n in new) or "nothing to take in: every quest on the display is here already")
        elif args.cmd == "add":
            quest = add(quests, args.name, args.status, args.objective, args.clue,
                        **{f: getattr(args, f) for f in FIELDS})
            event = {"name": quest["name"], "what": "new"}
            print("+ " + describe(quest))
        elif args.cmd == "set":
            quest = find(quests, args.name)
            changed = change(quests, quest, args.new_name, args.status, **{f: getattr(args, f) for f in FIELDS})
            if not changed:
                raise QuestError("set what? give --name, --status or one of --" + ", --".join(FIELDS))
            event = {"name": quest["name"], "what": quest["status"] if "status" in changed and quest["status"] not in OPEN
                     else "updated"}
            print(describe(quest))
        elif args.cmd in ("objective", "clue"):
            quest = find(quests, args.name)
            fresh = add_objective(quest, args.text) if args.cmd == "objective" else add_clue(quest, args.text)
            event = {"name": quest["name"], "what": "updated"} if fresh else None
            print(describe(quest) + ("" if fresh else "\n  (nothing new — it was there already)"))
        elif args.cmd in ("done", "undo"):
            quest = find(quests, args.name)
            row = mark(quest, args.text, args.cmd == "done")
            event = {"name": quest["name"], "what": "updated"}
            print(describe(quest) + "\n" + marked_line(quest, row))
        elif args.cmd == "drop":
            quest = find(quests, args.name)
            print("- " + drop(quest, args.objective, args.clue))
        elif args.cmd in ("resolve", "fail", "reopen", "threat"):
            quest = find(quests, args.name)
            quest["status"] = {"resolve": "resolved", "fail": "failed", "reopen": "active", "threat": "threat"}[args.cmd]
            event = {"name": quest["name"], "what": quest["status"] if quest["status"] not in OPEN else "updated"}
            print(describe(quest))
        elif args.cmd == "remove":
            quest = find(quests, args.name)
            quests.remove(quest)
            print("- " + quest["name"])
        save(path, quests)
        if camp == media.active_campaign():
            _announce(event)
    except QuestError as e:
        print(f"quests: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
