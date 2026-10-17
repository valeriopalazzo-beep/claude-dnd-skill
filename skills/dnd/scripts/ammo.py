#!/usr/bin/env python3
"""ammo.py — arrows, bolts, sling bullets and needles, counted on the sheet.

A weapon with the Ammunition property spends one piece with every attack, and
after the battle half of what was spent can be found again with a minute of
searching (PHB 2014 p. 146). The Attacks panel spends it by itself when a
player shoots; this script does the rest:

    ammo.py -c <campaign> show "Lyra"                 # what she has left, by kind
    ammo.py -c <campaign> spend "Lyra" [--kind arrow] [--n 2]
    ammo.py -c <campaign> spent                       # what this fight has cost so far
    ammo.py -c <campaign> recover                     # the fight is over: half of it back
    ammo.py -c <campaign> recover --who "Lyra" --none # nothing to find (shot into a chasm)

The count is read from the sheet's equipment, however it is written:
`Frecce ×20`, `20 frecce`, `Faretra con 20 frecce`, `Arco corto con faretra e
20 frecce`, `Frecce (20)`. Spending rewrites that number and nothing else; a
line that is only the ammunition goes when the last piece is shot. Kinds:
arrow (frecce), bolt (quadrelli), bullet (proiettili da fionda), needle (aghi
da cerbottana). What a fight has spent is kept in <campaign>/ammo.json until
`recover` (tracker.py clear does it too, at the end of the encounter).
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
import inventory  # noqa: E402

KINDS = ("arrow", "bolt", "bullet", "needle")
# The words a sheet uses for each kind (singular and plural), and for the weapons that shoot it.
WORDS = {
    "arrow": ("frecce", "freccia", "arrows", "arrow"),
    "bolt": ("quadrelli", "quadrello", "dardi da balestra", "crossbow bolts", "crossbow bolt", "bolts", "bolt"),
    "bullet": ("proiettili da fionda", "proiettile da fionda", "proiettili", "proiettile", "biglie", "biglia",
               "sling bullets", "sling bullet", "bullets", "bullet"),
    "needle": ("aghi da cerbottana", "ago da cerbottana", "aghi", "blowgun needles", "blowgun needle", "needles", "needle"),
}
KIND_ALIASES = {"frecce": "arrow", "freccia": "arrow", "arrows": "arrow", "quadrelli": "bolt", "quadrello": "bolt",
                "bolts": "bolt", "proiettili": "bullet", "bullets": "bullet", "aghi": "needle", "needles": "needle"}
_WEAPONS = (("bolt", re.compile(r"\bbalestr|\bcrossbow", re.I)),
            ("needle", re.compile(r"\bcerbottan|\bblowgun", re.I)),
            ("bullet", re.compile(r"\bfiond|\bsling\b", re.I)),
            ("arrow", re.compile(r"\barc(?:o|hi)\b|\b(?:long|short)?bows?\b", re.I)))
NAMES = {"it": {"arrow": "frecce", "bolt": "quadrelli", "bullet": "proiettili", "needle": "aghi"},
         "en": {"arrow": "arrows", "bolt": "bolts", "bullet": "bullets", "needle": "needles"}}
_MAGIC = re.compile(r"\+\d|magic|magich|incantat|enchant", re.I)


class AmmoError(Exception):
    """A request that cannot be applied; nothing was changed."""


def find_sheet(character: str, campaign: str = "") -> pathlib.Path:
    """The sheet, by the whole name or by a part only one character of the campaign has."""
    import spellbook
    try:
        return spellbook.find_sheet(character, campaign)
    except spellbook.SpellError as e:
        raise AmmoError(str(e))


def kind_of(word: str) -> str:
    w = (word or "").strip().lower()
    w = KIND_ALIASES.get(w, w)
    if w not in KINDS:
        raise AmmoError(f"the kind is one of {', '.join(KINDS)} — not {word!r}")
    return w


def weapon_kind(name: str, notes: str = "") -> str:
    """The ammunition a weapon shoots, from its name: 'Arco lungo' → 'arrow'; '' for a weapon that needs none."""
    for kind, pat in _WEAPONS:
        if pat.search(name or ""):
            return kind
    if re.search(r"\bmunizion|\bammunition", notes or "", re.I):
        for kind, pat in _WEAPONS:
            if pat.search(notes or ""):
                return kind
    return ""


# ─── Reading and writing the count on the sheet ──────────────────────────────

def _word_re(kind: str) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in WORDS[kind]) + r")\b", re.I)


def stacks(text: str, kind: str) -> list:
    """The equipment lines that hold this kind: [{"start", "end", "line", "count", "num": (s, e) | None,
    "qty": bool, "alone": bool, "magic": bool}] — "num" is where the count sits in the text."""
    word = _word_re(kind)
    out = []
    try:
        items = inventory._items(text)
    except inventory.InventoryError:          # no equipment section on this sheet: nothing to count
        return []
    for s, e, _sec, name, qty in items:
        line = text[s:e]
        m = word.search(line)
        if not m:
            continue
        entry = {"start": s, "end": e, "line": line, "count": None, "num": None, "qty": False,
                 "alone": False, "magic": bool(_MAGIC.search(line))}
        q = re.search(r"\s*[×x]\s*(\d+)\s*$", line)
        before = re.search(r"(\d{1,4})\s+(?:[^\W\d]+\s+){0,2}$", line[:m.start()])
        after = re.match(r"\s*(?:\(\s*(\d{1,4})\s*\)|:\s*(\d{1,4})\b)", line[m.end():])
        if q:
            entry.update(count=int(q.group(1)), num=(s + q.start(1), s + q.end(1)), qty=True)
        elif before:
            entry.update(count=int(before.group(1)), num=(s + before.start(1), s + before.end(1)))
        elif after:
            g = 1 if after.group(1) else 2
            entry.update(count=int(after.group(g)), num=(s + m.end() + after.start(g), s + m.end() + after.end(g)))
        rest = line
        if entry["num"]:
            rest = line[:entry["num"][0] - s] + line[entry["num"][1] - s:]
        rest = word.sub("", re.sub(r"^[-*]\s+", "", rest))
        entry["alone"] = not re.sub(r"[\s×x()\[\]:,.\-]+|\b(?:da|di|per|d)\b", "", rest, flags=re.I)
        out.append(entry)
    return out


def count(text: str, kind: str) -> int | None:
    """How many pieces of this kind the sheet holds; None when it has none written with a number."""
    known = [st["count"] for st in stacks(text, kind) if st["count"] is not None]
    return sum(known) if known else None


def _drop_line(text: str, st: dict) -> str:
    s, e = st["start"], st["end"]
    nl = 2 if text[e:e + 2] == "\r\n" else (1 if text[e:e + 1] == "\n" else 0)
    return text[:s] + text[e + nl:]


def _set(text: str, st: dict, n: int) -> str:
    if n <= 0 and st["alone"]:
        return _drop_line(text, st)
    a, b = st["num"]
    return text[:a] + str(max(0, n)) + text[b:]


def spend(text: str, kind: str, n: int = 1) -> tuple:
    """(new text, pieces left). Spends from the plain stacks first, the magic ones last."""
    if n < 1:
        raise AmmoError("spend at least one")
    piles = sorted([st for st in stacks(text, kind) if st["count"]], key=lambda st: st["magic"])
    have = sum(st["count"] for st in piles)
    if not piles:
        raise AmmoError(f"no {NAMES['en'][kind]} on the sheet" if not stacks(text, kind) else
                        f"the sheet does not say how many {NAMES['en'][kind]} — write the number (\"Frecce ×20\")")
    if have < n:
        raise AmmoError(f"only {have} {NAMES['en'][kind]} left")
    left = n
    for st in piles:                         # one pile at a time, re-reading the text after each change
        if not left:
            break
        cur = next(x for x in stacks(text, kind) if x["line"] == st["line"])
        take = min(left, cur["count"])
        text = _set(text, cur, cur["count"] - take)
        left -= take
    return text, have - n


def give_back(text: str, kind: str, n: int) -> str:
    """Put n pieces back: on the first plain stack, or on a new line of the gear when none is left."""
    if n < 1:
        return text
    piles = [st for st in stacks(text, kind) if st["num"] and not st["magic"]] or \
            [st for st in stacks(text, kind) if st["num"]]
    if piles:
        st = piles[0]
        return _set(text, st, st["count"] + n)
    return inventory.add_item(text, WORDS[kind][0].capitalize(), n, "gear")


# ─── This fight's spending ───────────────────────────────────────────────────

def data_path(campaign: str) -> pathlib.Path:
    return paths.find_campaign(campaign) / "ammo.json"


def load(campaign: str) -> dict:
    try:
        data = json.loads(data_path(campaign).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"spent": {}}
    return data if isinstance(data, dict) and isinstance(data.get("spent"), dict) else {"spent": {}}


def save(campaign: str, data: dict) -> None:
    path = data_path(campaign)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def note_spent(campaign: str, character: str, kind: str, n: int = 1) -> None:
    data = load(campaign)
    who = data["spent"].setdefault(character, {})
    who[kind] = int(who.get(kind, 0)) + n
    save(campaign, data)


def spend_on_sheet(path, kind: str, campaign: str = "", n: int = 1) -> int:
    """Spend from this sheet file and remember it for recover. Pieces left."""
    path = pathlib.Path(path)
    new, left = spend(inventory._read(path), kind, n)
    inventory._write(path, new)
    if campaign:
        try:
            note_spent(campaign, inventory.display_name(path), kind, n)
        except Exception as e:      # the shot is taken anyway; only the half to find again is lost
            print(f"ammo: what was spent is not remembered ({e})", file=sys.stderr)
    _refresh(path, campaign)
    return left


def op_spend(character: str, kind: str = "", n: int = 1, campaign: str = "") -> tuple:
    """Spend from the sheet and remember it for recover: (name, kind, left)."""
    camp = campaign or inventory.active_campaign()
    path = find_sheet(character, camp)
    if not kind:
        text = inventory._read(path)
        have = [k for k in KINDS if count(text, k)]
        if len(have) != 1:
            raise AmmoError("which kind? --kind " + "|".join(have or KINDS))
        kind = have[0]
    left = spend_on_sheet(path, kind, camp, n)
    return inventory.display_name(path), kind, left


def recover(campaign: str, who: str = "", none: bool = False) -> list:
    """The battle is over: half of what each character spent, rounded down, back on the sheet. Lines to print."""
    data = load(campaign)
    lines, keep = [], {}
    for name, kinds in data["spent"].items():
        if who and inventory._key(who) not in inventory._key(name):
            keep[name] = kinds
            continue
        try:
            path = inventory.find_sheet(name, campaign)
        except inventory.InventoryError:
            continue
        text = inventory._read(path)
        got = []
        for kind, spent in kinds.items():
            if kind not in KINDS or int(spent) < 1:
                continue
            back = 0 if none else int(spent) // 2
            text = give_back(text, kind, back)
            got.append(f"{back} of {spent} {NAMES['en'][kind]}")
        if got:
            inventory._write(path, text)
            _refresh(path, campaign)
            lines.append(f"{inventory.display_name(path)} finds again " + ", ".join(got)
                         + (" (a minute of searching)" if not none else ""))
    data["spent"] = keep
    save(campaign, data)
    return lines


def _refresh(path, campaign: str) -> None:
    """The bag weighs less now (encumbrance.py pushes the load to the display)."""
    try:
        import encumbrance
        encumbrance.refresh(path, campaign)
    except Exception:
        pass


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ammunition counted on the sheet, half of it found again after a fight.")
    ap.add_argument("--campaign", "-c", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show").add_argument("character")
    p = sub.add_parser("spend")
    p.add_argument("character")
    p.add_argument("--kind", default="")
    p.add_argument("--n", type=int, default=1)
    sub.add_parser("spent")
    p = sub.add_parser("recover")
    p.add_argument("--who", default="")
    p.add_argument("--none", action="store_true", help="nothing can be found again")
    args = ap.parse_args(argv)
    try:
        camp = args.campaign or inventory.active_campaign()
        if args.cmd == "show":
            path = find_sheet(args.character, camp)
            text = inventory._read(path)
            print(inventory.display_name(path))
            found = False
            for kind in KINDS:
                for st in stacks(text, kind):
                    found = True
                    print(f"  {kind}: " + (str(st["count"]) if st["count"] is not None else "? (no number written)")
                          + f"  — {st['line'].lstrip('-* ').strip()}")
            if not found:
                print("  no ammunition on the sheet")
        elif args.cmd == "spend":
            name, kind, left = op_spend(args.character, kind_of(args.kind) if args.kind else "", args.n, camp)
            print(f"{name}: {left} {NAMES['en'][kind]} left")
        elif args.cmd == "spent":
            if not camp:
                raise AmmoError("no active campaign")
            spent = load(camp)["spent"]
            print("\n".join(f"{n}: " + ", ".join(f"{v} {NAMES['en'].get(k, k)}" for k, v in kinds.items())
                            for n, kinds in spent.items() if kinds) or "nothing spent in this fight")
        else:
            if not camp:
                raise AmmoError("no active campaign")
            print("\n".join(recover(camp, args.who, args.none)) or "nothing was spent: nothing to find")
    except (AmmoError, inventory.InventoryError) as e:
        print(f"ammo: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
