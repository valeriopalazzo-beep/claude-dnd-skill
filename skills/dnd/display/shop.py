#!/usr/bin/env python3
"""shop.py — open a shop on the players' screens.

The price list appears on every screen. A player taps an item on their own
phone to buy it: the display checks the purse on the character sheet, takes
the coins, makes change, adds the item and lowers the stock — the same
arithmetic as scripts/inventory.py, with nothing left to remember. What was
sold reaches the DM with the next check_input.py / autorun_wait.py as

    [[SHOP: Aldric bought Corda di canapa (15 m) for 1gp at "Emporio di Brakka" — …]]

    # One item per line:  name | price | stock | note      (stock and note optional)
    python3 shop.py open "Emporio di Brakka" --keeper "Brakka" << 'DNDEND'
    Corda di canapa (15 m) | 1gp
    Pozione di guarigione | 50gp | 3 | sigillo di cera rossa
    Razioni (1 giorno) | 5sp
    [weapons] Spada corta | 10gp | 1
    [armour] Scudo | 10gp
    DNDEND

    python3 shop.py show      # what is on sale now, with the stock left
    python3 shop.py sales     # print what was sold since last time, then clear it
    python3 shop.py close     # the party leaves

Prices: pp gp ep sp cp (Italian mp mo me ma mr are read too). No stock = the
shop never runs out. `[weapons]` / `[armour]` in front of a name puts the item
in that list of the buyer's sheet; everything else goes to Adventuring Gear.

To change a price (haggling, a favour, a markup for strangers) open the shop
again with the new list. Selling TO the shop, and anything the list does not
cover, is a normal action: resolve it with scripts/inventory.py sell / buy.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from runtime_paths import rt          # writable runtime dir (update-safe)

SALES_FILE = rt("shop_sales.json")      # sales the DM has not been told about yet
TOKEN_FILE = rt(".token")
TIMEOUT = 4.0
_SECTION_TAG = re.compile(r"^\[(weapons?|armou?r|gear)\]\s*", re.I)


# ─── Sales log: written by the display server, drained by the DM's scripts ────

def sales_add(sale: dict) -> None:
    try:
        with open(SALES_FILE, encoding="utf-8") as f:
            sales = json.load(f)
        if not isinstance(sales, list):
            sales = []
    except (OSError, ValueError):
        sales = []
    sales.append(sale)
    tmp = SALES_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sales, f, ensure_ascii=False)
        os.replace(tmp, SALES_FILE)
    except OSError:
        pass


def format_sale(sale: dict) -> str:
    qty = int(sale.get("qty", 1) or 1)
    item = str(sale.get("item", "?")) + (f" ×{qty}" if qty > 1 else "")
    return (f'[[SHOP: {sale.get("character", "?")} bought {item} for {sale.get("total", "?")} '
            f'at "{sale.get("shop", "?")}" — already paid and added to the sheet '
            f'({sale.get("left", "?")} left in the purse). Not a turn action: mention it only if it matters.]]')


def drain_sales() -> list[str]:
    """Unreported sales as lines for the DM; clears the log."""
    try:
        with open(SALES_FILE, encoding="utf-8") as f:
            sales = json.load(f)
        os.remove(SALES_FILE)
    except (OSError, ValueError):
        return []
    return [format_sale(s) for s in sales if isinstance(s, dict)]


# ─── The price list ───────────────────────────────────────────────────────────

def parse_items(text: str) -> list[dict]:
    """`name | price | stock | note` lines → item dicts. Raises ValueError with the bad line."""
    items = []
    for n, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        section = "gear"
        tag = _SECTION_TAG.match(line)
        if tag:
            word = tag.group(1).lower()
            section = "weapons" if word.startswith("weapon") else "armour" if word.startswith("arm") else "gear"
            line = line[tag.end():]
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            raise ValueError(f"line {n}: write `name | price` (got: {raw.strip()!r})")
        item = {"name": parts[0], "price": parts[1], "section": section}
        if len(parts) > 2 and parts[2]:
            if not parts[2].isdigit():
                raise ValueError(f"line {n}: the stock must be a whole number (got: {parts[2]!r})")
            item["stock"] = int(parts[2])
        if len(parts) > 3 and parts[3]:
            item["note"] = " | ".join(parts[3:])
        items.append(item)
    if not items:
        raise ValueError("the shop has nothing to sell: give at least one `name | price` line")
    return items


# ─── Talking to the display server ────────────────────────────────────────────

def _request(path: str, body: dict | None = None):
    """(status, parsed JSON or None). status 0 = the display is not running."""
    scheme_file = os.path.join(_HERE, ".scheme")
    scheme = "http"
    if os.path.exists(scheme_file):
        scheme = open(scheme_file, encoding="utf-8").read().strip() or "http"
    url = f"{scheme}://localhost:5001{path}"
    headers = {"Content-Type": "application/json"}
    try:
        token = open(TOKEN_FILE, encoding="utf-8").read().strip()
    except OSError:
        token = ""
    if token:
        headers["X-DND-Token"] = token
    ctx = None
    if scheme == "https":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except Exception:
        return 0, None
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, None


def _fail(status: int, body, what: str) -> int:
    if status == 0:
        print(f"shop: the display is not running — {what}.", file=sys.stderr)
    else:
        why = body.get("error") if isinstance(body, dict) else ""
        print(f"shop: {what} (HTTP {status}{': ' + why if why else ''}).", file=sys.stderr)
    return 1


def _print_shop(shop: dict | None) -> None:
    if not shop:
        print("No shop is open.")
        return
    print(f'{shop.get("name", "")}' + (f' — {shop["keeper"]}' if shop.get("keeper") else ""))
    for it in shop.get("items", []):
        stock = "" if it.get("stock") is None else ("  (sold out)" if it["stock"] == 0 else f'  ({it["stock"]} left)')
        print(f'  {it.get("name", "")} — {it.get("price", "")}{stock}')


def main() -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    ap = argparse.ArgumentParser(description="Open a shop on the players' screens.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("open", help="put a shop on the screens, replacing the one already there; "
                                    "the price list comes from stdin or --items")
    p.add_argument("name")
    p.add_argument("--keeper", default="", help="who runs it")
    p.add_argument("--note", default="", help="a line under the name (mood, a house rule)")
    p.add_argument("--items", help="the price list; without it the list is read from stdin")
    sub.add_parser("close", help="close the shop")
    sub.add_parser("show", help="print what is on sale now")
    sub.add_parser("sales", help="print what was sold since last time, then clear it")
    args = ap.parse_args()

    if args.cmd == "sales":
        for line in drain_sales():
            print(line)
        return 0
    if args.cmd == "show":
        status, body = _request("/shop")
        if status != 200 or not isinstance(body, dict):
            return _fail(status, body, "could not read the shop")
        _print_shop(body.get("shop"))
        return 0
    if args.cmd == "close":
        status, body = _request("/shop", {"close": True})
        if status != 200:
            return _fail(status, body, "the shop was not closed")
        print("shop closed")
        return 0

    text = args.items if args.items is not None else ("" if sys.stdin.isatty() else sys.stdin.read())
    try:
        items = parse_items(text)
    except ValueError as e:
        print(f"shop: {e}. Nothing was opened.", file=sys.stderr)
        return 1
    status, body = _request("/shop", {"name": args.name, "keeper": args.keeper, "note": args.note, "items": items})
    if status != 200 or not isinstance(body, dict):
        return _fail(status, body, "the shop was not opened")
    _print_shop(body.get("shop"))
    print("→ open on every screen; players buy from their own phone")
    return 0


if __name__ == "__main__":
    sys.exit(main())
