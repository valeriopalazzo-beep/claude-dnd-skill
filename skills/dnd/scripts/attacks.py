#!/usr/bin/env python3
"""attacks.py — a character's attacks, rolled in one go.

The players' Attack panel is built on this: the weapons and attack spells in
the sheet's "## Attacks" table (Name | Attack Bonus | Damage | Type | Notes),
rolled to hit and for damage together. A natural 20 doubles the damage dice.
A natural 1 misses, so no damage is rolled. A note that carries another damage ("Versatile: 1d10+4 a due mani") is offered
as a choice, one that adds dice ("Attacco furtivo +1d6") as an extra.

A row whose bonus is a save DC ("CD 12", "DC 13") is not an attack roll: a
breath weapon, a spell like Sacred Flame. Nothing is rolled to hit; the
damage is rolled once and the targets make the save — the ability is read
from the row ("TS Destrezza", "DEX save"), and "metà"/"half" in the notes says
a made save halves it (PHB 2014 p. 205).

    attacks.py -c <campaign> list "Ajeje"
    attacks.py -c <campaign> roll "Ajeje" "spadone" [--target "Goblin 1"] [--advantage|--disadvantage]
                                                  [--alt 1] [--extra 1]

The DM's own use is for roll_mode auto; with the display running the
players roll from their phones and the result reaches the DM as their action.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import secrets
import sys

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import paths  # noqa: E402,F401  (also forces UTF-8 output on Windows)
import inventory  # noqa: E402  (finding and reading a sheet)

_HEAD = re.compile(r"^##\s+(?:Attacks|Attacchi)\b.*$", re.M | re.I)
_NEXT_HEAD = re.compile(r"^##\s", re.M)
_DICE = re.compile(r"(\d+)\s*d\s*(\d+)(?:\s*([+-−])\s*(\d+))?", re.I)
_BONUS = re.compile(r"([+-−]?)\s*(\d+)")
_SAVE_DC = re.compile(r"\b(?:CD|DC)\s*:?\s*(\d{1,2})\b", re.I)
ADVANTAGE = ("normal", "advantage", "disadvantage")
# The full names in any case; the short ones only in capitals ("TS con vantaggio" is not a CON save).
_ABILITY_WORDS = {
    "STR": ("strength", "forza", "STR", "FOR"), "DEX": ("dexterity", "destrezza", "DEX", "DES"),
    "CON": ("constitution", "costituzione", "CON", "COS"), "INT": ("intelligence", "intelligenza", "INT"),
    "WIS": ("wisdom", "saggezza", "WIS", "SAG"), "CHA": ("charisma", "carisma", "CHA", "CAR"),
}
_ABILITY_NAMES = {"en": {"STR": "Strength", "DEX": "Dexterity", "CON": "Constitution", "INT": "Intelligence",
                         "WIS": "Wisdom", "CHA": "Charisma"},
                  "it": {"STR": "Forza", "DEX": "Destrezza", "CON": "Costituzione", "INT": "Intelligenza",
                         "WIS": "Saggezza", "CHA": "Carisma"}}
_HALF = re.compile(r"\bmet[àa]\b|\bdimezz|\bhalf\b", re.I)
_NO_DAMAGE = re.compile(r"nessun danno|no damage|nothing on a success", re.I)


class AttackError(Exception):
    """A request that cannot be rolled."""


def _key(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def parse_dice(text: str) -> dict | None:
    """'2d6+4' → {"count": 2, "sides": 6, "bonus": 4, "text": "2d6+4"}; None without dice."""
    m = _DICE.search(text or "")
    if not m:
        return None
    bonus = int(m.group(4) or 0) * (-1 if m.group(3) in ("-", "−") else 1)
    count, sides = int(m.group(1)), int(m.group(2))
    if not (1 <= count <= 40 and 2 <= sides <= 100):
        return None
    return {"count": count, "sides": sides, "bonus": bonus,
            "text": f"{count}d{sides}" + (f"{bonus:+d}" if bonus else "")}


def _note_options(note: str) -> tuple:
    """(other damages, extra dice) found in a note, each {"label", "dice"}."""
    alts, extras = [], []
    for part in re.split(r"[;.](?:\s|$)", note or ""):
        for m in _DICE.finditer(part):
            dice = parse_dice(m.group(0))
            if not dice:
                continue
            before = part[:m.start()]
            plus = before.rstrip().endswith("+")
            label = re.sub(r"[\s:(+]+$", "", before).strip(" -—,(") or part[m.end():].strip(" ,()")[:30]
            label = label[:40]
            if plus:
                extras.append({"label": label, "dice": dict(dice, bonus=0, text=f"{dice['count']}d{dice['sides']}")})
            else:
                alts.append({"label": label, "dice": dice})
            break
    return alts, extras


def save_of(bonus_cell: str, note: str) -> dict | None:
    """'CD 12' + 'TS Destrezza; metà se riesce' → {"dc": 12, "ability": "DEX", "half": True}; None for an attack roll.

    "half" is None when the row does not say what a made save does."""
    m = _SAVE_DC.search(bonus_cell or "")
    if not m:
        return None
    text = f"{bonus_cell} {note}"
    ability = ""
    for code, words in _ABILITY_WORDS.items():
        pat = "|".join(re.escape(w) if w.isupper() else "(?i:" + re.escape(w) + ")" for w in words)
        if re.search(rf"\b(?i:TS|save|saving throw|tiro salvezza)\s+(?i:su\s+|di\s+)?(?:{pat})\b"
                     rf"|\b(?:{pat})\s+(?i:save|saving throw)\b", text):
            ability = code
            break
    half = True if _HALF.search(note or "") else False if _NO_DAMAGE.search(note or "") else None
    return {"dc": int(m.group(1)), "ability": ability, "half": half}


def read_attacks(text: str) -> list:
    """The rows of the sheet's Attacks table: name, bonus (or save), damage, type, notes, alts, extras."""
    m = _HEAD.search(text or "")
    if not m:
        return []
    nxt = _NEXT_HEAD.search(text, m.end())
    body = text[m.end():nxt.start() if nxt else len(text)]
    rows = [ln.strip() for ln in body.splitlines() if ln.strip().startswith("|")]
    if len(rows) < 3:
        return []
    head = [c.strip().casefold() for c in rows[0].strip("|").split("|")]

    def col(*words, default=None):
        for i, h in enumerate(head):
            if any(w in h for w in words):
                return i
        return default

    i_bonus = col("bonus", "attack", "colpire", "attacco", default=1)
    i_dmg = col("damage", "dann", default=2)
    i_type = col("type", "tipo")
    i_note = col("note")
    out = []
    for row in rows[2:]:
        cells = [c.strip() for c in row.strip("|").split("|")]
        name = cells[0] if cells else ""
        if not name or name in ("—", "-") or set(name) <= {"-", " ", ":"}:
            continue
        cell = lambda i: cells[i] if i is not None and i < len(cells) else ""  # noqa: E731
        save = save_of(cell(i_bonus), cell(i_note))
        b = None if save else _BONUS.search(cell(i_bonus))
        bonus = None if not b else int(b.group(2)) * (-1 if b.group(1) in ("-", "−") else 1)
        alts, extras = _note_options(cell(i_note))
        out.append({"name": name, "bonus": bonus, "save": save, "damage": parse_dice(cell(i_dmg)), "type": cell(i_type),
                    "notes": cell(i_note), "alts": alts, "extras": extras})
    return out


def find(attacks: list, name: str) -> dict:
    want = _key(name)
    if not want:
        raise AttackError("which attack?")
    exact = [a for a in attacks if _key(a["name"]) == want]
    hits = exact or [a for a in attacks if want in _key(a["name"])]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise AttackError(f"no attack called {name!r} on the sheet")
    raise AttackError(f"{name!r} matches several attacks: " + ", ".join(a["name"] for a in hits))


def _d(sides: int, rng) -> int:
    return (rng.randbelow(sides) if rng else secrets.randbelow(sides)) + 1


def roll(attack: dict, advantage: str = "normal", alt: int = 0, extra: bool = False, rng=None) -> dict:
    """Roll to hit and for damage. `alt` 1.. picks another damage from the notes; `extra` adds the extra dice."""
    if advantage not in ADVANTAGE:
        advantage = "normal"
    if attack.get("save"):
        return _roll_save(attack, alt, rng)
    if attack.get("bonus") is None:
        raise AttackError(f"{attack['name']} has no attack bonus on the sheet")
    d20s = [_d(20, rng)] + ([_d(20, rng)] if advantage != "normal" else [])
    natural = max(d20s) if advantage == "advantage" else min(d20s) if advantage == "disadvantage" else d20s[0]
    out = {"attack": attack["name"], "advantage": advantage, "d20": d20s, "natural": natural,
           "bonus": attack["bonus"], "to_hit": natural + attack["bonus"], "crit": natural == 20, "fumble": natural == 1,
           "type": attack.get("type", ""), "damage": None, "damage_text": "", "dice": [], "extra_dice": [], "extra_label": ""}
    damage = attack.get("damage")
    if alt:
        if not 1 <= int(alt) <= len(attack.get("alts") or []):
            raise AttackError("no such alternative damage")
        damage = attack["alts"][int(alt) - 1]["dice"]
        out["alt_label"] = attack["alts"][int(alt) - 1]["label"]
    if damage and not out["fumble"]:            # a natural 1 always misses: no damage to roll
        times = 2 if out["crit"] else 1
        out["dice"] = [_d(damage["sides"], rng) for _ in range(damage["count"] * times)]
        total = sum(out["dice"]) + damage["bonus"]
        text = f"{damage['count'] * times}d{damage['sides']}" + (f"{damage['bonus']:+d}" if damage["bonus"] else "")
        if extra and attack.get("extras"):
            ex = attack["extras"][0]
            out["extra_dice"] = [_d(ex["dice"]["sides"], rng) for _ in range(ex["dice"]["count"] * times)]
            out["extra_label"] = ex["label"]
            total += sum(out["extra_dice"])
            text += f" +{ex['dice']['count'] * times}d{ex['dice']['sides']}"
        out["damage"] = max(0, total)
        out["damage_text"] = text
    return out


def _roll_save(attack: dict, alt: int = 0, rng=None) -> dict:
    """A save attack: no roll to hit, no critical; the damage once, for every target (PHB 2014 p. 196)."""
    damage = attack.get("damage")
    out = {"attack": attack["name"], "advantage": "normal", "d20": [], "natural": None, "bonus": None, "to_hit": None,
           "crit": False, "fumble": False, "save": dict(attack["save"]), "type": attack.get("type", ""),
           "damage": None, "damage_text": "", "dice": [], "extra_dice": [], "extra_label": ""}
    if alt:
        if not 1 <= int(alt) <= len(attack.get("alts") or []):
            raise AttackError("no such alternative damage")
        damage = attack["alts"][int(alt) - 1]["dice"]
        out["alt_label"] = attack["alts"][int(alt) - 1]["label"]
    if damage:
        out["dice"] = [_d(damage["sides"], rng) for _ in range(damage["count"])]
        out["damage"] = max(0, sum(out["dice"]) + damage["bonus"])
        out["damage_text"] = damage["text"]
    return out


_WORDS = {
    "en": {"hit": "to hit", "dmg": "damage", "crit": "critical", "fumble": "natural 1", "adv": "advantage", "dis": "disadvantage",
           "save": "save", "dc": "DC", "half": "half on a success", "none": "nothing on a success"},
    "it": {"hit": "per colpire", "dmg": "danni", "crit": "critico", "fumble": "1 naturale", "adv": "vantaggio", "dis": "svantaggio",
           "save": "TS", "dc": "CD", "half": "metà a chi lo supera", "none": "niente a chi lo supera"},
}


def save_text(save: dict, lang: str = "en") -> str:
    """'TS Destrezza CD 12' / 'DC 12 Dexterity save' — 'DC 12 save' when the row does not name the ability."""
    w = _WORDS.get(lang, _WORDS["en"])
    ability = _ABILITY_NAMES.get(lang, _ABILITY_NAMES["en"]).get(save.get("ability") or "", "")
    if lang == "it":
        return f"TS {ability + ' ' if ability else ''}CD {save['dc']}"
    return f"DC {save['dc']} {ability + ' ' if ability else ''}save"


def _save_numbers(r: dict, lang: str) -> str:
    """'TS Destrezza CD 12 · 7 danni (acido, 2d6: 3, 4), metà a chi lo supera'."""
    w = _WORDS.get(lang, _WORDS["en"])
    line = save_text(r["save"], lang)
    if r["damage"] is not None:
        line += f" · {r['damage']} {w['dmg']} (" + (f"{r['type']}, " if r.get("type") else "") \
                + f"{r['damage_text']}: {', '.join(str(x) for x in r['dice'])})"
        if r["save"].get("half") is True:
            line += f", {w['half']}"
        elif r["save"].get("half") is False:
            line += f", {w['none']}"
    return line


def _numbers(r: dict, lang: str) -> str:
    """'20 to hit (d20 14 +6) · 11 damage (slashing, 2d6+4: 3, 4)', in the table's language."""
    if r.get("save"):
        return _save_numbers(r, lang)
    w = _WORDS.get(lang, _WORDS["en"])
    d20 = "d20 " + ("/".join(str(x) for x in r["d20"]) if len(r["d20"]) > 1 else str(r["d20"][0])) + f" {r['bonus']:+d}"
    if r["advantage"] != "normal":
        d20 += f", {w['adv'] if r['advantage'] == 'advantage' else w['dis']}"
    line = f"{r['to_hit']} {w['hit']} ({d20})"
    if r["crit"]:
        line += f" — {w['crit']}!"
    elif r["fumble"]:
        line += f" — {w['fumble']}"
    if r["damage"] is not None:
        dice = ", ".join(str(x) for x in r["dice"] + r["extra_dice"])
        line += f" · {r['damage']} {w['dmg']} (" + (f"{r['type']}, " if r.get("type") else "") + f"{r['damage_text']}: {dice})"
    return line


def story_line(character: str, target: str, r: dict, lang: str = "en") -> str:
    """The line the table reads: '⚔ Ajeje → Goblin 1 · Spadone: 20 to hit (d20 14 +6) · 11 damage (2d6+4: 3, 4)'."""
    return f"⚔ {character}" + (f" → {target}" if target else "") + f" · {r['attack']}: {_numbers(r, lang)}"


def action_line(target: str, r: dict, lang: str = "en") -> str:
    """The player's action, as their own screen shows it and the DM reads it:
    '⚔ Spadone → Goblin 1: 20 to hit (d20 14 +6) · 11 damage (…)' — already rolled."""
    name = r["attack"] + (f" ({r['alt_label']})" if r.get("alt_label") else "")
    return f"⚔ {name}" + (f" → {target}" if target else "") + f": {_numbers(r, lang)}"


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="A character's attacks, rolled to hit and for damage.")
    ap.add_argument("--campaign", "-c", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").add_argument("character")
    p = sub.add_parser("roll")
    p.add_argument("character")
    p.add_argument("attack")
    p.add_argument("--target", default="")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--advantage", action="store_true")
    g.add_argument("--disadvantage", action="store_true")
    p.add_argument("--alt", type=int, default=0, help="the other damage from the notes, 1 = the first")
    p.add_argument("--extra", action="store_true", help="add the extra dice from the notes (e.g. sneak attack)")
    args = ap.parse_args(argv)
    try:
        camp = args.campaign or inventory.active_campaign()
        import spellbook                       # finds a sheet by any part of the name
        path = spellbook.find_sheet(args.character, camp)
        name = inventory.display_name(path)
        attacks = read_attacks(inventory._read(path))
        if args.cmd == "list":
            print(name)
            for a in attacks:
                print(f"  {a['name']}: " + (save_text(a["save"]) if a.get("save") else
                                            f"{a['bonus']:+d}" if a["bonus"] is not None else "—")
                      + (f", {a['damage']['text']}" if a["damage"] else "") + (f" {a['type']}" if a["type"] else "")
                      + "".join(f" | or {x['dice']['text']} ({x['label']})" for x in a["alts"])
                      + "".join(f" | +{x['dice']['text']} {x['label']}" for x in a["extras"]))
            if not attacks:
                print("  no attacks on the sheet")
            return 0
        adv = "advantage" if args.advantage else "disadvantage" if args.disadvantage else "normal"
        r = roll(find(attacks, args.attack), adv, args.alt, args.extra)
        lang = paths.campaign_language(camp) if camp else "en"
        print(story_line(name, args.target, r, lang))
    except Exception as e:      # AttackError, InventoryError, SpellError (a name that means two characters)
        if not isinstance(e, (AttackError, inventory.InventoryError)) and type(e).__name__ != "SpellError":
            raise
        print(f"attacks: {e}.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
