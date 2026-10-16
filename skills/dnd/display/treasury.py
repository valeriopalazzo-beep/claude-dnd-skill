#!/usr/bin/env python3
"""treasury.py — the party's common fund: coins that belong to the group.

A purse held by nobody in particular: the reward the party keeps together,
the money for the inn, the boat, the bribe, the stronghold's upkeep. Each
player puts coins in or takes them out from their own phone (the coins move
between their sheet and the fund, with change made), the shop can be paid
from it, and every movement is written down with who and why. The DM is told
of what the players do as `[[TREASURY: …]]` with the next check_input.py.

    treasury.py show                                   # what is in it, and the last movements
    treasury.py add 120gp --reason "Ricompensa del borgomastro"   # coins that arrive to the group
    treasury.py spend 2gp 5sp --reason "Due notti alla Rana Ubriaca"
    treasury.py deposit "Lyra" 10gp                    # from Lyra's sheet into the fund
    treasury.py withdraw "Hernio" 5gp                  # from the fund onto Hernio's sheet
    treasury.py history [--all]
    treasury.py close                                  # no fund any more (the coins left are printed)

The fund lives in <campaign>/treasury.json and exists from the first `add` or
`deposit`; until then the display shows no Treasury panel. Coins: pp gp ep sp
cp (Italian mp mo me ma mr too). `--campaign NAME` works in another campaign.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import inventory  # noqa: E402  (coins: parse, change, the sheets)

HISTORY_MAX = 300
REASON = 160
KINDS = ("add", "spend", "deposit", "withdraw", "shop", "upkeep", "income")


class TreasuryError(Exception):
    """A request that cannot be applied; nothing was changed."""


# ─── Data ─────────────────────────────────────────────────────────────────────

def _active_campaign() -> str:
    import media
    return media.active_campaign() or ""


def _campaign(campaign: str = "") -> str:
    import media
    camp = media.safe_campaign(campaign) if campaign else media.active_campaign()
    if not camp:
        raise TreasuryError("no active campaign — pass --campaign or load one first")
    return camp


def data_path(campaign: str = "") -> str:
    import media
    return str(media.find_campaign(_campaign(campaign)) / "treasury.json")


def load(path: str) -> dict | None:
    """The fund, or None when the party has none."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        return None
    except ValueError as e:
        raise TreasuryError(f"treasury.json is not valid JSON ({e})")
    if not isinstance(data, dict):
        return None
    coins = data.get("coins") if isinstance(data.get("coins"), dict) else {}
    data["coins"] = {u: max(0, int(coins.get(u, 0) or 0)) for u in inventory.COIN_ORDER}
    if not isinstance(data.get("history"), list):
        data["history"] = []
    return data


def new_fund() -> dict:
    return {"coins": {u: 0 for u in inventory.COIN_ORDER}, "history": []}


def save(path: str, data: dict) -> None:
    data["history"] = data["history"][-HISTORY_MAX:]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _clean(text, limit: int) -> str:
    return " ".join(str(text or "").replace("`", "").split())[:limit]


def total_cp(data: dict | None) -> int:
    return inventory.coins_cp(data["coins"]) if data else 0


# ─── Movements (each changes the fund in memory; the caller saves) ──────────────

def _log(data: dict, kind: str, coins: dict, who: str = "", reason: str = "") -> dict:
    entry = {"t": int(time.time()), "kind": kind, "coins": inventory.format_coins(coins),
             "cp": inventory.coins_cp(coins), "who": _clean(who, 50), "reason": _clean(reason, REASON)}
    data["history"].append(entry)
    return entry


def put(data: dict, coins: dict, kind: str = "add", who: str = "", reason: str = "") -> dict:
    if inventory.coins_cp(coins) <= 0:
        raise TreasuryError("no coins named")
    data["coins"] = inventory.add_to(data["coins"], coins)
    return _log(data, kind, coins, who, reason)


def take(data: dict, coins: dict, kind: str = "spend", who: str = "", reason: str = "") -> dict:
    """Coins out of the fund, change made (as on a sheet). Raises if the fund cannot cover them."""
    cp = inventory.coins_cp(coins)
    if cp <= 0:
        raise TreasuryError("no coins named")
    try:
        data["coins"] = inventory.pay_from(data["coins"], cp)
    except inventory.InventoryError:
        raise TreasuryError(f"the fund holds {inventory.format_coins(data['coins'])}, "
                            f"not enough for {inventory.format_cp(cp)}")
    return _log(data, kind, coins, who, reason)


def deposit(data: dict, character: str, coins: dict, campaign: str = "") -> dict:
    """From the character's sheet into the fund. The sheet is written first; the caller saves the fund."""
    name = inventory.display_name(inventory.find_sheet(character, campaign))
    try:
        left = inventory.op_pay(character, inventory.coins_cp(coins), campaign)
    except inventory.InventoryError as e:
        raise TreasuryError(str(e))
    entry = put(data, coins, "deposit", name)
    return {"entry": entry, "purse": left, "character": name}


def withdraw(data: dict, character: str, coins: dict, campaign: str = "") -> dict:
    """From the fund onto the character's sheet. Nothing moves if the fund cannot cover it."""
    name = inventory.display_name(inventory.find_sheet(character, campaign))
    before = dict(data["coins"])
    entry = take(data, coins, "withdraw", name)
    try:
        purse = inventory.op_gain(character, coins, campaign)
    except inventory.InventoryError as e:
        data["coins"] = before
        data["history"].pop()
        raise TreasuryError(str(e))
    return {"entry": entry, "purse": purse, "character": name}


def shop_buy(data: dict, character: str, item: str, price_cp: int, qty: int = 1,
             section: str = "gear", shop: str = "", campaign: str = "") -> dict:
    """The fund pays, the item goes on the character's sheet — or neither."""
    path = inventory.find_sheet(character, campaign)
    name = inventory.display_name(path)
    total = price_cp * qty
    coins = {"gp": total // 100, "sp": total % 100 // 10, "cp": total % 10}
    before = dict(data["coins"])
    entry = take(data, coins, "shop", name, f"{item}" + (f" ×{qty}" if qty > 1 else "") + (f" — {shop}" if shop else ""))
    try:
        inventory._write(path, inventory.add_item(inventory._read(path), item, qty, section))
    except Exception as e:
        data["coins"] = before
        data["history"].pop()
        raise TreasuryError(str(e))
    return entry


# ─── What the screens see, and what the DM is told ───────────────────────────────

def public_view(data: dict | None, last: int = 30) -> dict | None:
    if data is None:
        return None
    return {"coins": dict(data["coins"]), "cp": total_cp(data), "text": inventory.format_coins(data["coins"]),
            "history": list(reversed(data["history"][-last:]))}


def describe(entry: dict) -> str:
    who, coins, why = entry.get("who") or "", entry.get("coins") or "", entry.get("reason") or ""
    kind = entry.get("kind")
    text = {
        "deposit": f"{who} put {coins} in",
        "withdraw": f"{who} took {coins} out",
        "shop": f"{who} paid {coins} from the fund",
        "add": f"+{coins}",
        "income": f"+{coins} earned",
        "spend": f"−{coins}",
        "upkeep": f"−{coins} upkeep",
    }.get(kind, f"{kind} {coins}")
    return text + (f" ({why})" if why else "")


def format_note(entry: dict, fund_text: str) -> str:
    return f"[[TREASURY: {describe(entry)} — the party fund now holds {fund_text}.]]"


def _notes_file() -> str:
    from runtime_paths import rt
    return rt("treasury_notes.json")


def notes_add(line: str) -> None:
    path = _notes_file()
    try:
        with open(path, encoding="utf-8") as f:
            notes = json.load(f)
        if not isinstance(notes, list):
            notes = []
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


def _announce(news: str = "") -> None:
    """Tell the display the fund changed. Silent when it is not running."""
    try:
        import send
        body = json.dumps({"news": news}, ensure_ascii=False).encode("utf-8")
        send._post(f"{send.BASE_URL}/treasury", body, send._read_token())
    except Exception:
        pass


# ─── Command line ─────────────────────────────────────────────────────────────

def _coins(tokens: list) -> dict:
    try:
        return inventory.parse_coins(" ".join(tokens))
    except inventory.InventoryError as e:
        raise TreasuryError(str(e))


def _print_fund(data: dict | None, rows: int = 8) -> None:
    if data is None:
        print("The party has no common fund (it starts with `add` or `deposit`).")
        return
    print(f"Party fund: {inventory.format_coins(data['coins'])}")
    for e in list(reversed(data["history"]))[:rows]:
        print(f"  {time.strftime('%d/%m %H:%M', time.localtime(e.get('t', 0)))}  {describe(e)}")


def main(argv: list | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    ap = argparse.ArgumentParser(description="The party's common fund.")
    ap.add_argument("--campaign", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show", help="what is in the fund")
    for cmd, text in (("add", "coins that arrive to the group"), ("spend", "coins the group spends")):
        p = sub.add_parser(cmd, help=text)
        p.add_argument("coins", nargs="+")
        p.add_argument("--reason", default="")
    for cmd, text in (("deposit", "from a sheet into the fund"), ("withdraw", "from the fund onto a sheet")):
        p = sub.add_parser(cmd, help=text)
        p.add_argument("character")
        p.add_argument("coins", nargs="+")
    p = sub.add_parser("history", help="the movements")
    p.add_argument("--all", action="store_true")
    sub.add_parser("close", help="the party has no fund any more")
    args = ap.parse_args(argv)

    try:
        camp = _campaign(args.campaign)
        path = data_path(camp)
        data = load(path)
        if args.cmd == "show":
            _print_fund(data)
            return 0
        if args.cmd == "history":
            _print_fund(data, rows=10 ** 6 if args.all else 30)
            return 0
        if args.cmd == "close":
            if data is None:
                raise TreasuryError("the party has no fund")
            os.remove(path)
            print(f"Fund closed. It held {inventory.format_coins(data['coins'])}: hand the coins out with inventory.py gain.")
            if camp == _active_campaign():
                _announce()
            return 0
        if data is None:
            if args.cmd in ("spend", "withdraw"):
                raise TreasuryError("the party has no fund yet")
            data = new_fund()
        coins = _coins(args.coins)
        if args.cmd == "add":
            entry = put(data, coins, "add", reason=args.reason)
        elif args.cmd == "spend":
            entry = take(data, coins, "spend", reason=args.reason)
        elif args.cmd == "deposit":
            entry = deposit(data, args.character, coins, camp)["entry"]
        else:
            entry = withdraw(data, args.character, coins, camp)["entry"]
        save(path, data)
        print(f"{describe(entry)} → fund: {inventory.format_coins(data['coins'])}")
        if args.cmd in ("deposit", "withdraw"):
            try:
                import encumbrance
                encumbrance.refresh(inventory.find_sheet(args.character, camp), camp)
            except Exception:
                pass
        if camp == _active_campaign():
            _announce(describe(entry))
    except (TreasuryError, inventory.InventoryError) as e:
        print(f"treasury: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
