#!/usr/bin/env python3
"""people.py — the people the party has met, as the party knows them.

Not the DM's NPC file: a notebook that starts empty and holds only what the
characters have seen and heard — who someone is, where they met, what the
party found out about them and what they said.

    people.py meet "Padre Oderico" --role "prete di Vallombra" --where "Cappella della Luce Vigile" \
                   --look "vecchio, tonaca rattoppata, una lanterna spenta al collo"
    people.py meet "L'Incappucciato" --role "adepto della Confraternita" --attitude hostile
    people.py meet "Åsa" --role "madre di Ketil" --status prisoner
    people.py meet "Gigi" --where "Il Mantice Rotto" --place "Ferrovalle" --quest "Lavoro al cantiere"
    people.py card "oderico"                                   # show his card on every screen again
    people.py learn "oderico" "Compagno di seminario di Isolda Fenn"
    people.py said "oderico" "A me risponde. A me ha sempre risposto."
    people.py seen "oderico" "Cimitero della Collina"          # where the party last saw them
    people.py set "oderico" --attitude friendly --faction "Chiesa di Morwen" --status missing
    people.py set "gigi" --place "Ferrovalle"                  # the town or place the panel files them under
    people.py link "oderico" "rogo della strega"               # a quest they are part of (unlink takes it away)
    people.py rename "incappucciato" "Nando"                   # the party learns the real name
    people.py drop "oderico" --fact "seminario"                # or --said: take one line away
    people.py forget "nando"
    people.py list
    people.py show "oderico"

Attitude: ally, friendly, neutral, wary, hostile (or "" for unknown). Status:
alive (the default), dead, prisoner, missing, gone. Italian words are
understood (amico, ostile, morto, prigioniero, scomparso…).

The notebook lives in <campaign>/people.json. Every screen has a People panel
and is told when a page is added or grows. Meeting someone new puts a card on
every screen — name, who they are, what they look like (--look, one line),
where — so the table knows at once who is speaking; `card` shows it again. A person can be named by any part
of the name that means one page only. Anything that does not match exits
non-zero and changes nothing.

The panel files people by place or by quest. The place is --place (a town, a
region, a dungeon: the heading they go under), else the place of the region
map whose name is in --where (then in their last sighting), else --where as
it is. Quests are the ones of quests.py they are linked to (--quest on meet,
or link/unlink), named by any part of the quest's name.
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

ATTITUDES = ("ally", "friendly", "neutral", "wary", "hostile")
ATTITUDE_ALIASES = {"alleato": "ally", "alleata": "ally", "allied": "ally", "amico": "friendly", "amica": "friendly",
                    "amichevole": "friendly", "neutrale": "neutral", "diffidente": "wary", "sospettoso": "wary",
                    "sospettosa": "wary", "suspicious": "wary", "ostile": "hostile", "nemico": "hostile",
                    "nemica": "hostile", "enemy": "hostile"}
STATUSES = ("alive", "dead", "prisoner", "missing", "gone")
STATUS_ALIASES = {"vivo": "alive", "viva": "alive", "morto": "dead", "morta": "dead", "prigioniero": "prisoner",
                  "prigioniera": "prisoner", "captured": "prisoner", "scomparso": "missing", "scomparsa": "missing",
                  "partito": "gone", "partita": "gone", "left": "gone"}
FIELDS = ("role", "where", "seen", "faction", "look", "place")
LIMITS = {"name": 60, "role": 120, "where": 120, "seen": 120, "faction": 80, "look": 160, "place": 80}
MAX_QUESTS = 12         # quests one person is linked to
_ARTICLES = ("il ", "lo ", "la ", "l'", "l’", "i ", "gli ", "le ", "the ")
LINE = 240
MAX_LINES = 40          # facts, or things said, on one page


class PeopleError(Exception):
    """A request that cannot be applied; the notebook is left as it was."""


# ─── Data ─────────────────────────────────────────────────────────────────────

def _campaign(campaign: str = "") -> str:
    import media
    camp = media.safe_campaign(campaign) if campaign else media.active_campaign()
    if not camp:
        raise PeopleError("no active campaign — pass --campaign or load one first")
    return camp


def data_path(campaign: str = "") -> str:
    import media
    return str(media.find_campaign(_campaign(campaign)) / "people.json")


def load(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        return []
    except ValueError as e:
        raise PeopleError(f"people.json is not valid JSON ({e})")
    rows = data.get("people") if isinstance(data, dict) else data
    return [p for p in rows or [] if isinstance(p, dict) and p.get("name")]


def save(path: str, people: list) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"people": people}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _key(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def _clean(text: str, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def find(people: list, name: str) -> dict:
    """The page `name` means: the exact name first, else a part only one page has."""
    want = _key(name)
    if not want:
        raise PeopleError("who?")
    hits = [p for p in people if _key(p["name"]) == want] or [p for p in people if want in _key(p["name"])]
    if not hits:
        known = ", ".join(p["name"] for p in people) or "nobody yet"
        raise PeopleError(f"nobody called {name!r} in the notebook (people: {known})")
    if len(hits) > 1:
        raise PeopleError(f"{name!r} matches several people: " + ", ".join(p["name"] for p in hits))
    return hits[0]


def _one_of(value: str, table: tuple, aliases: dict, what: str, empty: str = "") -> str:
    v = (value or "").strip().lower()
    if not v:
        return empty
    v = aliases.get(v, v)
    if v not in table:
        raise PeopleError(f"{what} is one of {', '.join(table)} — not {value!r}")
    return v


# ─── Operations ───────────────────────────────────────────────────────────────

def meet(people: list, name: str, role: str = "", where: str = "", attitude: str = "", faction: str = "",
         look: str = "", place: str = "") -> tuple:
    """A meeting. Returns (page, True if it is a new one). Meeting again updates where they were seen."""
    try:
        page = find(people, name)
    except PeopleError:
        page = None
    if page:
        if _clean(where, LIMITS["seen"]):
            page["seen"] = _clean(where, LIMITS["seen"])
        return page, False
    name = _clean(name, LIMITS["name"])
    if not name:
        raise PeopleError("a person needs a name, or what the party calls them")
    page = {"id": secrets.token_hex(4), "name": name, "role": _clean(role, LIMITS["role"]),
            "where": _clean(where, LIMITS["where"]), "seen": "", "faction": _clean(faction, LIMITS["faction"]),
            "look": _clean(look, LIMITS["look"]), "place": _clean(place, LIMITS["place"]),
            "attitude": _one_of(attitude, ATTITUDES, ATTITUDE_ALIASES, "attitude"), "status": "alive",
            "facts": [], "said": [], "ts": time.time()}
    people.append(page)
    return page, True


def _add_line(page: dict, field: str, text: str, what: str) -> bool:
    text = _clean(text, LINE)
    if not text:
        raise PeopleError(f"{what} needs words")
    rows = page.setdefault(field, [])
    if any(_key(r) == _key(text) for r in rows):
        return False
    if len(rows) >= MAX_LINES:
        raise PeopleError(f"{page['name']} has {MAX_LINES} lines there already")
    rows.append(text)
    return True


def learn(page: dict, fact: str) -> bool:
    """Something the party found out about them. False when it was known already."""
    return _add_line(page, "facts", fact, "a fact")


def said(page: dict, words: str) -> bool:
    """Something they told the party, in their words. False when it was noted already."""
    return _add_line(page, "said", words, "what they said")


def change(page: dict, attitude: str | None = None, status: str | None = None, **fields) -> list:
    """Set what was given (None = leave alone, "" = empty it). Returns what changed."""
    changed = []
    for field in FIELDS:
        if fields.get(field) is not None:
            value = _clean(fields[field], LIMITS[field])
            if value != page.get(field, ""):
                page[field] = value
                changed.append(field)
    if attitude is not None:
        value = _one_of(attitude, ATTITUDES, ATTITUDE_ALIASES, "attitude")
        if value != page.get("attitude", ""):
            page["attitude"] = value
            changed.append("attitude")
    if status is not None:
        value = _one_of(status, STATUSES, STATUS_ALIASES, "status", "alive")
        if value != page.get("status", "alive"):
            page["status"] = value
            changed.append("status")
    return changed


def drop(page: dict, fact: str = "", words: str = "") -> str:
    if bool(fact) == bool(words):
        raise PeopleError("drop what? give --fact or --said (one of them)")
    rows = page.get("facts" if fact else "said") or []
    want = _key(fact or words)
    hits = [i for i, r in enumerate(rows) if _key(r) == want] or [i for i, r in enumerate(rows) if want in _key(r)]
    if len(hits) != 1:
        raise PeopleError(f"{fact or words!r} matches {len(hits)} lines of {page['name']}")
    return rows.pop(hits[0])


def link(page: dict, quest: dict) -> bool:
    """`page` takes part in `quest` (a quests.py quest). False when it was linked already."""
    ids = page.setdefault("quests", [])
    if quest["id"] in ids:
        return False
    if len(ids) >= MAX_QUESTS:
        raise PeopleError(f"{page['name']} is linked to {MAX_QUESTS} quests already")
    ids.append(quest["id"])
    return True


def unlink(page: dict, quest: dict) -> bool:
    ids = page.get("quests") or []
    if quest["id"] not in ids:
        return False
    ids.remove(quest["id"])
    return True


def _bare(text: str) -> str:
    """A place name without its article, to compare: "La torre di Dentesecco" ~ "Torre di Dentesecco"."""
    k = _key(text)
    for a in _ARTICLES:
        if k.startswith(a):
            return k[len(a):].strip()
    return k


def group_of(page: dict, places=()) -> str:
    """The heading the panel files `page` under when it is sorted by place.

    --place first (spelled as on the region map when it names one of its
    places); else the region map's place named in where they met, then where
    they were last seen (the longest name wins: "Porta del Corvo, Kaldrheim"
    goes under Kaldrheim); else where they met, as written.
    """
    places = [p for p in places if _bare(p)]
    if page.get("place"):
        return next((p for p in places if _bare(p) == _bare(page["place"])), page["place"])
    for field in ("where", "seen"):
        text = _key(page.get(field, ""))
        hits = [p for p in places if _bare(p) in text]
        if hits:
            return max(hits, key=len)
    return page.get("where", "")


def public_view(people: list, places=(), quests=()) -> list:
    """What the players' screens get, in alphabetical order.

    `places`: the region map's place names; `quests`: the quests.py quests,
    so each page says where it is filed and which quests it belongs to.
    """
    by_id = {q.get("id"): q for q in quests if isinstance(q, dict) and q.get("id") and q.get("name")}
    rows = []
    for p in people:
        linked = [by_id[i] for i in p.get("quests") or [] if i in by_id]
        rows.append({"id": p.get("id", ""), "name": p["name"],
                     **{f: p.get(f, "") for f in FIELDS},
                     "attitude": p.get("attitude", "") if p.get("attitude") in ATTITUDES else "",
                     "status": p.get("status") if p.get("status") in STATUSES else "alive",
                     "facts": list(p.get("facts") or []), "said": list(p.get("said") or []),
                     "group": group_of(p, places), "ts": p.get("ts") or 0,
                     "quests": [{"name": q["name"], "status": q.get("status") or "active"} for q in linked]})
    return sorted(rows, key=lambda p: _key(p["name"]))


def card(page: dict) -> dict:
    """What the card on every screen shows: who they are, what they look like, where."""
    return {"role": page.get("role", ""), "look": page.get("look", ""), "where": page.get("seen") or page.get("where", "")}


def describe(page: dict, quests=()) -> str:
    head = page["name"] + (f" — {page['role']}" if page.get("role") else "")
    tags = [t for t in (page.get("attitude", ""), page.get("status", "alive") if page.get("status", "alive") != "alive" else "") if t]
    lines = [head + (f"  [{', '.join(tags)}]" if tags else "")]
    if page.get("look"):
        lines.append(f"  looks: {page['look']}")
    lines += [f"  {label}: {page[f]}" for f, label in (("place", "filed under"), ("where", "met"), ("seen", "last seen"),
                                                        ("faction", "faction"))
              if page.get(f)]
    names = {q.get("id"): q.get("name") for q in quests if isinstance(q, dict)}
    linked = [names.get(i) or f"(quest {i}, gone)" for i in page.get("quests") or []]
    if linked:
        lines.append("  quests: " + "; ".join(linked))
    lines += [f"  · {f}" for f in page.get("facts") or []]
    lines += [f"  « {s} »" for s in page.get("said") or []]
    return "\n".join(lines)


def _announce(event: dict | None = None) -> None:
    """Tell the display the notebook changed. Silent when it is not running."""
    try:
        import send
        send._post(f"{send.BASE_URL}/people", json.dumps({"event": event} if event else {}).encode("utf-8"),
                   send._read_token())
    except Exception:
        pass   # the display is off: the notebook is saved and will be there next time


# ─── Command line ─────────────────────────────────────────────────────────────

def _quests(camp: str) -> list:
    """The campaign's quests (quests.py); none when it has no quests.json or it cannot be read."""
    import quests as qmod
    try:
        return qmod.load(qmod.data_path(camp))
    except Exception:
        return []


def _quest(quests: list, name: str) -> dict:
    import quests as qmod
    try:
        quest = qmod.find(quests, name)
    except qmod.QuestError as e:
        raise PeopleError(str(e))
    if not quest.get("id"):
        raise PeopleError(f"«{quest['name']}» has no id in quests.json")
    return quest


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="The people the party has met, as the party knows them.")
    ap.add_argument("--campaign", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("meet", help="the party meets someone, or meets them again")
    p.add_argument("name")
    p.add_argument("--role", default="", help="who they are, as the party knows it")
    p.add_argument("--where", default="", help="where (the first time: where they met; later: where they were seen)")
    p.add_argument("--attitude", default="", help=", ".join(ATTITUDES))
    p.add_argument("--faction", default="")
    p.add_argument("--look", default="", help="what they look like, in one line: it goes on the card every screen shows")
    p.add_argument("--status", default="", help=", ".join(STATUSES) + " (a prisoner met in a cage, a body found dead)")
    p.add_argument("--place", default="", help="the town, region or place the panel files them under")
    p.add_argument("--quest", action="append", default=[], help="a quest they are part of (repeatable)")
    for cmd, arg, what in (("learn", "fact", "the party finds something out about them"),
                           ("said", "words", "something they told the party"),
                           ("seen", "place", "where the party last saw them")):
        p = sub.add_parser(cmd, help=what)
        p.add_argument("name")
        p.add_argument(arg)
    p = sub.add_parser("set", help="change who they are, how they stand, what became of them")
    p.add_argument("name")
    p.add_argument("--role", default=None)
    p.add_argument("--where", default=None, help="where the party first met them")
    p.add_argument("--faction", default=None)
    p.add_argument("--look", default=None)
    p.add_argument("--attitude", default=None, help=", ".join(ATTITUDES) + ' ("" = unknown)')
    p.add_argument("--status", default=None, help=", ".join(STATUSES))
    p.add_argument("--place", default=None, help='the town, region or place the panel files them under ("" = from --where)')
    for cmd, what in (("link", "they are part of a quest"), ("unlink", "they are not part of that quest after all")):
        p = sub.add_parser(cmd, help=what)
        p.add_argument("name")
        p.add_argument("quest", help="any part of the quest's name that means one quest")
    p = sub.add_parser("rename", help="the party learns their name")
    p.add_argument("name")
    p.add_argument("new_name")
    p = sub.add_parser("drop", help="take a fact or a quote away")
    p.add_argument("name")
    p.add_argument("--fact", default="")
    p.add_argument("--said", default="")
    for cmd in ("forget", "show"):
        sub.add_parser(cmd).add_argument("name")
    sub.add_parser("card", help="show their card on every screen again").add_argument("name")
    sub.add_parser("list")
    args = ap.parse_args(argv)

    try:
        import media
        camp = _campaign(args.campaign)
        path = data_path(camp)
        people = load(path)
        quests = _quests(camp)
        event = None
        if args.cmd == "list":
            print("\n".join(p["name"] + (f" — {p['role']}" if p.get("role") else "")
                            + (f" [{p['place']}]" if p.get("place") else "")
                            + f" ({len(p.get('facts') or [])} fact(s), {len(p.get('said') or [])} quote(s))"
                            for p in sorted(people, key=lambda p: _key(p["name"]))) or "the party has met nobody yet")
            return 0
        if args.cmd == "show":
            print(describe(find(people, args.name), quests))
            return 0
        if args.cmd == "card":
            page = find(people, args.name)
            if camp == media.active_campaign():
                _announce({"name": page["name"], "new": False, "card": card(page)})
            print(describe(page, quests))
            return 0
        if args.cmd == "meet":
            linked = [_quest(quests, q) for q in args.quest]
            page, fresh = meet(people, args.name, args.role, args.where, args.attitude, args.faction, args.look, args.place)
            if not fresh:
                change(page, args.attitude or None, None, role=args.role or None, faction=args.faction or None,
                       look=args.look or None, place=args.place or None)
            if args.status:
                change(page, None, args.status)
            for q in linked:
                link(page, q)
            event = {"name": page["name"], "new": True, "card": card(page)} if fresh else None
            if fresh and not page.get("look"):
                print("  (no --look: the card says who they are but not what they look like — people.py set --look)")
            print(("+ " if fresh else "") + describe(page, quests))
        elif args.cmd in ("learn", "said"):
            page = find(people, args.name)
            fresh = learn(page, args.fact) if args.cmd == "learn" else said(page, args.words)
            event = {"name": page["name"], "new": False} if fresh else None
            print(describe(page, quests) + ("" if fresh else "\n  (nothing new — the party knew that already)"))
        elif args.cmd == "seen":
            page = find(people, args.name)
            if not change(page, seen=args.place):
                raise PeopleError("seen where? (or that is where they were seen last already)")
            print(describe(page, quests))
        elif args.cmd == "set":
            page = find(people, args.name)
            changed = change(page, args.attitude, args.status, role=args.role, where=args.where, faction=args.faction,
                             look=args.look, place=args.place)
            if not changed:
                raise PeopleError("set what? give --role, --where, --place, --faction, --look, --attitude or --status")
            event = {"name": page["name"], "new": False} if "status" in changed else None
            print(describe(page, quests))
        elif args.cmd in ("link", "unlink"):
            page = find(people, args.name)
            quest = _quest(quests, args.quest)
            if not (link if args.cmd == "link" else unlink)(page, quest):
                raise PeopleError(f"{page['name']} is {'already' if args.cmd == 'link' else 'not'} linked to «{quest['name']}»")
            print(describe(page, quests))
        elif args.cmd == "rename":
            page = find(people, args.name)
            new_name = _clean(args.new_name, LIMITS["name"])
            if not new_name or any(p is not page and _key(p["name"]) == _key(new_name) for p in people):
                raise PeopleError(f"cannot rename to {args.new_name!r}: empty, or another page has that name")
            page["name"] = new_name
            event = {"name": page["name"], "new": False}
            print(describe(page, quests))
        elif args.cmd == "drop":
            page = find(people, args.name)
            print("- " + drop(page, args.fact, args.said))
        elif args.cmd == "forget":
            page = find(people, args.name)
            people.remove(page)
            print("- " + page["name"])
        save(path, people)
        if camp == media.active_campaign():
            _announce(event)
    except PeopleError as e:
        print(f"people: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
