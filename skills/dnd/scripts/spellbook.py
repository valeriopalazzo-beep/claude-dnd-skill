#!/usr/bin/env python3
"""spellbook.py — a caster's spells and slots, read from and written to the sheet.

The players' Spellbook panel is built on this: the spells a character knows
(the sheet's "Known Spells / Cantrips"), the slots left (its "Spell Slots"
table) and, for the spells that are in the SRD, their level, range, duration
and text. Casting from the panel marks the slot on the sheet at once and
tells the DM; the same can be done from here.

    spellbook.py -c <campaign> show "Vardamir"
    spellbook.py -c <campaign> cast "Vardamir" "parola guaritrice"          # marks a slot of the spell's level
    spellbook.py -c <campaign> cast "Vardamir" "cura ferite" --level 2       # cast with a higher slot
    spellbook.py -c <campaign> restore "Vardamir" --level 1                  # one slot of that level back
    spellbook.py -c <campaign> restore "Vardamir"                            # all of them: a long rest

A spell is named by any part of its name that means one of the character's
spells only. A cantrip uses no slot. Anything that cannot be done exits
non-zero and leaves the sheet as it was.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import paths  # noqa: E402  (also forces UTF-8 output on Windows)
import inventory  # noqa: E402  (finding, reading and writing a sheet)

_SLOTS_HEAD = re.compile(r"^##\s+Spell Slots\b.*$", re.M)
_SPELLS_HEAD = re.compile(r"^##\s+(?:Known Spells|Spells Known|Prepared Spells|Spells)\b.*$", re.M)
_NEXT_HEAD = re.compile(r"^##\s", re.M)
_ROW = re.compile(r"^(\|\s*)(\d+)([^|]*\|\s*)(\d+)(\s*\|\s*)(\d+)(\s*\|.*)$")
_BULLET = re.compile(r"^\s*[-*]\s+(?:\*\*(.+?):?\*\*:?\s*)?(.*)$")
_CANTRIP = re.compile(r"cantrip|trucchett", re.I)
_PLACEHOLDERS = {"", "-", "—", "–", "none", "nessuno", "nessuna", "(none)", "(nessuno)", "(nessuna)"}
_ORDINAL = {1: "1st", 2: "2nd", 3: "3rd"}
MAX_LEVEL = 9


class SpellError(Exception):
    """A request that cannot be applied; the sheet is left as it was."""


def ordinal(level: int) -> str:
    return _ORDINAL.get(level, f"{level}th")


def _key(text: str) -> str:
    return " ".join((text or "").split()).casefold()


# ─── Reading the sheet ────────────────────────────────────────────────────────

def _section(text: str, head: re.Pattern) -> tuple:
    """(start, end) of the lines under a `## ` heading — (0, 0) when the sheet has none."""
    m = head.search(text)
    if not m:
        return 0, 0
    nxt = _NEXT_HEAD.search(text, m.end())
    return m.end(), nxt.start() if nxt else len(text)


def read_slots(text: str) -> dict:
    """The Spell Slots table: {level: {"max": n, "used": n}}."""
    start, end = _section(text, _SLOTS_HEAD)
    slots = {}
    for line in text[start:end].splitlines():
        m = _ROW.match(line.rstrip("\r"))
        if m and 1 <= int(m.group(2)) <= MAX_LEVEL:
            total = int(m.group(4))
            slots[int(m.group(2))] = {"max": total, "used": min(int(m.group(6)), total)}
    return slots


def write_used(text: str, level: int, used: int) -> str:
    """The sheet with the Used cell of one level changed, and nothing else."""
    start, end = _section(text, _SLOTS_HEAD)
    out, done = [], False
    for line in text[start:end].splitlines(keepends=True):
        body = line.rstrip("\r\n")
        m = _ROW.match(body)
        if m and not done and int(m.group(2)) == level:
            line = "".join(m.group(1, 2, 3, 4, 5)) + str(used) + m.group(7) + line[len(body):]
            done = True
        out.append(line)
    if not done:
        raise SpellError(f"the sheet has no {ordinal(level)}-level slots")
    return text[:start] + "".join(out) + text[end:]


def casting_line(text: str) -> str:
    """The line under the slots that says the casting ability, save DC and attack bonus."""
    start, end = _section(text, _SLOTS_HEAD)
    for line in text[start:end].splitlines():
        line = line.strip()
        if line.startswith("*") and line.endswith("*") and _key(line.strip("*")) not in _PLACEHOLDERS:
            return line.strip("*").strip()
    return ""


def _split_names(text: str) -> list:
    """'A, B (note, with a comma), C' → the three names; commas inside brackets do not split."""
    names, depth, cur = [], 0, ""
    for ch in text:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if ch in ",;" and depth == 0:
            names.append(cur)
            cur = ""
        else:
            cur += ch
    return [n.strip() for n in names + [cur] if n.strip()]


def read_spells(text: str) -> list:
    """The Known Spells section: [{"name", "level" (None when the sheet does not say), "note"}]."""
    start, end = _section(text, _SPELLS_HEAD)
    spells, seen = [], set()
    for line in text[start:end].splitlines():
        m = _BULLET.match(line.rstrip("\r"))
        if not m:
            continue
        label, rest = (m.group(1) or "").strip(), m.group(2).strip()
        level = None
        if label:
            digits = re.search(r"\d+", label)
            level = 0 if _CANTRIP.search(label) else int(digits.group()) if digits else None
            if level is not None and level > MAX_LEVEL:
                level = None
        for raw in _split_names(rest):
            name = re.sub(r"\s*[\(\[].*$", "", raw).strip(" *_")
            note = raw[len(name):].strip(" ()[]") if raw.startswith(name) else ""
            if _key(name) in _PLACEHOLDERS or _key(name) in seen:
                continue
            seen.add(_key(name))
            spells.append({"name": name, "level": level, "note": note})
    return spells


def known(spells: list, name: str) -> dict:
    """The spell `name` means: the exact name first, else a part only one spell has."""
    want = _key(name)
    if not want:
        raise SpellError("which spell?")
    hits = [s for s in spells if _key(s["name"]) == want] or [s for s in spells if want in _key(s["name"])]
    if not hits:
        raise SpellError(f"{name!r} is not among the character's spells"
                         + (f" ({', '.join(s['name'] for s in spells)})" if spells else " — the sheet lists none"))
    if len(hits) > 1:
        raise SpellError(f"{name!r} matches several spells: " + ", ".join(s["name"] for s in hits))
    return hits[0]


# ─── The SRD behind a spell ───────────────────────────────────────────────────

def srd(name: str, ruleset: str = "2014", lang: str = "en") -> dict:
    """What the SRD says of a spell, worded for the table's language — {} when it is not there."""
    try:
        import lookup
        rec = lookup.lookup_record(name, category="spell", ruleset=ruleset)
        if not rec or rec.get("_cat") not in (None, "spell", "spells"):
            return {}
        loc = (lookup._localize(rec, lang) if lang and lang != "en" else None) or rec
    except Exception:
        return {}
    return {"srd": rec.get("name", ""), "level": int(rec.get("level", 0) or 0), "school": loc.get("school", ""),
            "time": loc.get("casting_time", ""), "range": loc.get("range", ""), "duration": loc.get("duration", ""),
            "components": ", ".join(loc.get("components") or []), "concentration": bool(rec.get("concentration")),
            "ritual": bool(rec.get("ritual"))}


def book(text: str, ruleset: str = "2014", lang: str = "en") -> dict:
    """Everything the Spellbook panel shows, from one sheet."""
    spells = []
    for s in read_spells(text):
        info = srd(s["name"], ruleset, lang)
        level = s["level"] if s["level"] is not None else info.get("level")
        spells.append({"name": s["name"], "note": s["note"], "level": level, "known": bool(info),
                       **{k: info.get(k, "" if k not in ("concentration", "ritual") else False)
                          for k in ("school", "time", "range", "duration", "components", "concentration", "ritual")}})
    spells.sort(key=lambda s: (s["level"] is None, s["level"] or 0))
    return {"casting": casting_line(text), "slots": {str(k): v for k, v in sorted(read_slots(text).items())},
            "spells": spells}


# ─── Casting ──────────────────────────────────────────────────────────────────

def cast(text: str, spell: str, level=None, ruleset: str = "2014", lang: str = "en", slots: dict | None = None) -> tuple:
    """Mark the slot a casting uses. Returns (the new sheet, what happened).

    What happened: {"spell", "level" (the spell's), "slot" (the slot used, 0 for a
    cantrip), "left", "max", "concentration"}. `slots` is the count to trust when
    it is not the sheet's own ({level: {"max", "used"}} — the display's live one).
    """
    entry = known(read_spells(text), spell)
    info = srd(entry["name"], ruleset, lang)
    base = entry["level"] if entry["level"] is not None else info.get("level")
    if base is None and level is None:
        raise SpellError(f"the sheet does not say what level {entry['name']} is — say which slot it takes")
    try:
        slot = int(level) if level not in (None, "") else int(base)
    except (TypeError, ValueError):
        raise SpellError(f"a slot level is a number (got {level!r})")
    base = slot if base is None else int(base)
    done = {"spell": entry["name"], "level": base, "slot": 0, "left": 0, "max": 0,
            "concentration": bool(info.get("concentration"))}
    if base == 0:
        return text, done                              # a cantrip costs nothing
    if not base <= slot <= MAX_LEVEL:
        raise SpellError(f"{entry['name']} is a {ordinal(base)}-level spell: it takes a slot of that level or higher")
    on_sheet = read_slots(text)
    have = (slots if slots is not None else on_sheet).get(slot)
    if not have or have["used"] >= have["max"]:
        raise SpellError(f"no {ordinal(slot)}-level slot left")
    done.update(slot=slot, left=have["max"] - have["used"] - 1, max=have["max"])
    if slot in on_sheet:
        text = write_used(text, slot, min(have["used"] + 1, on_sheet[slot]["max"]))
    return text, done


def restore(text: str, level=None) -> tuple:
    """Give slots back: one of `level`, or every slot when no level is given. Returns (sheet, how many)."""
    slots = read_slots(text)
    if level in (None, ""):
        count = 0
        for lvl, s in slots.items():
            if s["used"]:
                text = write_used(text, lvl, 0)
                count += s["used"]
        return text, count
    try:
        lvl = int(level)
    except (TypeError, ValueError):
        raise SpellError(f"a slot level is a number (got {level!r})")
    if lvl not in slots:
        raise SpellError(f"the sheet has no {ordinal(lvl)}-level slots")
    if not slots[lvl]["used"]:
        raise SpellError(f"every {ordinal(lvl)}-level slot is there already")
    return write_used(text, lvl, slots[lvl]["used"] - 1), 1


# ─── Telling the DM what a player cast from their own screen ──────────────────

def _casts_file() -> str:
    return str(paths.runtime_dir() / "spell_casts.json")


def casts_add(note: dict) -> None:
    path = _casts_file()
    try:
        with open(path, encoding="utf-8") as f:
            notes = json.load(f)
        if not isinstance(notes, list):
            notes = []
    except (OSError, ValueError):
        notes = []
    notes.append(note)
    try:
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(notes, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def format_cast(note: dict) -> str:
    who, spell = note.get("character", "?"), note.get("spell", "?")
    save = note.get("concentration_save")
    if save == "down":
        return (f"[[CONCENTRATION: {who} dropped to 0 HP and stopped concentrating on {spell} — the spell ends now; "
                "the display has already cleared it. Narrate what ends with it.]]")
    if save in ("kept", "lost"):
        roll = (f"{who} took {note.get('damage', '?')} damage and rolled {note.get('total', '?')} on the "
                f"Constitution save against DC {note.get('dc', '?')}")
        if save == "kept":
            return f"[[CONCENTRATION: {roll}: still concentrating on {spell}.]]"
        return (f"[[CONCENTRATION: {roll}: concentration on {spell} is broken — the spell ends now; "
                "the display has already cleared it. Narrate what ends with it.]]")
    if note.get("dropped"):
        return f"[[SPELL: {who} stopped concentrating on {spell}.]]"
    if not note.get("slot"):
        what = f"{who} cast the cantrip {spell} from the spellbook"
    else:
        what = (f"{who} cast {spell} from the spellbook with a {ordinal(int(note['slot']))}-level slot "
                f"({note.get('left', '?')} of {note.get('max', '?')} left) — the slot is already marked on the sheet "
                "and on the display, do not mark it again")
    if note.get("concentration"):
        what += f". It needs concentration: the display shows {who} concentrating on it"
    return f"[[SPELL: {what}. Resolve it from what the player says; if they did not say on whom or what, ask.]]"


def drain_casts() -> list:
    """Castings not yet reported, as lines for the DM; clears the list."""
    path = _casts_file()
    try:
        with open(path, encoding="utf-8") as f:
            notes = json.load(f)
        os.remove(path)
    except (OSError, ValueError):
        return []
    return [format_cast(n) for n in notes if isinstance(n, dict)]


# ─── Command line ─────────────────────────────────────────────────────────────

def _show(name: str, data: dict) -> str:
    lines = [name + (f" — {data['casting']}" if data["casting"] else "")]
    lines += [f"  {ordinal(int(lvl))} level: {s['max'] - s['used']} of {s['max']} slots left" for lvl, s in data["slots"].items()]
    for s in data["spells"]:
        level = "cantrip" if s["level"] == 0 else "level ?" if s["level"] is None else f"level {s['level']}"
        tags = [t for t, on in (("concentration", s["concentration"]), ("ritual", s["ritual"]), ("not in the SRD", not s["known"])) if on]
        lines.append(f"  · {s['name']} ({level})" + (f" [{', '.join(tags)}]" if tags else ""))
    if not data["spells"]:
        lines.append("  no spells on the sheet")
    return "\n".join(lines)


def find_sheet(character: str, campaign: str = "") -> pathlib.Path:
    """The character's sheet: by the whole name, else by a part only one character of the campaign has."""
    try:
        return inventory.find_sheet(character, campaign)
    except inventory.InventoryError as e:
        want = _key(character)
        try:
            folder = paths.find_campaign(campaign) / "characters" if campaign else None
            files = sorted(folder.glob("*.md")) if folder else []
        except Exception:
            files = []
        hits = [f for f in files if want and want in _key(inventory.display_name(f))]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise SpellError(f"{character!r} matches several characters: " + ", ".join(inventory.display_name(f) for f in hits))
        raise SpellError(str(e))


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="A caster's spells and slots, on the sheet.")
    ap.add_argument("--campaign", "-c", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show").add_argument("character")
    p = sub.add_parser("cast", help="mark the slot a casting uses")
    p.add_argument("character")
    p.add_argument("spell")
    p.add_argument("--level", default=None, help="the slot used, when higher than the spell's level")
    p = sub.add_parser("restore", help="give slots back: one of --level, or all (a long rest)")
    p.add_argument("character")
    p.add_argument("--level", default=None)
    args = ap.parse_args(argv)

    try:
        camp = args.campaign or inventory.active_campaign()
        path = find_sheet(args.character, camp)
        name = inventory.display_name(path)
        text = inventory._read(path)
        ruleset = paths.campaign_ruleset(camp) if camp else "2014"
        lang = paths.campaign_language(camp) if camp else "en"
        if args.cmd == "show":
            print(_show(name, book(text, ruleset, lang)))
            return 0
        if args.cmd == "cast":
            text, done = cast(text, args.spell, args.level, ruleset, lang)
            print(f"{name} casts {done['spell']}: " + ("a cantrip, no slot" if not done["slot"] else
                  f"{ordinal(done['slot'])}-level slot used, {done['left']} of {done['max']} left")
                  + (" — concentration" if done["concentration"] else ""))
        else:
            text, count = restore(text, args.level)
            print(f"{name}: {count} slot(s) back")
        inventory._write(path, text)
        slots = {str(k): v for k, v in read_slots(text).items()}
        if slots and (not args.campaign or args.campaign == inventory.active_campaign()):
            inventory._push(["--player", name, "--spell-slots", json.dumps(slots)])
    except (SpellError, inventory.InventoryError) as e:
        print(f"spellbook: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
