#!/usr/bin/env python3
"""bestiary.py — what the party has learned about the creatures it met.

Not the Monster Manual: a notebook that starts empty and holds only what the
characters have seen with their own eyes. Text only, no pictures.

    bestiary.py meet "ghoul"                         # first sight, or met again
    bestiary.py meet "ghoul" --as "Divoratore di tombe"   # the party's own name for it
    bestiary.py meet "La Custode" --custom --type "non morto"    # not in the SRD
    bestiary.py learn "ghoul" "Il suo tocco paralizza chi non è un elfo"
    bestiary.py learn "ghoul" --immune veleno --weak radiante --resist "armi non magiche"
    bestiary.py learn "ghoul" --stat ac --stat hp    # they have fought enough to judge: ac hp speed cr type all
    bestiary.py learn "La Custode" --set ac=15 --set hp=52    # numbers by hand, for a creature not in the SRD
    bestiary.py meet "animated armor" --as "Automa-sentinella"  # a custom page gets the SRD block it is played with
    bestiary.py defeat "ghoul" 3
    bestiary.py rename "ghoul" "Ghoul delle cripte"
    bestiary.py forget "ghoul"
    bestiary.py list
    bestiary.py show "ghoul"

`meet` looks the creature up in the SRD (Italian names are understood), then in
the DM's Monster Manual notes when this PC has them (riferimenti/mm), so that
`--stat` can later reveal its real numbers; the entry shows none of them until
you reveal them. The name is written in the campaign's language (`language:`
flag, or --lang). A creature can be named by any part of its name that means
one entry only.

The notebook lives in <campaign>/bestiary.json. Every screen has a Bestiary
panel, and is told when a page is added or grows. Anything that does not match
exits non-zero and changes nothing.
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

STATS = ("ac", "hp", "speed", "cr", "type")
DEFENCES = ("resist", "immune", "weak")
MAX_FACTS = 30


class BestiaryError(Exception):
    """A request that cannot be applied; the bestiary is left as it was."""


# ─── Data ─────────────────────────────────────────────────────────────────────

def _campaign(campaign: str = "") -> str:
    import media
    camp = media.safe_campaign(campaign) if campaign else media.active_campaign()
    if not camp:
        raise BestiaryError("no active campaign — pass --campaign or load one first")
    return camp


def data_path(campaign: str = "") -> str:
    import media
    return str(media.find_campaign(_campaign(campaign)) / "bestiary.json")


def load(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        return []
    except ValueError as e:
        raise BestiaryError(f"bestiary.json is not valid JSON ({e})")
    rows = data.get("creatures") if isinstance(data, dict) else data
    return [c for c in rows or [] if isinstance(c, dict) and c.get("name")]


def save(path: str, creatures: list) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"creatures": creatures}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _key(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def _clean(text: str, limit: int) -> str:
    return " ".join((text or "").split())[:limit]


def find(creatures: list, name: str) -> dict:
    """The entry `name` means: its name or its SRD name, exact first, else a unique part."""
    want = _key(name)
    if not want:
        raise BestiaryError("which creature?")
    names = lambda c: [_key(c["name"]), _key(c.get("srd_name", ""))]
    hits = [c for c in creatures if want in names(c)] or \
           [c for c in creatures if any(want in n for n in names(c) if n)]
    if not hits:
        known = ", ".join(c["name"] for c in creatures) or "none yet"
        raise BestiaryError(f"no creature called {name!r} in the bestiary (entries: {known})")
    if len(hits) > 1:
        raise BestiaryError(f"{name!r} matches several entries: " + ", ".join(c["name"] for c in hits))
    return hits[0]


# ─── The SRD behind an entry ──────────────────────────────────────────────────

def srd_record(query: str, ruleset: str = "2014") -> dict | None:
    """The SRD monster a name means (exact or leading match only), or None."""
    import lookup
    pool = lookup._get_records("monsters", ruleset)
    best = lookup._find(query, pool, 1)
    return best[0] if best and lookup._score(query, best[0]) >= 2 else None


def book_record(query: str, ruleset: str = "2014") -> dict | None:
    """The SRD monster a name means, else the DM's Monster Manual notes (Goblin Boss, Yeti…), or None."""
    record = srd_record(query, ruleset)
    if record is not None:
        return record
    import mm_notes
    hit = mm_notes.page(query)
    if hit is None:
        return None
    return {"index": "mm:" + hit["slug"], "name": hit["en"], "_i18n": {"it": {"name": hit["it"]}},
            "_mm_stats": mm_notes.stats(hit["text"])}


def srd_stats(record: dict, lang: str = "en") -> dict:
    """The numbers `learn --stat` can reveal, worded for the table's language."""
    if "_mm_stats" in record:          # Monster Manual notes: already in the notes' words
        return dict(record["_mm_stats"])
    import lookup
    loc = (lookup._localize(record, lang) if lang != "en" else None) or record
    cr = record.get("cr", "")
    cr = {0.125: "1/8", 0.25: "1/4", 0.5: "1/2"}.get(cr, str(int(cr)) if isinstance(cr, (int, float)) else str(cr))
    kind = ", ".join(str(x) for x in (loc.get("type", ""), loc.get("size", "")) if x)
    return {"ac": record.get("ac", ""), "hp": record.get("hp", ""), "speed": loc.get("speed", ""),
            "cr": cr, "type": kind}


def _local_name(record: dict, lang: str) -> str:
    return ((record.get("_i18n") or {}).get(lang) or {}).get("name") or record.get("name", "")


# ─── Operations ───────────────────────────────────────────────────────────────

def meet(creatures: list, query: str, alias: str = "", count: int = 1, record: dict | None = None,
         lang: str = "en", custom: bool = False, kind: str = "") -> tuple:
    """A sighting. Returns (entry, True if it is a new page)."""
    if count < 1:
        raise BestiaryError("how many? (a positive number)")
    try:
        entry = find(creatures, alias or query)
    except BestiaryError:
        entry = next((c for c in creatures if record and c.get("index") == record.get("index")), None)
    if entry:
        entry["met"] = int(entry.get("met", 0)) + count
        if record and not entry.get("index"):
            # a page written as custom gets the SRD block it is played with
            entry["index"], entry["srd_name"] = record.get("index", ""), record.get("name", "")
        return entry, False
    if record is None and not custom:
        raise BestiaryError(f"no SRD monster (nor Monster Manual note) called {query!r}; add --custom for a "
                            "creature of your own, or check the name with lookup.py monster")
    name = _clean(alias or (_local_name(record, lang) if record else query), 60)
    if not name:
        raise BestiaryError("a creature needs a name")
    entry = {"id": secrets.token_hex(4), "name": name, "index": record.get("index", "") if record else "",
             "srd_name": record.get("name", "") if record else "", "met": count, "defeated": 0,
             "known": {"type": _clean(kind, 60)} if kind else {}, "resist": [], "immune": [], "weak": [],
             "facts": [], "ts": time.time()}
    creatures.append(entry)
    return entry, True


def learn(entry: dict, fact: str = "", resist: list | None = None, immune: list | None = None,
          weak: list | None = None, stats: list | None = None, record: dict | None = None,
          lang: str = "en", values: list | None = None) -> list:
    """Add what the party found out. Returns the list of what was new.

    `values` are "key=value" numbers written by hand, for a creature the SRD
    does not have (the Monster Manual notes, one of the DM's own)."""
    new = []
    for item in values or []:
        key, sep, value = item.partition("=")
        key, value = key.strip().lower(), _clean(value, 60)
        if not sep or key not in STATS or not value:
            raise BestiaryError(f"--set is key=value with key one of {', '.join(STATS)} — not {item!r}")
        if entry["known"].get(key) != value:
            entry["known"][key] = value
            new.append(f"{key}: {value}")
    fact = _clean(fact, 240)
    if fact and _key(fact) not in [_key(f) for f in entry["facts"]]:
        if len(entry["facts"]) >= MAX_FACTS:
            raise BestiaryError(f"{entry['name']} has {MAX_FACTS} notes already")
        entry["facts"].append(fact)
        new.append(fact)
    for field, values in (("resist", resist), ("immune", immune), ("weak", weak)):
        for value in values or []:
            value = _clean(value, 60)
            if value and _key(value) not in [_key(v) for v in entry[field]]:
                entry[field].append(value)
                new.append(f"{field}: {value}")
    wanted = []
    for s in stats or []:
        s = s.strip().lower()
        if s == "all":
            wanted += list(STATS)
        elif s in STATS:
            wanted.append(s)
        else:
            raise BestiaryError(f"--stat is one of {', '.join(STATS)}, all — not {s!r}")
    if wanted:
        if record is None:
            raise BestiaryError(f"{entry['name']} is not an SRD creature: give its numbers with --set "
                                "(ac=15), or tie it to the SRD block it is played with: "
                                "meet \"<SRD creature>\" --as \"<its name>\"")
        real = srd_stats(record, lang)
        for s in dict.fromkeys(wanted):
            if real.get(s) not in ("", None) and entry["known"].get(s) != real[s]:
                entry["known"][s] = real[s]
                new.append(f"{s}: {real[s]}")
    return new


def defeat(entry: dict, count: int = 1) -> None:
    if count < 1:
        raise BestiaryError("how many? (a positive number)")
    entry["defeated"] = int(entry.get("defeated", 0)) + count
    entry["met"] = max(int(entry.get("met", 0)), 1)


def public_view(creatures: list) -> list:
    """What the players' screens get — the SRD index stays here."""
    keys = ("id", "name", "met", "defeated", "known", "resist", "immune", "weak", "facts")
    rows = [{k: c.get(k, [] if k in DEFENCES or k == "facts" else {} if k == "known" else 0 if k in ("met", "defeated") else "")
             for k in keys} for c in creatures]
    return sorted(rows, key=lambda c: _key(c["name"]))


def describe(entry: dict) -> str:
    book = "Monster Manual" if str(entry.get("index", "")).startswith("mm:") else "SRD"
    lines = [f"{entry['name']}" + (f"  ({book}: {entry['srd_name']})" if entry.get("srd_name") else "  (custom)")
             + f" — met {entry.get('met', 0)}, defeated {entry.get('defeated', 0)}"]
    known = entry.get("known") or {}
    if known:
        lines.append("  known: " + ", ".join(f"{k} {v}" for k, v in known.items()))
    for field in DEFENCES:
        if entry.get(field):
            lines.append(f"  {field}: " + ", ".join(entry[field]))
    lines += [f"  · {f}" for f in entry.get("facts", [])]
    return "\n".join(lines)


def _announce(event: dict | None = None) -> None:
    """Tell the display the bestiary changed. Silent when it is not running."""
    try:
        import send
        send._post(f"{send.BASE_URL}/bestiary", json.dumps({"event": event} if event else {}).encode("utf-8"),
                   send._read_token())
    except Exception:
        pass   # the display is off: the notebook is saved and will be there next time


# ─── Command line ─────────────────────────────────────────────────────────────

def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="What the party has learned about the creatures it met.")
    ap.add_argument("--campaign", default="", help="campaign to work in (default: the active one)")
    ap.add_argument("--lang", default="", help="language of the names (default: the campaign's)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("meet", help="the party sees a creature")
    p.add_argument("name")
    p.add_argument("--as", dest="alias", default="", help="what the party calls it")
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--custom", action="store_true", help="a creature that is not in the SRD")
    p.add_argument("--type", default="", help="with --custom: what kind of thing it is")
    p = sub.add_parser("learn", help="the party finds something out")
    p.add_argument("name")
    p.add_argument("fact", nargs="?", default="")
    for flag in DEFENCES:
        p.add_argument("--" + flag, action="append", default=[])
    p.add_argument("--stat", action="append", default=[], help="reveal a real number: ac hp speed cr type all")
    p.add_argument("--set", dest="values", action="append", default=[],
                   help="a number written by hand, key=value (ac hp speed cr type): for a creature not in the SRD")
    p = sub.add_parser("defeat", help="the party kills or routs some")
    p.add_argument("name")
    p.add_argument("n", type=int, nargs="?", default=1)
    p = sub.add_parser("rename")
    p.add_argument("name")
    p.add_argument("new_name")
    for cmd in ("forget", "show"):
        sub.add_parser(cmd).add_argument("name")
    sub.add_parser("list")
    args = ap.parse_args(argv)

    try:
        import paths
        camp = _campaign(args.campaign)
        lang = args.lang or paths.campaign_language(camp)
        ruleset = paths.campaign_ruleset(camp)
        path = data_path(camp)
        creatures = load(path)
        event = None
        if args.cmd == "list":
            print("\n".join(f"{c['name']} — met {c.get('met', 0)}, defeated {c.get('defeated', 0)}, "
                            f"{len(c.get('facts', [])) + sum(len(c.get(f, [])) for f in DEFENCES) + len(c.get('known', {}))} "
                            "thing(s) known" for c in sorted(creatures, key=lambda c: _key(c["name"])))
                  or "the bestiary is empty")
            return 0
        if args.cmd == "show":
            print(describe(find(creatures, args.name)))
            return 0
        if args.cmd == "meet":
            record = None if args.custom else book_record(args.name, ruleset)
            entry, fresh = meet(creatures, args.name, args.alias, args.count, record, lang, args.custom, args.type)
            event = {"name": entry["name"], "new": fresh} if fresh else None
            print(("+ " if fresh else "") + describe(entry))
        elif args.cmd == "learn":
            entry = find(creatures, args.name)
            if not (args.fact or args.resist or args.immune or args.weak or args.stat or args.values):
                raise BestiaryError("learn what? give a note, --resist/--immune/--weak, --stat or --set")
            record = book_record(entry["srd_name"], ruleset) if entry.get("srd_name") else None
            new = learn(entry, args.fact, args.resist, args.immune, args.weak, args.stat, record, lang,
                        args.values)
            event = {"name": entry["name"], "new": False} if new else None
            print(describe(entry))
            print("  (nothing new — the party knew that already)" if not new else
                  "  new: " + "; ".join(new))
        elif args.cmd == "defeat":
            entry = find(creatures, args.name)
            defeat(entry, args.n)
            print(describe(entry))
        elif args.cmd == "rename":
            entry = find(creatures, args.name)
            new_name = _clean(args.new_name, 60)
            if not new_name or any(c is not entry and _key(c["name"]) == _key(new_name) for c in creatures):
                raise BestiaryError(f"cannot rename to {args.new_name!r}: empty, or another entry has that name")
            entry["name"] = new_name
            print(describe(entry))
        elif args.cmd == "forget":
            entry = find(creatures, args.name)
            creatures.remove(entry)
            print("- " + entry["name"])
        save(path, creatures)
        _announce(event)
    except BestiaryError as e:
        print(f"bestiary: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
