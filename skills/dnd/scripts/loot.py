#!/usr/bin/env python3
"""loot.py — random treasure that ends up on the sheets, not in the DM's memory.

    loot.py roll --cr 3 --monsters 4                 # what four CR 3 creatures carried
    loot.py roll --cr 3 --hoard                      # a lair, a vault, a chest worth the name
    loot.py roll --level 5 --hoard                   # by party level instead of CR
    loot.py set  --coins 340gp 20sp --item "Chiave d'argento" --item "Granato (100 mo) x2"
    loot.py show
    loot.py take --among "Aldric,Mira,Lio" --item "2:Mira" --item "3:Lio"
    loot.py take --to "Aldric"                       # everything left, to one character
    loot.py drop                                     # the party leaves it there

`roll` (or `set`, for treasure the adventure prescribes) puts the pile in
<campaign>/loot.json and prints it numbered. `take` hands it out through
inventory.py: `--among` splits the coins in equal shares (the odd copper goes
to the first name), `--item N:NAME` gives a numbered item (`N:NAME:2` two of a
stack), `--items-to NAME` gives every item left, `--to NAME` gives everything.
What nobody took stays in the pile for a later `take`. The same options on
`roll` / `set` hand the treasure out straight away.

Coins and valuables scale with the challenge rating in four tiers (CR 0–4,
5–10, 11–16, 17+). Magic items are drawn from the SRD by rarity; a spell
scroll comes with its spell. `--lang it` (default: the campaign's `language:`
flag) writes the names in that language. `--seed` makes a roll repeatable.
Every name is checked before anything is written: a wrong one changes nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import random
import re
import sys

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import paths  # noqa: E402  (also forces UTF-8 output on Windows)
import inventory  # noqa: E402

LOOT_FILE = "loot.json"


class LootError(Exception):
    """A request that cannot be applied; sheets and pile are left as they were."""


# ─── Tables ───────────────────────────────────────────────────────────────────
# Tier 0: CR 0–4 · 1: CR 5–10 · 2: CR 11–16 · 3: CR 17+.
# A coin entry is (dice, sides, multiplier, coin).

INDIVIDUAL = [
    [(2, [(4, 6, 1, "cp")]), (2, [(3, 6, 1, "sp")]), (1, [(2, 6, 1, "sp"), (1, 6, 1, "gp")]), (1, [(3, 6, 1, "gp")])],
    [(2, [(4, 6, 10, "sp"), (1, 6, 5, "gp")]), (2, [(2, 6, 10, "gp")]), (1, [(3, 6, 10, "gp")]),
     (1, [(2, 6, 10, "gp"), (1, 6, 1, "pp")])],
    [(2, [(2, 6, 100, "gp")]), (2, [(1, 6, 100, "gp"), (1, 6, 10, "pp")]), (1, [(3, 6, 100, "gp")]),
     (1, [(2, 6, 100, "gp"), (2, 6, 10, "pp")])],
    [(1, [(2, 6, 1000, "gp"), (1, 6, 100, "pp")]), (1, [(1, 6, 1000, "gp"), (2, 6, 100, "pp")])],
]

HOARD_COINS = [
    [(5, 6, 100, "cp"), (3, 6, 100, "sp"), (2, 6, 10, "gp")],
    [(2, 6, 100, "sp"), (6, 6, 100, "gp"), (3, 6, 10, "pp")],
    [(4, 6, 1000, "gp"), (5, 6, 100, "pp")],
    [(12, 6, 1000, "gp"), (8, 6, 1000, "pp")],
]

# (chance %, [(kind, value in gp, dice, sides)]) — one of the options when the chance hits.
HOARD_VALUABLES = [
    (60, [("gem", 10, 2, 4), ("art", 25, 1, 4), ("gem", 50, 1, 6)]),
    (75, [("art", 25, 2, 4), ("gem", 50, 3, 6), ("gem", 100, 2, 6), ("art", 250, 1, 4)]),
    (85, [("art", 250, 2, 4), ("art", 750, 2, 4), ("gem", 500, 3, 6), ("gem", 1000, 3, 6)]),
    (95, [("gem", 1000, 3, 6), ("art", 2500, 1, 10), ("art", 7500, 1, 4), ("gem", 5000, 1, 8)]),
]

# (up to d100, [(dice, sides, rarities, consumables only)])
HOARD_MAGIC = [
    [(40, []), (75, [(1, 4, ("Common", "Uncommon"), True)]), (92, [(1, 2, ("Uncommon",), False)]),
     (100, [(1, 1, ("Rare",), False)])],
    [(30, []), (60, [(1, 4, ("Common", "Uncommon"), True)]), (85, [(1, 3, ("Uncommon",), False)]),
     (97, [(1, 1, ("Rare",), False), (1, 2, ("Uncommon",), False)]), (100, [(1, 1, ("Very Rare",), False)])],
    [(15, []), (45, [(1, 4, ("Uncommon", "Rare"), True)]), (75, [(1, 3, ("Rare",), False)]),
     (93, [(1, 2, ("Very Rare",), False)]), (100, [(1, 1, ("Legendary",), False)])],
    [(5, []), (30, [(1, 4, ("Rare", "Very Rare"), True)]), (65, [(1, 3, ("Very Rare",), False)]),
     (100, [(1, 3, ("Legendary",), False)])],
]

CONSUMABLE_CATEGORIES = {"Potion", "Scroll", "Ammunition"}

GEMS = {
    10: [("Azurite", "Azzurrite"), ("Agate", "Agata"), ("Blue quartz", "Quarzo blu"), ("Tiger eye", "Occhio di tigre"),
         ("Turquoise", "Turchese"), ("Malachite", "Malachite"), ("Hematite", "Ematite"),
         ("Lapis lazuli", "Lapislazzuli")],
    50: [("Carnelian", "Corniola"), ("Chalcedony", "Calcedonio"), ("Jasper", "Diaspro"),
         ("Moonstone", "Pietra di luna"), ("Onyx", "Onice"), ("Citrine", "Quarzo citrino"), ("Zircon", "Zircone")],
    100: [("Amber", "Ambra"), ("Amethyst", "Ametista"), ("Garnet", "Granato"), ("Jade", "Giada"), ("Pearl", "Perla"),
          ("Tourmaline", "Tormalina"), ("Coral", "Corallo")],
    500: [("Topaz", "Topazio"), ("Peridot", "Peridoto"), ("Black pearl", "Perla nera"),
          ("Aquamarine", "Acquamarina"), ("Blue spinel", "Spinello blu")],
    1000: [("Emerald", "Smeraldo"), ("Fire opal", "Opale di fuoco"), ("Blue sapphire", "Zaffiro blu"),
           ("Black opal", "Opale nero"), ("Star sapphire", "Zaffiro stellato")],
    5000: [("Diamond", "Diamante"), ("Ruby", "Rubino"), ("Black sapphire", "Zaffiro nero"), ("Jacinth", "Giacinto")],
}

ART = {
    25: [("Silver ewer", "Brocca d'argento"), ("Carved bone statuette", "Statuetta d'osso intagliato"),
         ("Thin gold bracelet", "Bracciale d'oro sottile"), ("Small silver mirror", "Specchietto d'argento"),
         ("Chased pewter goblet", "Calice di peltro cesellato"),
         ("Enamelled copper locket", "Medaglione di rame smaltato")],
    250: [("Gold signet ring", "Anello d'oro con sigillo"), ("Ivory statuette", "Statuetta d'avorio"),
          ("Silver cup set with garnets", "Coppa d'argento con granati"),
          ("Embroidered silk tapestry", "Arazzo di seta ricamato"),
          ("Parade dagger with a gold pommel", "Pugnale da parata con pomo d'oro"),
          ("Freshwater pearl necklace", "Collana di perle di fiume")],
    750: [("Gold chalice set with moonstones", "Calice d'oro con pietre di luna"),
          ("Silver circlet with amethysts", "Diadema d'argento con ametiste"),
          ("Rosewood harp inlaid with ivory", "Arpa di palissandro intarsiata d'avorio"),
          ("Small gold idol", "Piccolo idolo d'oro"), ("Gold music box", "Carillon d'oro")],
    2500: [("Gold chain with a fire opal", "Catena d'oro con opale di fuoco"),
           ("Old master painting", "Dipinto di un antico maestro"),
           ("Pearl-embroidered silk mantle", "Mantello di seta ricamato di perle"),
           ("White gold coronet with sapphires", "Corona d'oro bianco con zaffiri"),
           ("Silver sceptre with an emerald", "Scettro d'argento con smeraldo")],
    7500: [("Jewelled gold crown", "Corona d'oro tempestata di gemme"),
           ("Platinum ring with a diamond", "Anello di platino con diamante"),
           ("Gold statuette set with rubies", "Statuetta d'oro con rubini"),
           ("Gold cup set with emeralds", "Coppa d'oro con smeraldi"),
           ("Gold and ivory casket", "Scrigno d'oro e avorio")],
}

_GP_WORD = {"it": "mo"}
_SCROLL_LEVEL_RE = re.compile(r"^spell-scroll-(cantrip|\d)")


# ─── Rolling ──────────────────────────────────────────────────────────────────

def parse_cr(text: str) -> float:
    s = str(text).strip()
    try:
        if "/" in s:
            a, b = s.split("/", 1)
            return int(a) / int(b)
        value = float(s)
    except (ValueError, ZeroDivisionError):
        raise LootError(f"not a challenge rating: {text!r}")
    if value < 0:
        raise LootError(f"not a challenge rating: {text!r}")
    return value


def tier_for(cr: float) -> int:
    return 0 if cr < 5 else 1 if cr < 11 else 2 if cr < 17 else 3


def _roll(rng: random.Random, dice: int, sides: int) -> int:
    return sum(rng.randint(1, sides) for _ in range(dice))


def _weighted(rng: random.Random, options: list):
    total = sum(w for w, _ in options)
    pick = rng.randint(1, total)
    for w, value in options:
        pick -= w
        if pick <= 0:
            return value
    return options[-1][1]


def _coins(rng: random.Random, entries: list, into: dict) -> None:
    for dice, sides, mult, coin in entries:
        into[coin] = into.get(coin, 0) + _roll(rng, dice, sides) * mult


def _magic_pool(ruleset: str) -> tuple:
    """(magic items, spells) of the ruleset, with their translations attached."""
    import lookup
    return lookup._get_records("magic_items", ruleset), lookup._get_records("spells", ruleset)


def _name(record: dict, lang: str) -> str:
    return ((record.get("_i18n") or {}).get(lang) or {}).get("name") or record.get("name", "")


def _valuable(rng: random.Random, kind: str, value: int, lang: str) -> dict:
    en, it = rng.choice((GEMS if kind == "gem" else ART)[value])
    name = it if lang == "it" else en
    return {"name": f"{name} ({value} {_GP_WORD.get(lang, 'gp')})", "kind": kind, "qty": 1, "value_gp": value}


def _magic_item(rng: random.Random, rarities: tuple, consumable: bool, pool: tuple, lang: str) -> dict | None:
    items, spells = pool
    fit = [r for r in items if r.get("rarity") in rarities
           and (not consumable or r.get("category") in CONSUMABLE_CATEGORIES)]
    if not fit:
        return None
    rec = rng.choice(fit)
    name = _name(rec, lang)
    m = _SCROLL_LEVEL_RE.match(rec.get("index", ""))
    if m:
        level = 0 if m.group(1) == "cantrip" else int(m.group(1))
        known = [s for s in spells if s.get("level") == level]
        if known:
            name = f"{name}: {_name(rng.choice(known), lang)}"
    return {"name": name, "kind": "magic", "qty": 1, "rarity": rec.get("rarity", ""),
            "index": rec.get("index", "")}


def _stack(items: list) -> list:
    out: list = []
    for item in items:
        same = next((x for x in out if x["name"] == item["name"]), None)
        if same:
            same["qty"] += item["qty"]
        else:
            out.append(dict(item))
    return out


def roll_treasure(cr: float, hoard: bool = False, monsters: int = 1, lang: str = "en",
                  ruleset: str = "2014", rng: random.Random | None = None, pool: tuple | None = None) -> dict:
    """A pile: {"coins": {...}, "items": [...]} for `monsters` creatures or one hoard."""
    rng = rng or random.Random()
    tier = tier_for(cr)
    coins: dict = {}
    items: list = []
    if not hoard:
        for _ in range(max(1, monsters)):
            _coins(rng, _weighted(rng, INDIVIDUAL[tier]), coins)
    else:
        _coins(rng, HOARD_COINS[tier], coins)
        chance, options = HOARD_VALUABLES[tier]
        if rng.randint(1, 100) <= chance:
            kind, value, dice, sides = rng.choice(options)
            items += [_valuable(rng, kind, value, lang) for _ in range(_roll(rng, dice, sides))]
        d100 = rng.randint(1, 100)
        draws = next(d for top, d in HOARD_MAGIC[tier] if d100 <= top)
        if draws:
            pool = pool or _magic_pool(ruleset)
            for dice, sides, rarities, consumable in draws:
                for _ in range(_roll(rng, dice, sides)):
                    item = _magic_item(rng, rarities, consumable, pool, lang)
                    if item:
                        items.append(item)
    return {"coins": {c: n for c, n in coins.items() if n}, "items": _stack(items)}


# ─── The pile on disk ─────────────────────────────────────────────────────────

def _campaign(name: str) -> str:
    camp = name or inventory.active_campaign()
    if not camp:
        raise LootError("no campaign: pass --campaign NAME (none is active)")
    if not paths.find_campaign(camp).is_dir():
        raise LootError(f"no campaign folder for {camp!r}")
    return camp


def _pile_path(campaign: str) -> pathlib.Path:
    return paths.find_campaign(campaign) / LOOT_FILE


def load_pile(campaign: str) -> dict:
    try:
        pile = json.loads(_pile_path(campaign).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return pile if isinstance(pile, dict) else {}


def save_pile(campaign: str, pile: dict) -> None:
    path = _pile_path(campaign)
    if not pile.get("coins") and not pile.get("items"):
        if path.exists():
            path.unlink()
        return
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(pile, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


_ITEM_QTY_RE = re.compile(r"^(.*?)\s*[×x]\s*(\d+)$")


def parse_item(text: str) -> dict:
    name = " ".join((text or "").split())
    qty = 1
    m = _ITEM_QTY_RE.match(name)
    if m and m.group(1):
        name, qty = m.group(1), max(1, int(m.group(2)))
    if not name:
        raise LootError("an item needs a name")
    return {"name": name, "kind": "item", "qty": qty}


def describe(pile: dict, title: str = "Treasure") -> str:
    coins = pile.get("coins") or {}
    lines = [title]
    if coins:
        worth = inventory.coins_cp(coins) / 100
        lines.append(f"  Coins: {inventory.format_coins(coins)}   (≈ {worth:,.0f} gp)")
    for n, item in enumerate(pile.get("items") or [], 1):
        qty = f" ×{item['qty']}" if item.get("qty", 1) > 1 else ""
        rarity = f"   [{item['rarity']}]" if item.get("rarity") else ""
        lines.append(f"  {n}. {item['name']}{qty}{rarity}")
    if len(lines) == 1:
        lines.append("  nothing")
    return "\n".join(lines)


# ─── Handing it out ───────────────────────────────────────────────────────────

def _names(text: str) -> list:
    return [n.strip() for n in (text or "").split(",") if n.strip()]


def take(campaign: str, among: list | None = None, to: str = "", item_args: list | None = None,
         items_to: str = "") -> list:
    """Hand out the pile through inventory.py. Returns the lines of what happened."""
    pile = load_pile(campaign)
    coins = dict(pile.get("coins") or {})
    items = [dict(i) for i in pile.get("items") or []]
    if not coins and not items:
        raise LootError("there is no treasure waiting (roll or set one first)")
    among = among or []
    if to and (among or items_to):
        raise LootError("--to gives everything to one character; do not mix it with --among / --items-to")
    if not (to or among or item_args or items_to):
        raise LootError("say who takes what: --among, --to, --item N:NAME or --items-to")

    # Work out every hand-over first, so a wrong name or number changes nothing.
    gifts: list = []                                  # (index in items, character, qty)
    left = [i.get("qty", 1) for i in items]
    for arg in item_args or []:
        parts = [p.strip() for p in arg.split(":")]
        if len(parts) not in (2, 3) or not parts[0].isdigit() or not parts[1]:
            raise LootError(f"--item wants N:NAME or N:NAME:QTY, not {arg!r}")
        n = int(parts[0])
        if not 1 <= n <= len(items):
            raise LootError(f"there is no item {n} in the pile")
        qty = left[n - 1] if len(parts) == 2 else int(parts[2]) if parts[2].isdigit() else 0
        if qty < 1 or qty > left[n - 1]:
            raise LootError(f"item {n} ({items[n - 1]['name']}): {left[n - 1]} left, cannot give {parts[-1]}")
        left[n - 1] -= qty
        gifts.append((n - 1, parts[1], qty))
    everything_to = to or items_to
    if everything_to:
        gifts += [(i, everything_to, q) for i, q in enumerate(left) if q]
        left = [0] * len(items)
    coin_takers = [to] if to else among
    if len(set(inventory._key(n) for n in coin_takers)) != len(coin_takers):
        raise LootError("the same character is listed twice")
    for name in set(coin_takers) | {g[1] for g in gifts}:
        try:
            inventory.read_purse(inventory._read(inventory.find_sheet(name, campaign)))
        except inventory.InventoryError as e:
            raise LootError(str(e))

    done: list = []
    try:
        if coins and to:
            inventory.op_gain(to, coins, campaign)
            done.append(f"{to}: +{inventory.format_coins(coins)}")
            coins = {}
        elif coins and len(among) == 1:
            inventory.op_gain(among[0], coins, campaign)
            done.append(f"{among[0]}: +{inventory.format_coins(coins)}")
            coins = {}
        elif coins and among:
            share, rest = inventory.op_split(coins, among, campaign)
            done.append(f"{', '.join(among)}: +{inventory.format_cp(share)} each")
            if rest:
                inventory.op_gain(among[0], {"cp": rest}, campaign)
                done.append(f"{among[0]}: +{rest}cp left over from the split")
            coins = {}
        for i, name, qty in gifts:
            inventory.op_add(name, items[i]["name"], qty, "gear", campaign)
            done.append(f"{name}: + {items[i]['name']}" + (f" ×{qty}" if qty > 1 else ""))
            items[i]["qty"] = items[i].get("qty", 1) - qty
    except inventory.InventoryError as e:
        raise LootError(str(e))
    finally:
        pile["coins"] = coins
        pile["items"] = [i for i in items if i.get("qty", 1) > 0]
        save_pile(campaign, pile)
    return done


# ─── Command line ─────────────────────────────────────────────────────────────

def _take_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--among", default="", help='split the coins in equal shares: "Aldric,Mira,Lio"')
    p.add_argument("--to", default="", help="give everything to one character")
    p.add_argument("--item", action="append", default=[], metavar="N:NAME[:QTY]",
                   help="give a numbered item to a character (repeatable)")
    p.add_argument("--items-to", default="", help="give every item left to one character")


def _wants_take(args) -> bool:
    return bool(args.among or args.to or args.item or args.items_to)


def _finish(campaign: str, args, title: str) -> None:
    print(describe(load_pile(campaign), title))
    if _wants_take(args):
        for line in take(campaign, _names(args.among), args.to, args.item, args.items_to):
            print("  → " + line)
    pile = load_pile(campaign)
    if pile.get("coins") or pile.get("items"):
        print(describe(pile, "Not handed out yet") if _wants_take(args) else
              'Not handed out yet — loot.py take --among "A,B,C" --item "1:Name" …')


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="Random treasure, handed out through inventory.py.")
    ap.add_argument("--campaign", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("roll", help="roll treasure for a challenge rating")
    p.add_argument("--cr", default="", help="challenge rating of the creatures: 1/4, 3, 12…")
    p.add_argument("--level", type=int, default=0, help="party level, when there is no CR to go by")
    p.add_argument("--hoard", action="store_true", help="a lair or a vault, not what a creature carries")
    p.add_argument("--monsters", type=int, default=1, help="how many creatures carried coins (not with --hoard)")
    p.add_argument("--lang", default="", help="language of the names (default: the campaign's)")
    p.add_argument("--seed", default=None, help="make the roll repeatable")
    p.add_argument("--add", action="store_true", help="add to the treasure already waiting")
    _take_options(p)
    p = sub.add_parser("set", help="a treasure the adventure prescribes, to hand out the same way")
    p.add_argument("--coins", nargs="+", default=[], help="e.g. 340gp 20sp")
    p.add_argument("--item", dest="set_item", action="append", default=[], help='"Name" or "Name x3" (repeatable)')
    p.add_argument("--add", action="store_true", help="add to the treasure already waiting")
    p.add_argument("--among", default=""); p.add_argument("--to", default="")
    p.add_argument("--items-to", default="")
    p.set_defaults(item=[])
    sub.add_parser("show", help="the treasure waiting to be handed out")
    _take_options(sub.add_parser("take", help="hand out the treasure waiting"))
    sub.add_parser("drop", help="forget the treasure waiting")
    args = ap.parse_args(argv)

    try:
        camp = _campaign(args.campaign)
        if args.cmd in ("roll", "set"):
            old = load_pile(camp)
            if (old.get("coins") or old.get("items")) and not args.add:
                raise LootError("a treasure is still waiting — take it, drop it, or pass --add to pile this on top")
            if args.cmd == "roll":
                if not args.cr and not args.level:
                    raise LootError("roll needs --cr or --level")
                cr = parse_cr(args.cr) if args.cr else float(args.level)
                rng = random.Random(args.seed) if args.seed is not None else random.Random()
                new = roll_treasure(cr, args.hoard, args.monsters, args.lang or paths.campaign_language(camp),
                                    paths.campaign_ruleset(camp), rng)
                title = f"Treasure — {'hoard' if args.hoard else f'{max(1, args.monsters)} creature(s)'}, " \
                        f"{'CR ' + args.cr if args.cr else 'level ' + str(args.level)}"
            else:
                try:
                    coins = inventory.parse_coins(" ".join(args.coins)) if args.coins else {}
                except inventory.InventoryError as e:
                    raise LootError(str(e))
                new = {"coins": coins, "items": [parse_item(t) for t in args.set_item]}
                if not new["coins"] and not new["items"]:
                    raise LootError("set needs --coins and/or --item")
                title = "Treasure"
            pile = {"coins": inventory.add_to(old.get("coins") or {}, new["coins"]),
                    "items": _stack((old.get("items") or []) + new["items"])}
            pile["coins"] = {c: n for c, n in pile["coins"].items() if n}
            save_pile(camp, pile)
            _finish(camp, args, title)
        elif args.cmd == "show":
            print(describe(load_pile(camp), "Treasure waiting"))
        elif args.cmd == "take":
            for line in take(camp, _names(args.among), args.to, args.item, args.items_to):
                print("→ " + line)
            pile = load_pile(camp)
            print(describe(pile, "Still in the pile") if pile else "The pile is empty.")
        elif args.cmd == "drop":
            had = load_pile(camp)
            save_pile(camp, {})
            print("Dropped." if had else "There was nothing waiting.")
    except LootError as e:
        print(f"loot: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
