#!/usr/bin/env python3
"""inventory.py — coins and items on a character sheet, done by arithmetic.

The sheet's `## Equipment & Inventory` section is the record: item lists under
**Weapons:** / **Armour:** / **Adventuring Gear:** and a `**Currency:**` line.
This script edits that section so that buying, selling, looting and splitting
are applied rather than remembered: it makes change, refuses a purchase the
purse cannot cover, and exits non-zero without touching the file when a name
or an item does not match.

    inventory.py show  "Aldric"
    inventory.py gain  "Aldric" 12gp 5sp              # found, earned, rewarded
    inventory.py pay   "Aldric" 3sp                   # a toll, a bribe, a drink
    inventory.py add   "Aldric" "Corda di canapa (15 m)"
    inventory.py add   "Aldric" "Pozione di guarigione" --qty 2
    inventory.py add   "Aldric" "Spada corta" --section weapons
    inventory.py remove "Aldric" "Pozione di guarigione"        # one of them
    inventory.py buy   "Aldric" "Lanterna schermabile" --price 5gp
    inventory.py sell  "Aldric" "Spada corta" --price 5gp
    inventory.py give  "Aldric" "Mira" --item "Chiave di ferro"
    inventory.py give  "Aldric" "Mira" --coins 10gp
    inventory.py split 137gp 4sp --among "Aldric,Mira,Lio"       # loot, equal shares

Coins: pp gp ep sp cp (Italian mp mo me ma mr are read too). `--campaign NAME`
picks a campaign other than the active one. Every change is also pushed to the
display (push_stats.py), which stays silent when the display is not running.

Several of an item are written `Name ×N`. `remove` and `sell` match the exact
name first, then a unique part of it; an ambiguous match changes nothing and
lists the candidates.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import paths  # noqa: E402  (also forces UTF-8 output on Windows)

COIN_CP = {"pp": 1000, "gp": 100, "ep": 50, "sp": 10, "cp": 1}
COIN_ORDER = ("pp", "gp", "ep", "sp", "cp")
_COIN_ALIASES = {"mp": "pp", "mo": "gp", "me": "ep", "ma": "sp", "mr": "cp"}
_COIN_RE = re.compile(r"(\d+)\s*(pp|gp|ep|sp|cp|mp|mo|me|ma|mr)\b", re.I)

SECTIONS = {"weapons": "Weapons", "armour": "Armour", "gear": "Adventuring Gear"}
_SECTION_ALIASES = {"armor": "armour", "weapon": "weapons", "adventuring gear": "gear", "adventuring": "gear"}
_HEADING_RE = re.compile(r"^##\s+Equipment\b.*$", re.M)
_CURRENCY_RE = re.compile(r"^(\*\*Currency:?\*\*:?[ \t]*)([^\r\n]*)", re.M)
_QTY_RE = re.compile(r"^(.*?)\s*[×x]\s*(\d+)$")
_PLACEHOLDERS = {"", "-", "—", "–", "none", "nessuno", "nessuna"}


class InventoryError(Exception):
    """A request that cannot be applied; the sheet is left as it was."""


# ─── Coins ────────────────────────────────────────────────────────────────────

def parse_coins(text: str) -> dict:
    """'12gp 5sp' / '12 mo, 5 ma' → {'gp': 12, 'sp': 5}. Raises on anything else."""
    found = _COIN_RE.findall(text or "")
    leftover = _COIN_RE.sub("", text or "").replace(",", " ").replace("+", " ").strip()
    if not found or leftover:
        raise InventoryError(f"not an amount of coins: {text!r} (write e.g. 12gp 5sp)")
    out: dict = {}
    for n, unit in found:
        unit = _COIN_ALIASES.get(unit.lower(), unit.lower())
        out[unit] = out.get(unit, 0) + int(n)
    return out


def coins_cp(coins: dict) -> int:
    return sum(COIN_CP[u] * int(n) for u, n in coins.items())


def format_coins(coins: dict) -> str:
    """Coins worth mentioning, largest first: '2pp 5gp 3sp'; '0gp' for nothing."""
    parts = [f"{coins[u]}{u}" for u in COIN_ORDER if coins.get(u)]
    return " ".join(parts) or "0gp"


def format_cp(cp: int) -> str:
    """A price in copper as gp/sp/cp: 1250 → '12gp 5sp'."""
    return format_coins({"gp": cp // 100, "sp": cp % 100 // 10, "cp": cp % 10})


def pay_from(purse: dict, price_cp: int) -> dict:
    """The purse after paying, with change made. Raises if it is not enough.

    Platinum and electrum are kept whole while the rest can cover the price;
    gold, silver and copper come back as tidy change.
    """
    total = coins_cp(purse)
    if price_cp > total:
        raise InventoryError(
            f"not enough coins: the price is {format_cp(price_cp)}, the purse holds {format_coins(purse)}")
    rest = total - price_cp
    pp = min(int(purse.get("pp", 0)), rest // COIN_CP["pp"])
    rest -= pp * COIN_CP["pp"]
    ep = min(int(purse.get("ep", 0)), rest // COIN_CP["ep"])
    rest -= ep * COIN_CP["ep"]
    return {"pp": pp, "gp": rest // 100, "ep": ep, "sp": rest % 100 // 10, "cp": rest % 10}


def add_to(purse: dict, coins: dict) -> dict:
    return {u: int(purse.get(u, 0)) + int(coins.get(u, 0)) for u in COIN_ORDER}


# ─── The sheet text ───────────────────────────────────────────────────────────

def _equipment_span(text: str) -> tuple[int, int]:
    m = _HEADING_RE.search(text)
    if not m:
        raise InventoryError("the sheet has no '## Equipment & Inventory' section")
    nxt = re.search(r"^##\s", text[m.end():], re.M)
    return m.end(), (m.end() + nxt.start()) if nxt else len(text)


def read_purse(text: str) -> dict:
    start, end = _equipment_span(text)
    m = _CURRENCY_RE.search(text, start, end)
    purse = {u: 0 for u in COIN_ORDER}
    if m:
        for n, unit in _COIN_RE.findall(m.group(2)):
            unit = _COIN_ALIASES.get(unit.lower(), unit.lower())
            purse[unit] += int(n)
    return purse


def write_purse(text: str, purse: dict) -> str:
    """Rewrite the Currency line: gp sp cp always, pp and ep only when held."""
    coins = " ".join(f"{int(purse.get(u, 0))}{u}" for u in COIN_ORDER
                     if u in ("gp", "sp", "cp") or purse.get(u))
    start, end = _equipment_span(text)
    m = _CURRENCY_RE.search(text, start, end)
    if not m:
        nl = "\r\n" if "\r\n" in text else "\n"
        body = text[start:end].rstrip("\r\n")
        return text[:start] + body + f"{nl}{nl}**Currency:** {coins}{nl}{nl}" + text[end:]
    # Keep any note the player or the DM wrote after the coins.
    tail = _COIN_RE.sub("", m.group(2)).strip(" ,;")
    return text[:m.start(2)] + coins + (f" {tail}" if tail else "") + text[m.end(2):]


def _section_key(name: str) -> str:
    key = (name or "gear").strip().lower()
    key = _SECTION_ALIASES.get(key, key)
    if key not in SECTIONS:
        raise InventoryError(f"unknown section {name!r} (use: {', '.join(SECTIONS)})")
    return key


def _section_label_re(key: str) -> re.Pattern:
    label = "Armou?r" if key == "armour" else re.escape(SECTIONS[key])
    return re.compile(rf"^\*\*{label}:?\*\*:?[ \t]*(?=\r?$)", re.M | re.I)


def _items(text: str) -> list:
    """Every item line of the equipment section: (line start, line end, section key, name, qty)."""
    start, end = _equipment_span(text)
    out, section = [], "gear"
    pos = start
    for raw in text[start:end].splitlines(keepends=True):
        line = raw.rstrip("\r\n")
        for key in SECTIONS:
            if _section_label_re(key).match(line):
                section = key
        m = re.match(r"^[-*]\s+(.*\S)\s*$", line)
        if m and m.group(1).strip().lower() not in _PLACEHOLDERS:
            name, qty = m.group(1).strip(), 1
            q = _QTY_RE.match(name)
            if q:
                name, qty = q.group(1).strip(), int(q.group(2))
            out.append((pos, pos + len(line), section, name, qty))
        pos += len(raw)
    return out


def list_items(text: str) -> dict:
    out: dict = {key: [] for key in SECTIONS}
    for _s, _e, section, name, qty in _items(text):
        out[section].append(name if qty == 1 else f"{name} ×{qty}")
    return out


def _item_line(name: str, qty: int) -> str:
    return f"- {name}" if qty == 1 else f"- {name} ×{qty}"


def add_item(text: str, name: str, qty: int = 1, section: str = "gear") -> str:
    name = " ".join((name or "").split())
    if not name or qty < 1:
        raise InventoryError("an item needs a name and a quantity of at least 1")
    key = _section_key(section)
    for s, e, _sec, existing, have in _items(text):
        if existing.casefold() == name.casefold():          # one more of what is already there
            return text[:s] + _item_line(existing, have + qty) + text[e:]

    nl = "\r\n" if "\r\n" in text else "\n"
    start, end = _equipment_span(text)
    label = _section_label_re(key).search(text, start, end)
    if not label:
        # No such list yet: open it just above the Currency line (or at the section's end).
        cur = _CURRENCY_RE.search(text, start, end)
        at = cur.start() if cur else end
        block = f"**{SECTIONS[key]}:**{nl}{_item_line(name, qty)}{nl}{nl}"
        if at > 0 and not text[:at].endswith(nl + nl):
            block = (nl if text[:at].endswith(nl) else nl + nl) + block
        return text[:at] + block + text[at:]

    # Walk the list under the label; replace a lone placeholder, else append after the last item.
    pos, last_end, placeholder = label.end(), None, None
    for raw in text[label.end():end].splitlines(keepends=True):
        line = raw.rstrip("\r\n")
        if pos > label.end() and not line.strip() and (last_end is not None or placeholder is not None):
            break
        if line.startswith(("**", "##")) and pos > label.end():
            break
        m = re.match(r"^[-*]\s*(.*)$", line)
        if m:
            if m.group(1).strip().lower() in _PLACEHOLDERS:
                placeholder = (pos, pos + len(line))
            else:
                last_end = pos + len(line)
        pos += len(raw)
    if last_end is None and placeholder:
        return text[:placeholder[0]] + _item_line(name, qty) + text[placeholder[1]:]
    at = last_end if last_end is not None else label.end()
    return text[:at] + nl + _item_line(name, qty) + text[at:]


def find_item(text: str, name: str):
    """The one item line `name` means: exact name first, else a unique part of it."""
    want = " ".join((name or "").split()).casefold()
    if not want:
        raise InventoryError("which item?")
    items = _items(text)
    hits = [i for i in items if i[3].casefold() == want] or [i for i in items if want in i[3].casefold()]
    if not hits:
        raise InventoryError(f"no item matching {name!r} on the sheet")
    if len(hits) > 1:
        raise InventoryError(f"{name!r} matches several items: " + "; ".join(h[3] for h in hits))
    return hits[0]


def remove_item(text: str, name: str, qty: int = 1) -> tuple[str, str]:
    """(new text, the item's full name). Removing the last one deletes the line."""
    s, e, _sec, full, have = find_item(text, name)
    if qty < 1 or qty > have:
        raise InventoryError(f"cannot remove {qty} of {full!r}: the sheet has {have}")
    if have > qty:
        return text[:s] + _item_line(full, have - qty) + text[e:], full
    # Drop the whole line, including its line break.
    nl_len = 2 if text[e:e + 2] == "\r\n" else (1 if text[e:e + 1] == "\n" else 0)
    return text[:s] + text[e + nl_len:], full


# ─── Files ────────────────────────────────────────────────────────────────────

def _key(name: str) -> str:
    return "".join(ch for ch in (name or "").casefold() if ch.isalnum())


def active_campaign() -> str:
    try:
        return (paths.runtime_dir() / ".campaign").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def find_sheet(character: str, campaign: str = "") -> pathlib.Path:
    """The character's .md: the campaign's copy first, then the global roster."""
    want = _key(character)
    if not want:
        raise InventoryError("which character?")
    folders = []
    camp = campaign or active_campaign()
    if camp:
        try:
            folders.append(paths.find_campaign(camp) / "characters")
        except Exception:
            pass
    folders.append(paths.characters_dir())
    for folder in folders:
        try:
            files = sorted(folder.glob("*.md"))
        except OSError:
            continue
        for f in files:
            if _key(f.stem) == want:
                return f
        for f in files:                       # the file name may differ from the name on the sheet
            try:
                first = f.open(encoding="utf-8").readline()
            except OSError:
                continue
            if first.startswith("# ") and _key(first[2:]) == want:
                return f
    where = " or ".join(str(f) for f in folders)
    raise InventoryError(f"no character sheet for {character!r} in {where}")


def _read(path: pathlib.Path) -> str:
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def _write(path: pathlib.Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def display_name(path: pathlib.Path) -> str:
    try:
        first = path.open(encoding="utf-8").readline().strip()
    except OSError:
        first = ""
    return first[2:].strip() if first.startswith("# ") else path.stem


def _push(args: list) -> None:
    """Mirror a change on the display's sheet. Silent when the display is off."""
    script = paths.display_dir() / "push_stats.py"
    try:
        subprocess.run([sys.executable, str(script), *args], capture_output=True, timeout=10)
    except Exception:
        pass


# ─── Operations (each one reads, changes and writes whole sheets, or nothing) ─

def op_gain(character: str, coins: dict, campaign: str = "") -> dict:
    path = find_sheet(character, campaign)
    text = _read(path)
    purse = add_to(read_purse(text), coins)
    _write(path, write_purse(text, purse))
    return purse


def op_pay(character: str, price_cp: int, campaign: str = "") -> dict:
    path = find_sheet(character, campaign)
    text = _read(path)
    purse = pay_from(read_purse(text), price_cp)
    _write(path, write_purse(text, purse))
    return purse


def op_add(character: str, item: str, qty: int = 1, section: str = "gear", campaign: str = "") -> None:
    path = find_sheet(character, campaign)
    _write(path, add_item(_read(path), item, qty, section))
    _push(["--player", display_name(path), "--inventory-add", item if qty == 1 else f"{item} ×{qty}"])


def op_remove(character: str, item: str, qty: int = 1, campaign: str = "") -> str:
    path = find_sheet(character, campaign)
    text, full = remove_item(_read(path), item, qty)
    _write(path, text)
    _push(["--player", display_name(path), "--inventory-remove", full])
    return full


def op_buy(character: str, item: str, price_cp: int, qty: int = 1, section: str = "gear",
           campaign: str = "", push: bool = True) -> dict:
    """Pay `price_cp` EACH and add the item, or do neither.

    `push=False` is for the display server, which updates its own copy of the sheet.
    """
    path = find_sheet(character, campaign)
    text = _read(path)
    purse = pay_from(read_purse(text), price_cp * qty)
    text = add_item(write_purse(text, purse), item, qty, section)
    _write(path, text)
    if push:
        _push(["--player", display_name(path), "--inventory-add", item if qty == 1 else f"{item} ×{qty}"])
    return purse


def purse_of(character: str, campaign: str = "") -> dict:
    return read_purse(_read(find_sheet(character, campaign)))


def op_sell(character: str, item: str, price_cp: int, qty: int = 1, campaign: str = "") -> tuple[dict, str]:
    """Remove the item and add `price_cp` EACH, or do neither."""
    path = find_sheet(character, campaign)
    text, full = remove_item(_read(path), item, qty)
    purse = add_to(read_purse(text), {"gp": price_cp * qty // 100, "sp": price_cp * qty % 100 // 10,
                                      "cp": price_cp * qty % 10})
    _write(path, write_purse(text, purse))
    _push(["--player", display_name(path), "--inventory-remove", full])
    return purse, full


def op_give(giver: str, taker: str, item: str = "", qty: int = 1, coins: dict | None = None,
            campaign: str = "") -> str:
    src, dst = find_sheet(giver, campaign), find_sheet(taker, campaign)
    if src == dst:
        raise InventoryError("giver and receiver are the same character")
    a, b = _read(src), _read(dst)
    if coins:
        a = write_purse(a, pay_from(read_purse(a), coins_cp(coins)))
        b = write_purse(b, add_to(read_purse(b), coins))
        what = format_coins(coins)
    else:
        a, full = remove_item(a, item, qty)
        b = add_item(b, full, qty)
        what = full if qty == 1 else f"{full} ×{qty}"
    _write(src, a)
    _write(dst, b)
    if not coins:
        _push(["--player", display_name(src), "--inventory-remove", full])
        _push(["--player", display_name(dst), "--inventory-add", what])
    return what


def op_split(coins: dict, among: list, campaign: str = "") -> tuple[int, int]:
    """Equal shares in copper to each name; (share, remainder) in cp. All sheets or none."""
    names = [n.strip() for n in among if n.strip()]
    if len(names) < 2:
        raise InventoryError("split needs at least two characters (--among \"A,B\")")
    sheets = [find_sheet(n, campaign) for n in names]
    if len({str(p) for p in sheets}) != len(sheets):
        raise InventoryError("the same character is listed twice")
    share, rest = divmod(coins_cp(coins), len(names))
    add = {"gp": share // 100, "sp": share % 100 // 10, "cp": share % 10}
    updated = [(p, write_purse(t, add_to(read_purse(t), add))) for p, t in ((p, _read(p)) for p in sheets)]
    for p, text in updated:
        _write(p, text)
    return share, rest


# ─── Command line ─────────────────────────────────────────────────────────────

def _coins_arg(tokens: list) -> dict:
    return parse_coins(" ".join(tokens))


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="Coins and items on a character sheet, done by arithmetic.")
    ap.add_argument("--campaign", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("show", help="coins and items of a character")
    p.add_argument("character")
    for cmd, text in (("gain", "add coins to the purse"), ("pay", "take coins from the purse, making change")):
        p = sub.add_parser(cmd, help=text)
        p.add_argument("character")
        p.add_argument("coins", nargs="+", help="e.g. 12gp 5sp")
    p = sub.add_parser("add", help="add an item")
    p.add_argument("character"); p.add_argument("item")
    p.add_argument("--qty", type=int, default=1)
    p.add_argument("--section", default="gear", help="weapons, armour or gear (default)")
    p = sub.add_parser("remove", help="remove an item (used, lost, broken)")
    p.add_argument("character"); p.add_argument("item")
    p.add_argument("--qty", type=int, default=1)
    p = sub.add_parser("buy", help="pay and add the item, or neither")
    p.add_argument("character"); p.add_argument("item")
    p.add_argument("--price", required=True, nargs="+", help="price of ONE, e.g. 5gp")
    p.add_argument("--qty", type=int, default=1)
    p.add_argument("--section", default="gear")
    p = sub.add_parser("sell", help="remove the item and add the coins, or neither")
    p.add_argument("character"); p.add_argument("item")
    p.add_argument("--price", required=True, nargs="+", help="price of ONE, e.g. 5gp")
    p.add_argument("--qty", type=int, default=1)
    p = sub.add_parser("give", help="hand an item or coins to another character")
    p.add_argument("giver"); p.add_argument("taker")
    p.add_argument("--item", default=""); p.add_argument("--qty", type=int, default=1)
    p.add_argument("--coins", nargs="+")
    p = sub.add_parser("split", help="share coins equally among characters")
    p.add_argument("coins", nargs="+", help="e.g. 137gp 4sp")
    p.add_argument("--among", required=True, help='comma-separated names: "Aldric,Mira,Lio"')
    args = ap.parse_args(argv)
    camp = args.campaign

    try:
        if args.cmd == "show":
            path = find_sheet(args.character, camp)
            text = _read(path)
            print(f"{display_name(path)} — {format_coins(read_purse(text))}")
            for key, items in list_items(text).items():
                if items:
                    print(f"  {SECTIONS[key]}: " + "; ".join(items))
        elif args.cmd == "gain":
            coins = _coins_arg(args.coins)
            print(f"{args.character}: +{format_coins(coins)} → {format_coins(op_gain(args.character, coins, camp))}")
        elif args.cmd == "pay":
            coins = _coins_arg(args.coins)
            print(f"{args.character}: −{format_coins(coins)} → "
                  f"{format_coins(op_pay(args.character, coins_cp(coins), camp))}")
        elif args.cmd == "add":
            op_add(args.character, args.item, args.qty, args.section, camp)
            print(f"{args.character}: + {args.item}" + (f" ×{args.qty}" if args.qty > 1 else ""))
        elif args.cmd == "remove":
            full = op_remove(args.character, args.item, args.qty, camp)
            print(f"{args.character}: − {full}" + (f" ×{args.qty}" if args.qty > 1 else ""))
        elif args.cmd == "buy":
            price = coins_cp(_coins_arg(args.price))
            purse = op_buy(args.character, args.item, price, args.qty, args.section, camp)
            print(f"{args.character}: bought {args.item}" + (f" ×{args.qty}" if args.qty > 1 else "")
                  + f" for {format_cp(price * args.qty)} → {format_coins(purse)} left")
        elif args.cmd == "sell":
            price = coins_cp(_coins_arg(args.price))
            purse, full = op_sell(args.character, args.item, price, args.qty, camp)
            print(f"{args.character}: sold {full}" + (f" ×{args.qty}" if args.qty > 1 else "")
                  + f" for {format_cp(price * args.qty)} → {format_coins(purse)}")
        elif args.cmd == "give":
            if bool(args.item) == bool(args.coins):
                raise InventoryError("give exactly one of --item or --coins")
            what = op_give(args.giver, args.taker, args.item, args.qty,
                           _coins_arg(args.coins) if args.coins else None, camp)
            print(f"{args.giver} → {args.taker}: {what}")
        elif args.cmd == "split":
            share, rest = op_split(_coins_arg(args.coins), args.among.split(","), camp)
            print(f"each gets {format_cp(share)}"
                  + (f"; {format_cp(rest)} left over — hand it to someone with gain" if rest else ""))
    except InventoryError as e:
        print(f"inventory: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
