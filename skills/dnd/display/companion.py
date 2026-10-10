#!/usr/bin/env python3
"""companion.py — a small sheet for the animals and followers that travel with the party.

A horse, a familiar, a war dog, a hired guard, a summoned beast: not a player
character, not a monster to be fought, but something with hit points that the
table needs to keep track of.

    companion.py add "Fulmine" --owner "Lyra" --kind mount --from "riding horse"
    companion.py add "Nebbia" --owner "Vardamir" --kind familiar --from raven --note "vede con i suoi occhi"
    companion.py add "Ser Bertrand" --kind ally --hp 27 --ac 16 --speed "9 m" \
        --attack "Spada lunga: +5 a colpire, 1d8+3 taglienti" --trait "Non abbandona mai un ferito"
    companion.py hp "Fulmine" -5          # damage; +3 heals; =9 sets; full restores
    companion.py set "Fulmine" --ac 12 --note "ferrato di fresco"
    companion.py set "Nebbia" --owner "Lio"        # --owner "" = with the whole party
    companion.py remove "Fulmine"
    companion.py list
    companion.py show "Fulmine"

`--from` copies Armour Class, hit points, speed, attacks and traits from an SRD
creature (Italian names understood), in the campaign's language. Everything
can be given or corrected by hand. Kinds: mount, familiar, pet, ally — also
cavalcatura, famiglio, animale, alleato.

Companions live in <campaign>/companions.json. Each one shows as a chip on its
owner's card — name and hit points — and opens into its own small sheet; the
Companions panel lists them all. They never join the player list. Anything
that does not match exits non-zero and changes nothing.
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

KINDS = ("mount", "familiar", "pet", "ally")
KIND_ALIASES = {"cavalcatura": "mount", "destriero": "mount", "famiglio": "familiar", "animale": "pet",
                "compagno": "pet", "companion": "pet", "alleato": "ally", "gregario": "ally", "seguace": "ally",
                "hireling": "ally", "follower": "ally", "summon": "ally", "evocazione": "ally"}
MAX_COMPANIONS = 16
_ACTION_RE = re.compile(r"^[^:—]{1,30} — ")


class CompanionError(Exception):
    """A request that cannot be applied; the companions are left as they were."""


# ─── Data ─────────────────────────────────────────────────────────────────────

def _campaign(campaign: str = "") -> str:
    import media
    camp = media.safe_campaign(campaign) if campaign else media.active_campaign()
    if not camp:
        raise CompanionError("no active campaign — pass --campaign or load one first")
    return camp


def data_path(campaign: str = "") -> str:
    import media
    return str(media.find_campaign(_campaign(campaign)) / "companions.json")


def load(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        return []
    except ValueError as e:
        raise CompanionError(f"companions.json is not valid JSON ({e})")
    rows = data.get("companions") if isinstance(data, dict) else data
    return [c for c in rows or [] if isinstance(c, dict) and c.get("name")]


def save(path: str, companions: list) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"companions": companions}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _key(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def _clean(text, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def find(companions: list, name: str) -> dict:
    want = _key(name)
    if not want:
        raise CompanionError("which companion?")
    hits = [c for c in companions if _key(c["name"]) == want] or [c for c in companions if want in _key(c["name"])]
    if not hits:
        known = ", ".join(c["name"] for c in companions) or "none yet"
        raise CompanionError(f"no companion called {name!r} (companions: {known})")
    if len(hits) > 1:
        raise CompanionError(f"{name!r} matches several companions: " + ", ".join(c["name"] for c in hits))
    return hits[0]


def _kind(value: str) -> str:
    k = (value or "pet").strip().lower()
    k = KIND_ALIASES.get(k, k)
    if k not in KINDS:
        raise CompanionError(f"a companion is one of {', '.join(KINDS)} — not {value!r}")
    return k


# ─── From the SRD ─────────────────────────────────────────────────────────────

def srd_record(query: str, ruleset: str = "2014") -> dict:
    import lookup
    pool = lookup._get_records("monsters", ruleset)
    best = lookup._find(query, pool, 1)
    if not best or lookup._score(query, best[0]) < 2:
        raise CompanionError(f"no SRD creature called {query!r} (check the name with lookup.py monster)")
    return best[0]


def from_record(record: dict, lang: str = "en") -> dict:
    """Armour Class, hit points, speed, attacks and traits of an SRD creature."""
    import lookup
    loc = (lookup._localize(record, lang) if lang != "en" else None) or record
    attacks, traits = [], []
    for para in str(loc.get("description", "")).split("\n\n"):
        para = para.strip()
        if not para:
            continue
        m = _ACTION_RE.match(para)
        (attacks if m else traits).append(_clean(para[m.end():] if m else para, 400))
    return {"species": _clean(loc.get("name", ""), 60), "ac": record.get("ac", ""), "hp": int(record.get("hp") or 1),
            "speed": _clean(loc.get("speed", ""), 60), "attacks": attacks[:6], "traits": traits[:6]}


# ─── Operations ───────────────────────────────────────────────────────────────

def add(companions: list, name: str, kind: str = "", owner: str = "", base: dict | None = None,
        hp: int | None = None, ac=None, speed: str = "", attacks: list | None = None,
        traits: list | None = None, note: str = "") -> dict:
    name = _clean(name, 40)
    if not name:
        raise CompanionError("a companion needs a name")
    if any(_key(c["name"]) == _key(name) for c in companions):
        raise CompanionError(f"there is already a companion called {name!r}")
    if len(companions) >= MAX_COMPANIONS:
        raise CompanionError(f"{MAX_COMPANIONS} companions already — remove the ones that have left")
    base = base or {}
    max_hp = hp if hp is not None else base.get("hp")
    if max_hp is None:
        raise CompanionError("how many hit points? give --hp, or --from an SRD creature")
    if max_hp < 1:
        raise CompanionError("hit points start at 1")
    entry = {"id": secrets.token_hex(4), "name": name, "kind": _kind(kind), "owner": _clean(owner, 50),
             "species": base.get("species", ""), "hp": {"current": int(max_hp), "max": int(max_hp)},
             "ac": ac if ac not in (None, "") else base.get("ac", ""),
             "speed": _clean(speed, 60) or base.get("speed", ""),
             "attacks": [_clean(a, 400) for a in (attacks or []) if _clean(a, 400)] or list(base.get("attacks", [])),
             "traits": [_clean(t, 400) for t in (traits or []) if _clean(t, 400)] or list(base.get("traits", [])),
             "note": _clean(note, 300), "ts": time.time()}
    companions.append(entry)
    return entry


def change_hp(entry: dict, text: str) -> int:
    """'-5' damage, '+3' healing, '=9' or '9' sets, 'full' restores. Returns the change."""
    hp = entry["hp"]
    before = int(hp["current"])
    t = (text or "").strip().lower()
    try:
        if t in ("full", "max", "pieno"):
            target = int(hp["max"])
        elif t.startswith(("+", "-")):
            target = before + int(t)
        else:
            target = int(t.lstrip("="))
    except ValueError:
        raise CompanionError(f"hit points change as -5, +3, =9 or full — not {text!r}")
    hp["current"] = max(0, min(int(hp["max"]), target))
    return hp["current"] - before


def update(entry: dict, companions: list, **fields) -> list:
    """Change some fields; returns the names of what changed."""
    changed = []
    for key, value in fields.items():
        if value is None:
            continue
        if key == "kind":
            value = _kind(value)
        elif key == "max_hp":
            if int(value) < 1:
                raise CompanionError("hit points start at 1")
            if entry["hp"]["max"] != int(value):
                entry["hp"]["max"] = int(value)
                entry["hp"]["current"] = min(int(entry["hp"]["current"]), int(value))
                changed.append("hp")
            continue
        elif key in ("attacks", "traits"):
            value = [_clean(v, 400) for v in value if _clean(v, 400)]
            if not value:
                continue
        elif key == "name":
            value = _clean(value, 40)
            if not value or any(c is not entry and _key(c["name"]) == _key(value) for c in companions):
                raise CompanionError(f"cannot rename to {value!r}: empty, or another companion has that name")
        elif key != "ac":
            value = _clean(value, 300 if key == "note" else 60)
        if entry.get(key) != value:
            entry[key] = value
            changed.append(key)
    return changed


def public_view(companions: list) -> list:
    keys = ("id", "name", "kind", "owner", "species", "hp", "ac", "speed", "attacks", "traits", "note")
    return [{k: c.get(k, [] if k in ("attacks", "traits") else "") for k in keys} for c in companions]


def describe(c: dict) -> str:
    who = f"with {c['owner']}" if c.get("owner") else "with the party"
    lines = [f"{c['name']} — {c['kind']}" + (f" ({c['species']})" if c.get("species") else "") + f", {who}",
             f"  HP {c['hp']['current']}/{c['hp']['max']}" + (f" · AC {c['ac']}" if c.get("ac") not in ("", None) else "")
             + (f" · {c['speed']}" if c.get("speed") else "")]
    lines += [f"  ⚔ {a}" for a in c.get("attacks", [])] + [f"  · {t}" for t in c.get("traits", [])]
    if c.get("note"):
        lines.append(f"  note: {c['note']}")
    return "\n".join(lines)


def _announce(event: dict | None = None) -> None:
    """Tell the display the companions changed. Silent when it is not running."""
    try:
        import send
        send._post(f"{send.BASE_URL}/companions", json.dumps({"event": event} if event else {}).encode("utf-8"),
                   send._read_token())
    except Exception:
        pass   # the display is off: they are saved and will be there next time


# ─── Command line ─────────────────────────────────────────────────────────────

def _fields(p: argparse.ArgumentParser) -> None:
    p.add_argument("--owner", default=None, help='the PC it belongs to ("" = the whole party)')
    p.add_argument("--kind", default=None, help="mount, familiar, pet or ally")
    p.add_argument("--hp", type=int, default=None, help="maximum hit points")
    p.add_argument("--ac", default=None)
    p.add_argument("--speed", default=None)
    p.add_argument("--attack", action="append", default=[], help="one line per attack (repeatable)")
    p.add_argument("--trait", action="append", default=[], help="one line per trait (repeatable)")
    p.add_argument("--note", default=None)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="Small sheets for the animals and followers with the party.")
    ap.add_argument("--campaign", default="", help="campaign to work in (default: the active one)")
    ap.add_argument("--lang", default="", help="language of what --from copies (default: the campaign's)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add", help="a new companion")
    p.add_argument("name")
    p.add_argument("--from", dest="base", default="", help="copy the numbers of an SRD creature")
    _fields(p)
    p = sub.add_parser("hp", help="damage, healing or a new value")
    p.add_argument("name")
    p.add_argument("change", help="-5, +3, =9 or full")
    p = sub.add_parser("set", help="change some fields")
    p.add_argument("name")
    p.add_argument("--name", dest="new_name", default=None)
    _fields(p)
    for cmd in ("remove", "show"):
        sub.add_parser(cmd).add_argument("name")
    sub.add_parser("list")
    # A hit-point change that starts with "-" must not be read as an option.
    argv = list(sys.argv[1:] if argv is None else argv)
    if "hp" in argv and "--" not in argv:
        at = argv.index("hp")
        if len(argv) > at + 2 and re.fullmatch(r"-\d+", argv[at + 2]):
            argv.insert(at + 2, "--")
    args = ap.parse_args(argv)

    try:
        import paths
        camp = _campaign(args.campaign)
        path = data_path(camp)
        companions = load(path)
        event = None
        if args.cmd == "list":
            print("\n\n".join(describe(c) for c in companions) or "no companions")
            return 0
        if args.cmd == "show":
            print(describe(find(companions, args.name)))
            return 0
        if args.cmd == "add":
            base = None
            if args.base:
                base = from_record(srd_record(args.base, paths.campaign_ruleset(camp)),
                                   args.lang or paths.campaign_language(camp))
            entry = add(companions, args.name, args.kind or "", args.owner or "", base, args.hp, args.ac,
                        args.speed or "", args.attack, args.trait, args.note or "")
            event = {"name": entry["name"], "what": "new"}
            print("+ " + describe(entry))
        elif args.cmd == "hp":
            entry = find(companions, args.name)
            delta = change_hp(entry, args.change)
            if delta:
                event = {"name": entry["name"], "what": "hp", "delta": delta,
                         "current": entry["hp"]["current"], "max": entry["hp"]["max"]}
            print(f"{entry['name']}: HP {entry['hp']['current']}/{entry['hp']['max']}"
                  + ("  ★ DOWN — at 0 hit points" if entry["hp"]["current"] == 0 else "")
                  + ("" if delta else "  (no change)"))
        elif args.cmd == "set":
            entry = find(companions, args.name)
            changed = update(entry, companions, name=args.new_name, owner=args.owner, kind=args.kind, max_hp=args.hp,
                             ac=args.ac, speed=args.speed, attacks=args.attack or None, traits=args.trait or None,
                             note=args.note)
            if not changed:
                raise CompanionError("set what? pass at least one of --name --owner --kind --hp --ac --speed --attack --trait --note")
            print(describe(entry))
        elif args.cmd == "remove":
            entry = find(companions, args.name)
            companions.remove(entry)
            print("- " + entry["name"])
        save(path, companions)
        _announce(event)
    except CompanionError as e:
        print(f"companion: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
