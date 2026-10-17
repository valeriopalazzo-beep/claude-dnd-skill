#!/usr/bin/env python3
"""shapes.py — Wild Shape and Polymorph: a character in a beast's body.

The druid's Wild Shape (PHB 2014 pp. 66-67) and the spells that do the same
to anyone — Polymorph, Animal Shapes, Shapechange, True Polymorph (PHB pp.
266-267, 212, 274, 283): the creature's statistics take the place of the
character's, with its own hit points. Damage comes off the form first; when
the form drops to 0 the character is back, with the hit points they had and
the damage left over taken from those (not unconscious unless that brings
them to 0 too).

    shapes.py wild "Rovo" "lupo"                       # Wild Shape: a use, the druid's limits checked
    shapes.py spell "Lyra" "orso bruno" --spell polymorph --by "Vardamir"
    shapes.py spell "Rovo" "tigre" --spell animal-shapes
    shapes.py hit "Rovo" 9                             # damage to the form (the display does it by itself)
    shapes.py heal "Rovo" 4
    shapes.py end "Rovo"                               # a bonus action, the spell ends, concentration broken
    shapes.py rest "Rovo"                              # Wild Shape uses back (rest.py does it by itself)
    shapes.py list | show "Rovo"

Wild Shape limits by druid level: 2nd CR 1/4, no flying or swimming speed;
4th CR 1/2, no flying; 8th CR 1. The Circle of the Moon (read from the sheet,
or --moon) goes to CR 1 at 2nd level and level / 3 from 6th. Two uses per
short or long rest (unlimited at 20th); hours = half the druid level. The
form's creature comes from the SRD (Italian names understood). Forms live in
<campaign>/shapes.json; the player's card shows the form and its hit points,
and the Attacks panel rolls the form's attacks.
"""
from __future__ import annotations

import argparse
import json
import math
import os
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

# What each way of changing shape allows (PHB 2014): the creature types, a CR cap, hours, concentration.
SPELLS = {
    "wild": {"types": ("beast",), "hours": None, "conc": False, "names": {"en": "Wild Shape", "it": "Forma Selvatica"}},
    "polymorph": {"types": ("beast",), "cr": "level", "hours": 1, "conc": True, "names": {"en": "Polymorph", "it": "Metamorfosi"}},
    "animal-shapes": {"types": ("beast",), "cr": 4, "max_size": "Large", "hours": 24, "conc": True,
                      "names": {"en": "Animal Shapes", "it": "Forme Animali"}},
    "shapechange": {"types": None, "cr": "level", "hours": 1, "conc": True, "not": ("construct", "undead"),
                    "names": {"en": "Shapechange", "it": "Trasformazione"}},
    "true-polymorph": {"types": None, "cr": "level", "hours": 1, "conc": True,
                       "names": {"en": "True Polymorph", "it": "Metamorfosi Pura"}},
}
SPELL_ALIASES = {"metamorfosi": "polymorph", "forme animali": "animal-shapes", "forme-animali": "animal-shapes",
                 "animal shapes": "animal-shapes", "trasformazione": "shapechange", "metamorfosi pura": "true-polymorph",
                 "metamorfosi-pura": "true-polymorph", "true polymorph": "true-polymorph"}
SIZES = ("Tiny", "Small", "Medium", "Large", "Huge", "Gargantuan")
WILD_USES = 2


class ShapeError(Exception):
    """A request that cannot be applied; nothing was changed."""


# ─── Data ─────────────────────────────────────────────────────────────────────

def _campaign(campaign: str = "") -> str:
    import media
    camp = media.safe_campaign(campaign) if campaign else media.active_campaign()
    if not camp:
        raise ShapeError("no active campaign — pass --campaign or load one first")
    return camp


def data_path(campaign: str = "") -> str:
    import media
    return str(media.find_campaign(_campaign(campaign)) / "shapes.json")


def blank() -> dict:
    return {"forms": [], "used": {}}


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        return blank()
    except ValueError as e:
        raise ShapeError(f"shapes.json is not valid JSON ({e})")
    base = blank()
    if isinstance(data, dict):
        base.update({k: data[k] for k in base if k in data})
    return base


def save(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _key(text: str) -> str:
    return "".join(ch for ch in (text or "").casefold() if ch.isalnum())


def form_of(data: dict, character: str) -> dict | None:
    return next((f for f in data["forms"] if _key(f["character"]) == _key(character)), None)


def spell_of(word: str) -> str:
    w = " ".join((word or "").strip().lower().split())
    w = SPELL_ALIASES.get(w, w.replace(" ", "-"))
    if w not in SPELLS or w == "wild":
        raise ShapeError(f"--spell is one of {', '.join(k for k in SPELLS if k != 'wild')} — not {word!r}")
    return w


def cr_text(cr) -> str:
    return {0.125: "1/8", 0.25: "1/4", 0.5: "1/2"}.get(float(cr), f"{float(cr):g}")


# ─── The druid ────────────────────────────────────────────────────────────────

def druid_level(text: str) -> tuple:
    """(druid level, Circle of the Moon) from the sheet; (0, False) for one who is not a druid."""
    m = re.search(r"\*\*Class:\*\*\s*([^|\n]*)", text)
    cls = m.group(1) if m else ""
    if not re.search(r"druid", cls, re.I):
        return 0, False
    moon = bool(re.search(r"\b(?:luna|moon)\b", cls + " " + text[:3000], re.I))
    n = re.search(r"druid\w*[^|\d\n]*?(\d+)", cls, re.I)
    if n:
        return int(n.group(1)), moon
    lv = re.search(r"\*\*Level:\*\*\s*(\d+)", text)
    return (int(lv.group(1)) if lv else 1), moon


def wild_limits(level: int, moon: bool = False) -> dict:
    """{"cr", "fly", "swim"} the druid's Wild Shape allows (PHB pp. 66-67, 69)."""
    if level < 2:
        raise ShapeError("Wild Shape comes at the 2nd druid level")
    cr = 1.0 if level >= 8 else 0.5 if level >= 4 else 0.25
    if moon:
        cr = max(1.0, float(level // 3)) if level >= 6 else 1.0
    return {"cr": cr, "fly": level >= 8, "swim": level >= 4}


# ─── The creature ─────────────────────────────────────────────────────────────

_ACTION = re.compile(r"^(?:Action|Azione)\s+—\s+([^:]{1,40}):\s*(.*)$", re.S)
_TO_HIT = re.compile(r"([+-]\d+)\s+(?:to hit|a colpire)", re.I)
_HIT_DMG = re.compile(r"\((\d+d\d+(?:\s*[+-]\s*\d+)?)\)\s*(?:danni\s+(?:da\s+)?(\w+)|(\w+)\s+damage)", re.I)


def form_attacks(description: str) -> list:
    """The creature's attacks as rows of the Attacks table (attacks.py): name, bonus, damage, type, notes."""
    import attacks
    rows = []
    for para in str(description or "").split("\n\n"):
        m = _ACTION.match(para.strip())
        if not m:
            continue
        name, body = m.group(1).strip(), m.group(2)
        hit = _TO_HIT.search(body)
        dmg = _HIT_DMG.search(body)
        if not hit or not dmg:
            continue
        rows.append({"name": name, "bonus": int(hit.group(1)), "save": None, "damage": attacks.parse_dice(dmg.group(1).replace(" ", "")),
                     "type": (dmg.group(2) or dmg.group(3) or "").lower(), "notes": " ".join(body.split())[:200],
                     "alts": [], "extras": [], "ammo": None})
    return rows


def srd_creature(query: str, ruleset: str = "2014") -> dict:
    import lookup
    pool = lookup._get_records("monsters", ruleset)
    best = lookup._find(query, pool, 1)
    if not best or lookup._score(query, best[0]) < 2:
        raise ShapeError(f"no SRD creature called {query!r} (check the name with lookup.py monster)")
    return best[0]


def make_form(record: dict, lang: str = "en") -> dict:
    import lookup
    loc = (lookup._localize(record, lang) if lang != "en" else None) or record
    traits = [" ".join(p.split())[:300] for p in str(loc.get("description", "")).split("\n\n")
              if p.strip() and not _ACTION.match(p.strip())]
    return {"creature": loc.get("name", record.get("name", "")), "index": record.get("index", ""),
            "type": str(record.get("type", "")).lower(), "size": record.get("size", ""), "cr": float(record.get("cr") or 0),
            "hp": {"current": int(record.get("hp") or 1), "max": int(record.get("hp") or 1)}, "ac": record.get("ac", ""),
            "speed": loc.get("speed", ""), "speed_en": record.get("speed", ""),
            "attacks": form_attacks(loc.get("description", "")), "traits": traits[:6]}


def check(form: dict, how: str, level: int = 0, moon: bool = False, target_cr=None) -> None:
    """Refuse a form the way of changing shape does not allow."""
    spec = SPELLS[how]
    if spec["types"] and form["type"] not in spec["types"]:
        raise ShapeError(f"{form['creature']} is a {form['type']}, not a beast")
    if form["type"] in spec.get("not", ()):
        raise ShapeError(f"{SPELLS[how]['names']['en']} cannot take the shape of a {form['type']}")
    if how == "wild":
        lim = wild_limits(level, moon)
        if form["cr"] > lim["cr"]:
            raise ShapeError(f"{form['creature']} is CR {cr_text(form['cr'])}: at druid level {level}"
                             f"{' (Moon)' if moon else ''} Wild Shape goes to CR {cr_text(lim['cr'])}")
        if not lim["fly"] and re.search(r"\bfly\b", form["speed_en"]):
            raise ShapeError(f"{form['creature']} flies: Wild Shape takes flying forms from the 8th druid level")
        if not lim["swim"] and re.search(r"\bswim\b", form["speed_en"]):
            raise ShapeError(f"{form['creature']} swims: Wild Shape takes swimming forms from the 4th druid level")
        return
    cap = spec.get("cr")
    cap = target_cr if cap == "level" else cap
    if cap is not None and form["cr"] > float(cap):
        raise ShapeError(f"{form['creature']} is CR {cr_text(form['cr'])}: {spec['names']['en']} allows CR {cr_text(cap)} at most")
    if spec.get("max_size") and form["size"] in SIZES and SIZES.index(form["size"]) > SIZES.index(spec["max_size"]):
        raise ShapeError(f"{form['creature']} is {form['size']}: {spec['names']['en']} allows {spec['max_size']} or smaller")


# ─── Damage, healing, the end ─────────────────────────────────────────────────

def hit(form: dict, damage: int) -> dict:
    """Damage to the form: {"taken", "left", "excess", "ended"} — at 0 the form is over and `excess` goes on."""
    if damage < 0:
        raise ShapeError("damage is 0 or more")
    cur = int(form["hp"]["current"])
    if damage < cur:
        form["hp"]["current"] = cur - damage
        return {"taken": damage, "left": cur - damage, "excess": 0, "ended": False}
    form["hp"]["current"] = 0
    return {"taken": cur, "left": 0, "excess": damage - cur, "ended": True}


def heal(form: dict, points: int) -> int:
    got = max(0, min(int(points), int(form["hp"]["max"]) - int(form["hp"]["current"])))
    form["hp"]["current"] += got
    return got


def remove(data: dict, character: str) -> dict:
    f = form_of(data, character)
    if not f:
        raise ShapeError(f"{character} is in their own shape")
    data["forms"].remove(f)
    return f


def uses_left(data: dict, character: str, level: int) -> int | None:
    if level >= 20:
        return None                                  # Archdruid: as often as they like
    return max(0, WILD_USES - int(data["used"].get(character, 0)))


def public_form(f: dict | None) -> dict | None:
    """What the player's card and the display need."""
    if not f:
        return None
    return {"creature": f["creature"], "how": f["how"], "hp": dict(f["hp"]), "ac": f.get("ac", ""), "speed": f.get("speed", ""),
            "until": f.get("until_text", ""), "by": f.get("by", "")}


def expired(data: dict, now_h) -> list:
    """The forms whose time is up at hour `now_h` (removed from data)."""
    if now_h is None:
        return []
    gone = [f for f in data["forms"] if f.get("until_h") is not None and now_h >= f["until_h"]]
    for f in gone:
        data["forms"].remove(f)
    return gone


def tick(campaign: str, now_h) -> list:
    """calendar.py moved the clock: forms whose hours are over end. Lines for the DM."""
    try:
        path = data_path(campaign)
    except Exception:
        return []
    if not os.path.isfile(path):
        return []
    data = load(path)
    gone = expired(data, now_h)
    if not gone:
        return []
    save(path, data)
    _announce({"what": "ended", "name": ", ".join(f["character"] for f in gone)})
    return [f"{f['character']} is back in their own shape: the {f['creature']}'s hours are over" for f in gone]


# ─── Sheets, time, the display ────────────────────────────────────────────────

def _now_hours(camp: str):
    try:
        import timeline
        lib, cal = timeline.calendar_of(camp)
        return lib._abs_day(cal) * 24 + int(cal.get("hour", 8)) if lib else None
    except Exception:
        return None


def _sheet(name: str, camp: str) -> tuple:
    import inventory
    import spellbook
    try:
        path = spellbook.find_sheet(name, camp)
    except Exception as e:
        raise ShapeError(str(e))
    return inventory.display_name(path), inventory._read(path)


def _live_hp(name: str) -> dict | None:
    from runtime_paths import rt
    try:
        with open(rt("stats.json"), encoding="utf-8") as f:
            players = json.load(f).get("players") or []
    except (OSError, ValueError, AttributeError):
        return None
    p = next((p for p in players if isinstance(p, dict) and _key(p.get("name", "")) == _key(name)), None)
    return p.get("hp") if p and isinstance(p.get("hp"), dict) else None


def _push_hp(name: str, current: int, maximum: int) -> None:
    try:
        subprocess.run([sys.executable, os.path.join(_HERE, "push_stats.py"), "--player", name, "--hp", str(current), str(maximum)],
                       capture_output=True, timeout=10, env=dict(os.environ, PYTHONUTF8="1"))
    except Exception:
        pass


def back_to_normal(name: str, excess: int) -> str:
    """The form is over: the damage left over comes off the character's own hit points (on the display)."""
    if excess <= 0:
        return f"{name} is back in their own shape, with the hit points they had"
    hp = _live_hp(name)
    if not hp:
        return f"{name} is back in their own shape: take {excess} damage off their hit points (the display is off)"
    cur = max(0, int(hp.get("current", 0)) - excess)
    _push_hp(name, cur, int(hp.get("max", cur)))
    return f"{name} is back in their own shape and takes the {excess} damage left over: {cur}/{hp.get('max')} HP" \
           + (" — DOWN" if cur == 0 else "")


def _announce(event: dict | None = None) -> None:
    try:
        import send
        send._post(f"{send.BASE_URL}/shapes", json.dumps({"event": event} if event else {}).encode("utf-8"), send._read_token())
    except Exception:
        pass


def _notes_file() -> str:
    from runtime_paths import rt
    return rt("shape_notes.json")


def notes_add(line: str) -> None:
    path = _notes_file()
    try:
        with open(path, encoding="utf-8") as f:
            notes = json.load(f)
        notes = notes if isinstance(notes, list) else []
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


def rested(campaign: str, character: str) -> bool:
    """A short or long rest is over for the character: their Wild Shape uses are back."""
    try:
        path = data_path(campaign)
    except Exception:
        return False
    if not os.path.isfile(path):
        return False
    data = load(path)
    hit_ = [k for k in data["used"] if _key(k) == _key(character)]
    if not hit_:
        return False
    for k in hit_:
        del data["used"][k]
    save(path, data)
    return True


def describe(f: dict) -> str:
    how = SPELLS[f["how"]]["names"]["en"]
    return (f"{f['character']} as {f['creature']} ({how}{', by ' + f['by'] if f.get('by') else ''}): "
            f"HP {f['hp']['current']}/{f['hp']['max']}, AC {f.get('ac')}, speed {f.get('speed')}"
            + (f", until {f['until_text']}" if f.get("until_text") else "")
            + "".join(f"\n  ⚔ {a['name']}: {a['bonus']:+d}, {a['damage']['text'] if a['damage'] else '—'} {a['type']}" for a in f.get("attacks") or []))


def main(argv: list | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    ap = argparse.ArgumentParser(description="Wild Shape and Polymorph: a character in a beast's body.")
    ap.add_argument("--campaign", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("wild", help="the druid's Wild Shape")
    p.add_argument("character")
    p.add_argument("creature")
    p.add_argument("--moon", action="store_true", help="Circle of the Moon (read from the sheet otherwise)")
    p.add_argument("--free", action="store_true", help="no use spent (the DM's call)")
    p = sub.add_parser("spell", help="a spell changes the character's shape")
    p.add_argument("character")
    p.add_argument("creature")
    p.add_argument("--spell", required=True)
    p.add_argument("--by", default="", help="who cast it (their concentration holds it)")
    p.add_argument("--cr", default="", help="the target's level or CR, when the sheet does not give it")
    for cmd in ("hit", "heal"):
        p = sub.add_parser(cmd)
        p.add_argument("character")
        p.add_argument("points", type=int)
    for cmd in ("end", "rest", "show"):
        sub.add_parser(cmd).add_argument("character")
    sub.add_parser("list")
    args = ap.parse_args(argv)
    try:
        import media
        import paths
        camp = _campaign(args.campaign)
        path = data_path(camp)
        data = load(path)
        event, after = None, None
        lang = paths.campaign_language(camp)
        if args.cmd == "list":
            print("\n".join(describe(f) for f in data["forms"]) or "everyone is in their own shape")
            return 0
        if args.cmd == "show":
            f = form_of(data, args.character)
            print(describe(f) if f else f"{args.character} is in their own shape")
            return 0
        if args.cmd in ("wild", "spell"):
            name, text = _sheet(args.character, camp)
            if form_of(data, name):
                raise ShapeError(f"{name} is already in another shape — shapes.py end first")
            form = make_form(srd_creature(args.creature), lang)
            level, moon = druid_level(text)
            if args.cmd == "wild":
                how = "wild"
                if not level:
                    raise ShapeError(f"{name} is not a druid (the sheet's Class)")
                moon = moon or args.moon
                check(form, how, level, moon)
                left = uses_left(data, name, level)
                if left == 0 and not args.free:
                    raise ShapeError(f"{name} has used both Wild Shapes — a short or long rest brings them back")
                hours = level // 2
                if not args.free and left is not None:
                    data["used"][name] = int(data["used"].get(name, 0)) + 1
            else:
                how = spell_of(args.spell)
                lv = re.search(r"\*\*Level:\*\*\s*(\d+)", text)
                target = float(args.cr) if args.cr else float(lv.group(1)) if lv else None
                if SPELLS[how].get("cr") == "level" and target is None:
                    raise ShapeError("the target's level or CR? --cr")
                check(form, how, target_cr=target)
                hours = SPELLS[how]["hours"]
            now = _now_hours(camp)
            form.update({"character": name, "how": how, "by": args.by if args.cmd == "spell" else "",
                         "until_h": None if now is None or not hours else now + hours,
                         "until_text": f"{hours} h" if hours else "", "ts": time.time()})
            data["forms"].append(form)
            event = {"what": "start", "name": name, "creature": form["creature"]}
            print(describe(form))
            if how == "wild":
                left = uses_left(data, name, level)
                print(f"  Wild Shape: {('unlimited' if left is None else str(left) + ' use(s) left')}; up to {hours} hour(s). "
                      "Keeps INT, WIS, CHA, skills and saves proficiencies; cannot cast spells; concentration holds.")
            else:
                print(f"  {SPELLS[how]['names']['en']}: up to {hours} hour(s)" + (f", while {args.by} concentrates" if args.by else "")
                      + "; the form's INT, WIS and CHA too (Polymorph), no speech, no spells.")
            if now is None:
                print("  (no calendar: end it with shapes.py end when the time is up)")
        elif args.cmd in ("hit", "heal"):
            f = form_of(data, args.character)
            if not f:
                raise ShapeError(f"{args.character} is in their own shape — the damage goes on their sheet")
            if args.cmd == "heal":
                got = heal(f, args.points)
                print(f"{f['character']} ({f['creature']}): +{got} → {f['hp']['current']}/{f['hp']['max']}")
                event = {"what": "hp", "name": f["character"]}
            else:
                r = hit(f, args.points)
                if r["ended"]:
                    data["forms"].remove(f)
                    print(f"{f['character']}: the {f['creature']} drops to 0.")
                    event = {"what": "ended", "name": f["character"]}
                    after = (f["character"], r["excess"])
                else:
                    print(f"{f['character']} ({f['creature']}): -{r['taken']} → {r['left']}/{f['hp']['max']}")
                    event = {"what": "hp", "name": f["character"]}
        elif args.cmd == "end":
            f = remove(data, args.character)
            event = {"what": "ended", "name": f["character"]}
            after = (f["character"], 0)
        elif args.cmd == "rest":
            name, _text = _sheet(args.character, camp)
            data["used"].pop(next((k for k in data["used"] if _key(k) == _key(name)), ""), None)
            print(f"{name}: Wild Shape uses back")
        save(path, data)
        if camp == media.active_campaign():
            _announce(event)               # first the form leaves the display, so the HP push below is not taken as damage to it
        if after:
            print(back_to_normal(*after))
    except ShapeError as e:
        print(f"shapes: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
