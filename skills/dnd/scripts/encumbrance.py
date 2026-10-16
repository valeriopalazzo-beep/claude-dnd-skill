#!/usr/bin/env python3
"""encumbrance.py — how much a character carries, and what it costs them.

The weight comes from the sheet itself: every item under
`## Equipment & Inventory` is looked up in the SRD equipment (English or
Italian name, a pack by its contents' total), coins weigh 1 lb per 50, and
Strength sets the limits (PHB 2014, p. 176):

    standard rule   carrying capacity = STR × 15 lb; beyond it the character
                    cannot move more than 5 ft
    variant rule    over STR × 5  → encumbered: speed −10 ft
                    over STR × 10 → heavily encumbered: speed −20 ft and
                    disadvantage on ability checks, attack rolls and saving
                    throws that use STR, DEX or CON

The campaign picks the rule with an `encumbrance: variant` line in state.md
(standard when missing). Heavy armour worn without its Strength costs 10 ft
in the standard rule (the variant ignores it, PHB p. 176). Powerful Build doubles the numbers.

    encumbrance.py show "Ajeje"            # every item, its weight, the total and the limits
    encumbrance.py party                   # one line per character of the campaign
    encumbrance.py weight "Fiaschetta di grappa" 0.5kg   # an item the SRD does not know (lb or kg)
    encumbrance.py weight "Fiaschetta di grappa" --forget
    encumbrance.py push                    # send everyone's load to the display

An item the SRD does not know weighs nothing until `weight` gives it one;
`show` lists those. Weights given that way live in <campaign>/weights.json and
count for every character. Italian tables read kilograms (1 lb = 0.5 kg, as
in the Italian Player's Handbook).
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import unicodedata

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import paths  # noqa: E402  (also forces UTF-8 output on Windows)
import inventory  # noqa: E402

COINS_PER_LB = 50
# PHB 2014 equipment packs: the total of what is inside.
PACKS = {"burglars-pack": 44.5, "diplomats-pack": 36, "dungeoneers-pack": 61.5, "entertainers-pack": 38,
         "explorers-pack": 59, "priests-pack": 24, "scholars-pack": 10}
# How Italian sheets name the packs ("Zaino da dungeon", "Dotazione dell'esploratore").
_PACK_WORDS = (("dungeon", "dungeoneers-pack"), ("esplorat", "explorers-pack"), ("scassin", "burglars-pack"),
               ("avventurier", "dungeoneers-pack"), ("ladr", "burglars-pack"), ("diplomat", "diplomats-pack"), ("intratten", "entertainers-pack"),
               ("artist", "entertainers-pack"), ("sacerdot", "priests-pack"), ("chieric", "priests-pack"),
               ("studios", "scholars-pack"), ("erudit", "scholars-pack"))
_PACK_HEADS = ("zaino", "dotazione", "pacchetto", "kit", "equipaggiamento")
# Common Italian words that are not the dataset's names.
ALIASES = {"borraccia": "waterskin", "otre": "waterskin", "torce": "torch", "razione": "rations-1-day",
           "razioni": "rations-1-day", "corda": "rope-hempen-50-feet", "giaciglio": "bedroll",
           "sacco a pelo": "bedroll", "acciarino": "tinderbox", "pitoni": "piton", "chiodi da roccia": "piton", "chiodi da rocciatore": "piton",
           "arnesi da scasso": "thieves-tools", "strumenti da scasso": "thieves-tools",
           "kit da guaritore": "healers-kit", "lanterna schermabile": "lantern-hooded", "abiti": "clothes-common",
           "vestiti": "clothes-common", "accetta": "handaxe", "kit da erborista": "herbalism-kit",
           "scrigno per pergamene": "case-map-or-scroll", "custodia per pergamene": "case-map-or-scroll"}
# Things the equipment list leaves out, by the word a sheet starts them with (lb each).
MISC = {"pozione": 0.5, "pozioni": 0.5, "potion": 0.5, "fiala": 0.25, "vial": 0.25, "fiaschetta": 1,
        "pergamena": 0, "scroll": 0, "lettera": 0, "chiave": 0, "key": 0, "anello": 0, "ring": 0,
        "gemma": 0, "gem": 0, "moneta": 0, "pegno": 0}
# Ammunition is sold by the bundle; the dataset weighs the bundle. One piece:
AMMO = {"arrow": 1 / 20, "crossbow-bolt": 1.5 / 20, "sling-bullet": 1.5 / 20, "blowgun-needle": 1 / 50}
MIN_PREFIX = 4          # a dataset name shorter than this never matches the start of a longer one
RULES = ("standard", "variant")
_RULE_PAT = re.compile(r"^\s*[-*]?\s*\**encumbrance\**\s*:\s*\**\s*(standard|variant)", re.I | re.M)
_STR_PAT = re.compile(r"\|\s*STR\s*\|", re.I)
_POWERFUL = re.compile(r"powerful build|corporatura possente|costituzione possente", re.I)


class EncumbranceError(Exception):
    """A request that cannot be applied; nothing was changed."""


# ─── Names and weights ────────────────────────────────────────────────────────

def norm(name: str) -> str:
    """'Cotta di Maglia (indossata) ×2' → 'cotta di maglia': no accents, brackets or counts."""
    text = re.sub(r"\s*[×x]\s*\d+\s*$", " ", str(name or "").strip())
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", text)
    text = re.sub(r"[^a-z0-9' ]+", " ", text).replace("'", " ")
    return " ".join(text.split())


def split_qty(name: str) -> tuple[str, int]:
    """'10 torce' → ('torce', 10); 'Ascia (1) (l'altra persa)' → ('Ascia (l'altra persa)', 1)."""
    text = " ".join(str(name or "").split())
    qty = 1
    m = re.match(r"^(\d{1,4})\s+(?=\D)(.*)$", text)
    if m:
        qty, text = int(m.group(1)), m.group(2)
    m = re.search(r"\(\s*(\d{1,4})\s*\)", text)
    if m:
        qty = int(m.group(1))
        text = " ".join((text[:m.start()] + text[m.end():]).split())
    return text, qty


_INDEX: dict | None = None


def _index() -> dict:
    """normalised name → weight in lb, from the SRD (English and Italian names)."""
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    index, by_key = {}, {}
    try:
        data = json.loads(paths.srd_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    for rec in data.get("equipment") or []:
        key = rec.get("index") or ""
        weight = PACKS.get(key, AMMO.get(key, rec.get("weight")))
        if not isinstance(weight, (int, float)):
            continue
        by_key[key] = float(weight)
        index.setdefault(norm(rec.get("name", "")), float(weight))
    try:
        it = json.loads((paths.data_dir() / "i18n" / "it" / "equipment.json").read_text(encoding="utf-8"))
        for key, rec in (it.get("equipment") or {}).items():
            if key not in by_key:
                continue
            for name in [rec.get("name", "")] + list(rec.get("aliases") or []):
                if norm(name):
                    index.setdefault(norm(name), by_key[key])
    except (OSError, ValueError, AttributeError):
        pass
    for word, key in ALIASES.items():
        if key in by_key:
            index.setdefault(norm(word), by_key[key])
    for word, weight in MISC.items():
        index.setdefault(norm(word), float(weight))
    index.pop("", None)
    _INDEX = {"names": index, "keys": by_key}
    return _INDEX


def _prefix_hit(name: str, table: dict):
    """The weight of the longest name in `table` the item's name starts with (whole words)."""
    best = None
    for known, weight in table.items():
        if len(known) >= MIN_PREFIX and (name == known or name.startswith(known + " ")):
            if best is None or len(known) > len(best[0]):
                best = (known, weight)
    return best[1] if best else None


def _singulars(word: str) -> list:
    """Italian and English plurals back to the singulars they may come from: pugnali → pugnale, pugnalo."""
    out = []
    if word.endswith("ce") or word.endswith("ge"):
        out.append(word[:-1] + "ia")                       # torce → torcia, frecce → freccia
    if word.endswith("chi") or word.endswith("ghi"):
        out.append(word[:-2] + "o")                        # sacchi → sacco
    if word.endswith("i"):
        out += [word[:-1] + "e", word[:-1] + "o"]          # pugnali → pugnale, giavellotti → giavellotto
    if word.endswith("e"):
        out.append(word[:-1] + "a")                        # asce → ascia is above; lance → lancia too
    if word.endswith("es"):
        out.append(word[:-2])
    if word.endswith("s"):
        out.append(word[:-1])
    return out


def weight_of(name: str, extra: dict | None = None):
    """Weight in lb of ONE of the item, or None when nothing knows it."""
    n = norm(name)
    if not n:
        return None
    found = _weight_of(n, extra)
    if found is None:
        first, _, rest = n.partition(" ")
        for single in _singulars(first):
            found = _weight_of((single + " " + rest).strip(), extra)
            if found is not None:
                break
    return found


def _weight_of(n: str, extra: dict | None = None):
    for table in (extra or {}, _index()["names"]):
        if n in table:
            return float(table[n])
    words = n.split()
    if words and words[0] in _PACK_HEADS:
        for part, key in _PACK_WORDS:
            if part in n and key in _index()["keys"]:
                return _index()["keys"][key]
    for table in (extra or {}, _index()["names"]):
        hit = _prefix_hit(n, table)
        if hit is not None:
            return float(hit)
    return None


# ─── Campaign settings ────────────────────────────────────────────────────────

def _campaign_dir(campaign: str = ""):
    camp = campaign or inventory.active_campaign()
    if not camp:
        return None
    try:
        return paths.find_campaign(camp)
    except Exception:
        return None


def rule_of(campaign: str = "") -> str:
    folder = _campaign_dir(campaign)
    try:
        m = _RULE_PAT.search((folder / "state.md").read_text(encoding="utf-8", errors="replace")) if folder else None
    except OSError:
        m = None
    return m.group(1).lower() if m else "standard"


def _weights_path(campaign: str = ""):
    folder = _campaign_dir(campaign)
    return folder / "weights.json" if folder else None


def load_weights(campaign: str = "") -> dict:
    path = _weights_path(campaign)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path else {}
    except (OSError, ValueError):
        return {}
    return {norm(k): float(v) for k, v in (data or {}).items() if norm(k) and isinstance(v, (int, float))}


def save_weights(campaign: str, weights: dict) -> None:
    path = _weights_path(campaign)
    if not path:
        raise EncumbranceError("no active campaign — pass --campaign or load one first")
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(dict(sorted(weights.items())), ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def parse_weight(text: str, lang: str = "en") -> float:
    """'2lb' / '0,5 kg' / '3' (lb, or kg at an Italian table) → lb."""
    m = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(lb|lbs|libbre|kg|chili)?\s*", str(text or ""), re.I)
    if not m:
        raise EncumbranceError(f"not a weight: {text!r} (write e.g. 2lb or 1kg)")
    value = float(m.group(1).replace(",", "."))
    unit = (m.group(2) or ("kg" if lang == "it" else "lb")).lower()
    return value * 2 if unit in ("kg", "chili") else value


# ─── The sheet ────────────────────────────────────────────────────────────────

def strength(text: str) -> int:
    """The STR score from the sheet's Ability Scores table; 10 when it cannot be read."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _STR_PAT.search(line) and "## Ability" in "\n".join(lines[max(0, i - 4):i]):
            cols = [c.strip() for c in line.strip().strip("|").split("|")]
            col = next((j for j, c in enumerate(cols) if c.upper() == "STR"), None)
            for row in lines[i + 1:i + 4]:
                cells = [c.strip() for c in row.strip().strip("|").split("|")]
                if col is not None and col < len(cells):
                    m = re.match(r"(\d{1,2})", cells[col])
                    if m:
                        return int(m.group(1))
    return 10


def _armour_penalty(items: list, score: int) -> list:
    """Heavy armour worn without its Strength: [(name, needed)]."""
    try:
        data = json.loads(paths.srd_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    need = {}
    for rec in data.get("equipment") or []:
        if rec.get("str_minimum"):
            need[rec.get("index")] = int(rec["str_minimum"])
    names = {}
    try:
        it = json.loads((paths.data_dir() / "i18n" / "it" / "equipment.json").read_text(encoding="utf-8"))
        names = {norm(r.get("name", "")): k for k, r in (it.get("equipment") or {}).items()}
    except (OSError, ValueError, AttributeError):
        pass
    for rec in data.get("equipment") or []:
        names.setdefault(norm(rec.get("name", "")), rec.get("index"))
    out = []
    for section, name, _qty, _w in items:
        key = names.get(norm(name))
        if section == "armour" and key in need and score < need[key]:
            out.append((name, need[key]))
    return out


def report(text: str, campaign: str = "", rule: str = "", weights: dict | None = None) -> dict:
    """Everything the sheet carries and what it means. Weights in lb."""
    weights = load_weights(campaign) if weights is None else weights
    rule = rule or rule_of(campaign)
    items, unknown, total = [], [], 0.0
    for _s, _e, section, raw, qty in inventory._items(text):
        name, more = split_qty(raw)
        qty *= more
        each = weight_of(name, weights)
        items.append((section, raw, qty, each))
        if each is None:
            unknown.append(raw)
        else:
            total += each * qty
    coins = sum(inventory.read_purse(text).values())
    coins_lb = coins / COINS_PER_LB
    total += coins_lb
    score = strength(text)
    mult = 2 if _POWERFUL.search(text) else 1
    capacity = score * 15 * mult
    limits = {"encumbered": score * 5 * mult, "heavy": score * 10 * mult, "capacity": capacity}
    if total > capacity:
        status, speed = "over", None            # only 5 ft
    elif rule == "variant" and total > limits["heavy"]:
        status, speed = "heavy", -20
    elif rule == "variant" and total > limits["encumbered"]:
        status, speed = "encumbered", -10
    else:
        status, speed = "ok", 0
    # PHB 2014 p. 176: the variant rule ignores the Strength column of the armour table.
    armour = _armour_penalty(items, score) if rule == "standard" else []
    if armour and speed is not None:
        speed -= 10
    return {"items": items, "unknown": unknown, "coins": coins, "coins_lb": round(coins_lb, 2),
            "total": round(total, 2), "strength": score, "rule": rule, "limits": limits, "status": status,
            "speed": speed, "armour": armour, "powerful": mult == 2}


def display_load(rep: dict) -> dict:
    """The part of a report the character card shows."""
    return {"lb": rep["total"], "capacity": rep["limits"]["capacity"], "encumbered": rep["limits"]["encumbered"],
            "heavy": rep["limits"]["heavy"], "status": rep["status"], "rule": rep["rule"],
            "speed": rep["speed"], "unknown": len(rep["unknown"]), "armour": bool(rep["armour"])}


# ─── Words ────────────────────────────────────────────────────────────────────

def fmt_weight(lb: float, lang: str = "en") -> str:
    if lang == "it":
        kg = lb / 2
        return (f"{kg:.1f}".rstrip("0").rstrip(".").replace(".", ",") or "0") + " kg"
    return (f"{lb:.1f}".rstrip("0").rstrip(".") or "0") + " lb"


def warning(rep: dict, lang: str = "en") -> str:
    """One line saying what the load costs, or '' when it costs nothing."""
    w = lambda lb: fmt_weight(lb, lang)   # noqa: E731
    lines = []
    if rep["status"] == "over":
        lines.append(f"OVER CAPACITY ({w(rep['total'])} of {w(rep['limits']['capacity'])}): speed 5 ft until they drop something")
    elif rep["status"] == "heavy":
        lines.append(f"HEAVILY ENCUMBERED ({w(rep['total'])}, over {w(rep['limits']['heavy'])}): speed −20 ft, "
                     "disadvantage on ability checks, attack rolls and saving throws with STR, DEX or CON")
    elif rep["status"] == "encumbered":
        lines.append(f"ENCUMBERED ({w(rep['total'])}, over {w(rep['limits']['encumbered'])}): speed −10 ft")
    for name, need in rep["armour"]:
        lines.append(f"{name} needs STR {need} (has {rep['strength']}): speed −10 ft")
    return "; ".join(lines)


def describe(name: str, rep: dict, lang: str = "en", full: bool = True) -> str:
    w = lambda lb: fmt_weight(lb, lang)   # noqa: E731
    lim = rep["limits"]
    head = (f"{name}: {w(rep['total'])} carried — STR {rep['strength']}"
            + (" (Powerful Build)" if rep["powerful"] else "")
            + (f", encumbered over {w(lim['encumbered'])}, heavily over {w(lim['heavy'])}" if rep["rule"] == "variant" else "")
            + f", capacity {w(lim['capacity'])} [{rep['rule']} rule]")
    lines = [head]
    if full:
        for _section, raw, qty, each in rep["items"]:
            lines.append(f"  {raw}: " + ("? (unknown — set it with `weight`)" if each is None
                                         else w(each * qty) + (f" ({qty} × {w(each)})" if qty > 1 else "")))
        if rep["coins"]:
            lines.append(f"  {rep['coins']} coins: {w(rep['coins_lb'])}")
    elif rep["unknown"]:
        lines.append(f"  no weight for: {', '.join(rep['unknown'])}")
    warn = warning(rep, lang)
    lines.append("  ⚠ " + warn if warn else "  no penalty")
    return "\n".join(lines)


# ─── The display ──────────────────────────────────────────────────────────────

def push(name: str, rep: dict) -> None:
    """Show the load on the character card. Silent when the display is not running."""
    try:
        sys.path.insert(0, str(paths.display_dir()))
        import send
        body = {"players": [{"name": name, "load": display_load(rep)}]}
        send._post(f"{send.BASE_URL}/stats", json.dumps(body).encode("utf-8"), send._read_token())
    except Exception:
        pass


def refresh(path, campaign: str = "", quiet: bool = False) -> str:
    """After an inventory change: push the new load and return the warning ('' when none)."""
    try:
        text = inventory._read(pathlib.Path(path))
        rep = report(text, campaign)
    except Exception:
        return ""
    push(inventory.display_name(pathlib.Path(path)), rep)
    camp = campaign or inventory.active_campaign()
    return warning(rep, paths.campaign_language(camp) if camp else "en")


def party_sheets(campaign: str = "") -> list:
    folder = _campaign_dir(campaign)
    if not folder:
        raise EncumbranceError("no active campaign — pass --campaign or load one first")
    names = []
    try:
        players = json.loads((paths.runtime_dir() / "stats.json").read_text(encoding="utf-8")).get("players") or []
        names = [p.get("name") for p in players if isinstance(p, dict) and p.get("name")]
    except (OSError, ValueError, AttributeError):
        pass
    sheets = []
    for n in names:
        try:
            sheets.append(inventory.find_sheet(n, campaign))
        except inventory.InventoryError:
            pass
    if not sheets:
        sheets = sorted((folder / "characters").glob("*.md"))
    return sheets


# ─── Command line ─────────────────────────────────────────────────────────────

def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="How much a character carries, and what it costs them.")
    ap.add_argument("--campaign", "-c", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show", help="every item, its weight and the limits").add_argument("character")
    sub.add_parser("party", help="one line per character")
    p = sub.add_parser("weight", help="give an item the SRD does not know a weight")
    p.add_argument("item")
    p.add_argument("weight", nargs="?", default="")
    p.add_argument("--forget", action="store_true")
    sub.add_parser("push", help="send everyone's load to the display")
    args = ap.parse_args(argv)
    camp = args.campaign or inventory.active_campaign()
    lang = paths.campaign_language(camp) if camp else "en"

    try:
        if args.cmd == "show":
            path = inventory.find_sheet(args.character, camp)
            rep = report(inventory._read(path), camp)
            print(describe(inventory.display_name(path), rep, lang))
            push(inventory.display_name(path), rep)
        elif args.cmd in ("party", "push"):
            for path in party_sheets(camp):
                rep = report(inventory._read(path), camp)
                if args.cmd == "party":
                    print(describe(inventory.display_name(path), rep, lang, full=False))
                push(inventory.display_name(path), rep)
            if args.cmd == "push":
                print("loads sent to the display")
        elif args.cmd == "weight":
            key = norm(args.item)
            if not key:
                raise EncumbranceError("which item?")
            weights = load_weights(camp)
            if args.forget:
                if weights.pop(key, None) is None:
                    raise EncumbranceError(f"no weight was set for {args.item!r}")
                print(f"- {args.item}")
            else:
                if not args.weight:
                    raise EncumbranceError("how much does it weigh? (e.g. 2lb or 1kg)")
                weights[key] = parse_weight(args.weight, lang)
                print(f"{args.item}: {fmt_weight(weights[key], lang)} each")
            save_weights(camp, weights)
    except (EncumbranceError, inventory.InventoryError) as e:
        print(f"encumbrance: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
