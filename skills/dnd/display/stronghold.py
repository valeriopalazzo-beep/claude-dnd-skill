#!/usr/bin/env python3
"""stronghold.py — the party's own place: a tower, an inn, a temple, a keep.

Something the party owns or builds (DMG 2014, pp. 127-129): what it costs to
build and how long, what it costs each day to keep (staff included), who works
there, the improvements under way, and what running it as a business brings.
Upkeep is counted as the calendar moves and paid every 30 days from the
party's common fund (treasury.py) when there is one; what cannot be paid
becomes a debt, and each unpaid debt weighs on the next business roll.

    stronghold.py kinds                                      # the table: cost, days, upkeep, staff
    stronghold.py found "Torre di Vallombra" --kind tower --place Vallombra --build --pay fund
    stronghold.py found "La Rana Ubriaca" --kind inn_town --place Brezza    # owned already (a gift, a deed)
    stronghold.py show
    stronghold.py present on|off      # someone of the party oversees the works (else one day of work in three)
    stronghold.py pass 7              # days that pass outside calendar.py (which counts them by itself)
    stronghold.py upkeep pay          # pay what is owed now (from the fund, or --from "Lyra")
    stronghold.py hire "Berta" --role amministratrice [--wage 2gp]   # a named member of the staff
    stronghold.py fire "Berta"
    stronghold.py improve "Biblioteca" --cost 500gp --days 20 [--upkeep 1gp] [--pay fund]
    stronghold.py business 10 [--by Lyra]     # running it for 10 days of downtime: d100 + days (DMG p. 129)
    stronghold.py set --upkeep 30gp --skilled 10 --untrained 2 --place "…" --note "…"
    stronghold.py close                       # the party no longer has it

Kinds: abbey, farm, guildhall, hunting_lodge, inn_rural, inn_town, keep,
noble_estate, outpost, palace, shop, temple_large, temple_small, tower,
trading_post, or custom (give --upkeep, and --cost / --days to build it).
The data lives in <campaign>/stronghold.json; every screen shows it.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import inventory  # noqa: E402
import treasury  # noqa: E402

# DMG 2014 p. 127 (maintenance per day, staff) and p. 128 (building: gp, days).
# upkeep in copper per day; build None = not in the building table (bought, inherited, given).
KINDS = {
    "abbey":         {"upkeep": 2000,  "skilled": 5,   "untrained": 25,  "build": (50000, 400)},
    "farm":          {"upkeep": 50,    "skilled": 1,   "untrained": 2,   "build": None},
    "guildhall":     {"upkeep": 500,   "skilled": 5,   "untrained": 3,   "build": (5000, 60)},
    "hunting_lodge": {"upkeep": 50,    "skilled": 1,   "untrained": 0,   "build": None},
    "inn_rural":     {"upkeep": 1000,  "skilled": 5,   "untrained": 10,  "build": None},
    "inn_town":      {"upkeep": 500,   "skilled": 1,   "untrained": 5,   "build": None},
    "keep":          {"upkeep": 10000, "skilled": 50,  "untrained": 50,  "build": (50000, 400)},
    "noble_estate":  {"upkeep": 1000,  "skilled": 3,   "untrained": 15,  "build": (25000, 150)},
    "outpost":       {"upkeep": 5000,  "skilled": 20,  "untrained": 40,  "build": (15000, 100)},
    "palace":        {"upkeep": 40000, "skilled": 200, "untrained": 100, "build": (500000, 1200)},
    "shop":          {"upkeep": 200,   "skilled": 1,   "untrained": 0,   "build": None},
    "temple_large":  {"upkeep": 2500,  "skilled": 10,  "untrained": 10,  "build": (50000, 400)},
    "temple_small":  {"upkeep": 100,   "skilled": 2,   "untrained": 0,   "build": None},
    "tower":         {"upkeep": 2500,  "skilled": 10,  "untrained": 0,   "build": (15000, 100)},
    "trading_post":  {"upkeep": 1000,  "skilled": 4,   "untrained": 2,   "build": (5000, 60)},
    "custom":        {"upkeep": 0,     "skilled": 0,   "untrained": 0,   "build": None},
}
ALIASES = {"abbazia": "abbey", "fattoria": "farm", "gilda": "guildhall", "sede di gilda": "guildhall",
           "baita": "hunting_lodge", "baita da caccia": "hunting_lodge", "locanda": "inn_town",
           "locanda rurale": "inn_rural", "fortezza": "keep", "castello": "keep", "villa": "noble_estate",
           "tenuta": "noble_estate", "avamposto": "outpost", "forte": "outpost", "fort": "outpost",
           "palazzo": "palace", "negozio": "shop", "bottega": "shop", "tempio": "temple_large",
           "tempio grande": "temple_large", "tempio piccolo": "temple_small", "temple": "temple_large",
           "torre": "tower", "avamposto commerciale": "trading_post", "inn": "inn_town", "castle": "keep"}
BILL_DAYS = 30              # an administrator pays the upkeep every 30 days
AWAY_RATE = 1 / 3           # DMG p. 128: each day the owner is away adds 3 days to the works
LOG_MAX = 120
# DMG p. 129, running a business: d100 + days (max 30), −10 per unpaid debt.
BUSINESS = ((20, "pay1.5", None), (30, "pay1", None), (40, "pay0.5", None), (60, "even", None),
            (80, "earn", (1, 6)), (90, "earn", (2, 8)), (10 ** 6, "earn", (3, 10)))


class StrongholdError(Exception):
    """A request that cannot be applied; nothing was changed."""


# ─── Data ─────────────────────────────────────────────────────────────────────

def _active_campaign() -> str:
    import media
    return media.active_campaign() or ""


def _campaign(campaign: str = "") -> str:
    import media
    camp = media.safe_campaign(campaign) if campaign else media.active_campaign()
    if not camp:
        raise StrongholdError("no active campaign — pass --campaign or load one first")
    return camp


def data_path(campaign: str = "") -> str:
    import media
    return str(media.find_campaign(_campaign(campaign)) / "stronghold.json")


def fund_path(campaign: str = "") -> str:
    import media
    return str(media.find_campaign(_campaign(campaign)) / "treasury.json")


def load(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        return None
    except ValueError as e:
        raise StrongholdError(f"stronghold.json is not valid JSON ({e})")
    if not isinstance(data, dict) or not data.get("name"):
        return None
    for key, default in (("staff", []), ("improvements", []), ("log", []), ("owed_days", 0), ("debt_cp", 0),
                         ("unpaid", 0), ("present", False), ("build", None), ("place", ""), ("note", "")):
        data.setdefault(key, default)
    return data


def save(path: str, data: dict) -> None:
    data["log"] = data["log"][-LOG_MAX:]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _clean(text, limit: int) -> str:
    return " ".join(str(text or "").replace("`", "").split())[:limit]


def _key(name: str) -> str:
    return "".join(ch for ch in (name or "").casefold() if ch.isalnum())


def kind_of(word: str) -> str:
    w = " ".join((word or "").strip().lower().replace("-", " ").split())
    w = ALIASES.get(w, w).replace(" ", "_")
    if w not in KINDS:
        raise StrongholdError(f"{word!r} is not a kind of property ({', '.join(KINDS)})")
    return w


def note(data: dict, text: str, key: str = "", **values) -> str:
    """A line of the stronghold's story. `key` and `values` let the display say it in the table's language."""
    entry = {"t": int(time.time()), "text": _clean(text, 240)}
    if key:
        entry["key"], entry["vars"] = key, {k: str(v) for k, v in values.items()}
    data["log"].append(entry)
    return text


def daily_cp(data: dict) -> int:
    """What a day costs: the property's upkeep, the named staff's wages, finished improvements."""
    return (int(data.get("upkeep_cp", 0)) + sum(int(s.get("wage_cp", 0)) for s in data["staff"])
            + sum(int(i.get("upkeep_cp", 0)) for i in data["improvements"] if i.get("done")))


def ready(data: dict) -> bool:
    return not (data.get("build") and not data["build"].get("done"))


# ─── Founding ─────────────────────────────────────────────────────────────────

def found(name: str, kind: str, place: str = "", build: bool = False, upkeep_cp: int | None = None,
          cost_gp: int | None = None, days: int | None = None, note_text: str = "") -> dict:
    kind = kind_of(kind)
    row = KINDS[kind]
    data = {"name": _clean(name, 80), "kind": kind, "place": _clean(place, 80), "note": _clean(note_text, 240),
            "upkeep_cp": int(row["upkeep"] if upkeep_cp is None else upkeep_cp),
            "skilled": row["skilled"], "untrained": row["untrained"], "staff": [], "improvements": [],
            "owed_days": 0, "debt_cp": 0, "unpaid": 0, "present": False, "build": None, "log": [],
            "founded": int(time.time())}
    if not data["name"]:
        raise StrongholdError("the place needs a name")
    if build:
        gp, need = row["build"] if row["build"] else (None, None)
        gp, need = (cost_gp if cost_gp is not None else gp), (days if days is not None else need)
        if not gp or not need:
            raise StrongholdError(f"{kind} is not in the building table: give --cost and --days")
        data["build"] = {"cost_gp": int(gp), "days_needed": int(need), "days_done": 0.0, "done": False}
        note(data, f"Works begin: {gp} gp, {need} days with someone of the party there", "begin", gp=gp, days=need)
    else:
        note(data, "The party takes possession", "possess")
    return data


# ─── Time passing ─────────────────────────────────────────────────────────────

def _bill(data: dict, cp: int, fund: dict | None, why: str) -> tuple[str, bool]:
    """Pay `cp` from the fund if it can, else it becomes a debt. (line, paid)."""
    if cp <= 0:
        return "", True
    coins = {"gp": cp // 100, "sp": cp % 100 // 10, "cp": cp % 10}
    if fund is not None and treasury.total_cp(fund) >= cp:
        treasury.take(fund, coins, "upkeep", reason=data["name"])
        return f"{why}: {inventory.format_cp(cp)} paid from the common fund", True
    data["debt_cp"] = int(data.get("debt_cp", 0)) + cp
    data["unpaid"] = int(data.get("unpaid", 0)) + 1
    return (f"{why}: {inventory.format_cp(cp)} NOT paid — "
            + ("the common fund is short" if fund is not None else "no common fund")
            + f"; debt now {inventory.format_cp(data['debt_cp'])}"), False


def _bill_note(data: dict, cp: int, fund: dict | None, why: str, key: str, **values) -> str:
    """Bill it and write it down: '<key>.paid' or '<key>.unpaid' for the display."""
    line, paid = _bill(data, cp, fund, why)
    if not line:
        return ""
    return note(data, line, key + (".paid" if paid else ".unpaid"), coins=inventory.format_cp(cp),
                debt=inventory.format_cp(int(data.get("debt_cp", 0))), **values)


def pass_days(data: dict, days: float, fund: dict | None = None) -> list:
    """Days go by: the works move on, upkeep is counted and billed every 30 days. Lines of news."""
    news = []
    if days <= 0:
        return news
    rate = 1.0 if data.get("present") else AWAY_RATE
    upkeep_days = days if ready(data) else 0.0
    b = data.get("build")
    if b and not b.get("done"):
        to_finish = (float(b["days_needed"]) - b["days_done"]) / rate
        b["days_done"] = min(float(b["days_needed"]), b["days_done"] + days * rate)
        if b["days_done"] >= b["days_needed"] - 1e-9:
            b["done"] = True
            upkeep_days = max(0.0, days - to_finish)          # the upkeep starts on the day it stands
            news.append(note(data, f"{data['name']} is finished", "finished", name=data["name"]))
    for imp in data["improvements"]:
        if imp.get("done"):
            continue
        imp["days_done"] = min(float(imp["days_needed"]), imp["days_done"] + days * rate)
        if imp["days_done"] >= imp["days_needed"] - 1e-9:
            imp["done"] = True
            news.append(note(data, f"{imp['name']} is finished", "finished", name=imp["name"]))
    if upkeep_days > 0:
        data["owed_days"] = round(float(data.get("owed_days", 0)) + upkeep_days, 4)
        while data["owed_days"] >= BILL_DAYS - 1e-9:
            data["owed_days"] -= BILL_DAYS
            line = _bill_note(data, daily_cp(data) * BILL_DAYS, fund, f"upkeep for {BILL_DAYS} days", "bill", days=BILL_DAYS)
            if line:
                news.append(line)
    return news


def pay_now(data: dict, fund: dict | None = None, payer_purse=None) -> list:
    """Pay what is owed today: the debt and the days counted since the last bill."""
    owed = int(data.get("debt_cp", 0)) + int(round(daily_cp(data) * float(data.get("owed_days", 0))))
    if owed <= 0:
        return ["nothing is owed"]
    if payer_purse is not None:
        payer_purse(owed)
        line, key = f"paid {inventory.format_cp(owed)} of upkeep", "paynow"
    else:
        if fund is None or treasury.total_cp(fund) < owed:
            raise StrongholdError(f"{inventory.format_cp(owed)} is owed and the common fund "
                                  + ("does not exist" if fund is None else f"holds only {inventory.format_coins(fund['coins'])}"))
        treasury.take(fund, {"gp": owed // 100, "sp": owed % 100 // 10, "cp": owed % 10}, "upkeep",
                      reason=data["name"])
        line, key = f"paid {inventory.format_cp(owed)} of upkeep from the common fund", "paynow.fund"
    data["debt_cp"], data["unpaid"], data["owed_days"] = 0, 0, 0
    return [note(data, line, key, coins=inventory.format_cp(owed))]


def business(data: dict, days: int, fund: dict | None = None, by: str = "", roll: int | None = None,
             rng: random.Random | None = None) -> dict:
    """Running it for `days` days of downtime (DMG p. 129). Settles the upkeep of those days."""
    if not ready(data):
        raise StrongholdError("it is still being built")
    if daily_cp(data) <= 0:
        raise StrongholdError("it costs nothing to keep: there is no business to run (set --upkeep)")
    rng = rng or random.Random()
    days = max(1, int(days))
    d100 = roll if roll is not None else rng.randint(1, 100)
    malus = 10 * int(data.get("unpaid", 0))
    total = d100 + min(days, 30) - malus
    kind, dice = next((k, d) for top, k, d in BUSINESS if total <= top)
    out = {"roll": d100, "days": days, "malus": malus, "total": total, "cost_cp": 0, "income_gp": 0, "lines": []}
    upkeep = daily_cp(data) * days
    if kind.startswith("pay"):
        out["cost_cp"] = int(round(upkeep * float(kind[3:])))
    elif kind == "earn":
        n, sides = dice
        out["income_gp"] = sum(rng.randint(1, sides) for _ in range(n)) * 5
    data["owed_days"] = max(0.0, float(data.get("owed_days", 0)) - days)
    head = (f"business for {days} days" + (f" ({_clean(by, 50)})" if by else "")
            + f": d100 {d100} + {min(days, 30)}" + (f" − {malus} (unpaid debts)" if malus else "") + f" = {total}")
    who = _clean(by, 50)
    if out["cost_cp"]:
        out["lines"].append(_bill_note(data, out["cost_cp"], fund, f"{head} — it does not cover itself",
                                       "business.cost", days=days, total=total, who=who))
    elif out["income_gp"]:
        if fund is not None:
            treasury.put(fund, {"gp": out["income_gp"]}, "income", by, data["name"])
            out["lines"].append(note(data, f"{head} — it covers itself and earns {out['income_gp']} gp, into the common fund",
                                     "business.earn", days=days, total=total, who=who, gp=out["income_gp"]))
        else:
            out["lines"].append(note(data, f"{head} — it covers itself and earns {out['income_gp']} gp "
                                           "(no common fund: hand it out with inventory.py gain)",
                                     "business.earn", days=days, total=total, who=who, gp=out["income_gp"]))
    else:
        out["lines"].append(note(data, f"{head} — it covers its own upkeep", "business.even", days=days, total=total, who=who))
    return out


# ─── What the screens see ───────────────────────────────────────────────────────

def public_view(data: dict | None) -> dict | None:
    if data is None:
        return None
    b = data.get("build")
    return {"name": data["name"], "kind": data["kind"], "place": data.get("place", ""), "note": data.get("note", ""),
            "ready": ready(data), "present": bool(data.get("present")),
            "build": ({"days_needed": b["days_needed"], "days_done": round(b["days_done"], 1), "cost_gp": b["cost_gp"],
                       "done": bool(b.get("done"))} if b else None),
            "daily": inventory.format_cp(daily_cp(data)), "daily_cp": daily_cp(data),
            "next_bill_days": (round(BILL_DAYS - float(data.get("owed_days", 0)), 1) if ready(data) and daily_cp(data) else None),
            "debt": inventory.format_cp(int(data.get("debt_cp", 0))) if data.get("debt_cp") else "",
            "skilled": int(data.get("skilled", 0)), "untrained": int(data.get("untrained", 0)),
            "staff": [{"name": s["name"], "role": s.get("role", "")} for s in data["staff"]],
            "improvements": [{"name": i["name"], "days_needed": i["days_needed"], "days_done": round(i["days_done"], 1),
                              "done": bool(i.get("done"))} for i in data["improvements"]],
            "log": [{k: e[k] for k in ("t", "text", "key", "vars") if k in e} for e in reversed(data["log"][-12:])]}


def describe(data: dict) -> str:
    lines = [f"{data['name']} — {data['kind'].replace('_', ' ')}" + (f", {data['place']}" if data.get("place") else "")]
    b = data.get("build")
    if b and not b.get("done"):
        lines.append(f"  being built: {b['days_done']:.1f}/{b['days_needed']} days of work ({b['cost_gp']} gp); "
                     + ("someone of the party oversees it" if data.get("present") else "nobody of the party there: one day of work in three"))
    lines.append(f"  upkeep {inventory.format_cp(daily_cp(data))}/day; staff {data.get('skilled', 0)} skilled, "
                 f"{data.get('untrained', 0)} untrained" + (f" + {', '.join(s['name'] + (' (' + s['role'] + ')' if s.get('role') else '') for s in data['staff'])}"
                                                             if data["staff"] else ""))
    if ready(data) and daily_cp(data):
        lines.append(f"  {float(data.get('owed_days', 0)):g} day(s) counted since the last bill (every {BILL_DAYS})")
    if data.get("debt_cp"):
        lines.append(f"  DEBT {inventory.format_cp(data['debt_cp'])} ({data.get('unpaid', 0)} unpaid bill(s): −10 each on business rolls)")
    for i in data["improvements"]:
        lines.append(f"  improvement: {i['name']} — " + ("done" if i.get("done") else f"{i['days_done']:.1f}/{i['days_needed']} days"))
    return "\n".join(lines)


# ─── Calendar hook ──────────────────────────────────────────────────────────────

def tick(campaign: str, days: float) -> list:
    """calendar.py moved on `days` whole days. Lines of news; nothing at all without a stronghold.json."""
    try:
        path = data_path(campaign)
    except Exception:
        return []
    if days <= 0 or not os.path.isfile(path):
        return []
    data = load(path)
    if data is None:
        return []
    fpath = fund_path(campaign)
    fund = treasury.load(fpath)
    news = pass_days(data, days, fund)
    save(path, data)
    if fund is not None:
        treasury.save(fpath, fund)
    if campaign == _active_campaign():
        _announce(news)
    return news


def _announce(news: list | None = None) -> None:
    try:
        import send
        body = json.dumps({"news": [str(n) for n in (news or [])][:4]}, ensure_ascii=False).encode("utf-8")
        send._post(f"{send.BASE_URL}/stronghold", body, send._read_token())
        send._post(f"{send.BASE_URL}/treasury", json.dumps({}).encode("utf-8"), send._read_token())   # upkeep, income
    except Exception:
        pass


# ─── Command line ─────────────────────────────────────────────────────────────

def _cp(tokens) -> int:
    try:
        return inventory.coins_cp(inventory.parse_coins(" ".join(tokens) if isinstance(tokens, list) else tokens))
    except inventory.InventoryError as e:
        raise StrongholdError(str(e))


def main(argv: list | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    ap = argparse.ArgumentParser(description="The party's own place: build it, keep it, run it.")
    ap.add_argument("--campaign", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("kinds", help="the table of properties")
    sub.add_parser("show")
    p = sub.add_parser("found", help="the party gets a place (owned, or --build)")
    p.add_argument("name")
    p.add_argument("--kind", required=True)
    p.add_argument("--place", default="")
    p.add_argument("--note", default="")
    p.add_argument("--build", action="store_true", help="it has to be built first")
    p.add_argument("--cost", nargs="+", help="building cost, when not the table's (e.g. 8000gp)")
    p.add_argument("--days", type=int, help="building days, when not the table's")
    p.add_argument("--upkeep", nargs="+", help="upkeep per day, when not the table's (e.g. 3gp)")
    p.add_argument("--pay", choices=["fund", "none"], default="none", help="pay the building cost from the common fund")
    p = sub.add_parser("present", help="someone of the party is there to oversee the works")
    p.add_argument("state", choices=["on", "off"])
    p = sub.add_parser("pass", help="days that pass outside calendar.py")
    p.add_argument("days", type=float)
    p = sub.add_parser("upkeep", help="what is owed; pay it")
    p.add_argument("action", nargs="?", choices=["pay"], default=None)
    p.add_argument("--from", dest="payer", default="fund", help='"fund" (default) or a character who pays from the sheet')
    p = sub.add_parser("hire", help="a named member of the staff")
    p.add_argument("name")
    p.add_argument("--role", default="")
    p.add_argument("--wage", nargs="+", help="their wage per day, on top of the upkeep (e.g. 2gp)")
    p = sub.add_parser("fire")
    p.add_argument("name")
    p = sub.add_parser("improve", help="an improvement: a library, a wall, a stable")
    p.add_argument("name")
    p.add_argument("--cost", nargs="+", required=True)
    p.add_argument("--days", type=int, required=True)
    p.add_argument("--upkeep", nargs="+", help="what it adds to the upkeep per day once finished")
    p.add_argument("--pay", choices=["fund", "none"], default="none")
    p = sub.add_parser("business", help="run it as a business for some days of downtime")
    p.add_argument("days", type=int)
    p.add_argument("--by", default="")
    p = sub.add_parser("set")
    p.add_argument("--upkeep", nargs="+")
    p.add_argument("--skilled", type=int)
    p.add_argument("--untrained", type=int)
    p.add_argument("--place")
    p.add_argument("--note")
    p.add_argument("--name")
    sub.add_parser("close", help="the party no longer has it")
    args = ap.parse_args(argv)

    if args.cmd == "kinds":
        for k, row in KINDS.items():
            if k == "custom":
                continue
            build = f"build {row['build'][0]} gp / {row['build'][1]} days" if row["build"] else "not built from scratch"
            print(f"{k:14} upkeep {inventory.format_cp(row['upkeep'])}/day · staff {row['skilled']} skilled, "
                  f"{row['untrained']} untrained · {build}")
        print("custom         give --upkeep (and --cost/--days with --build)")
        return 0
    try:
        camp = _campaign(args.campaign)
        path, fpath = data_path(camp), fund_path(camp)
        data = load(path)
        fund = treasury.load(fpath)
        fund_touched = False
        if args.cmd == "found":
            if data is not None:
                raise StrongholdError(f"the party has {data['name']} already — close it first")
            data = found(args.name, args.kind, args.place, args.build,
                         _cp(args.upkeep) if args.upkeep else None,
                         _cp(args.cost) // 100 if args.cost else None, args.days, args.note)
            if args.build and args.pay == "fund":
                if fund is None:
                    raise StrongholdError("no common fund to pay from")
                import paths
                word = "costruzione" if paths.campaign_language(camp) == "it" else "building"
                treasury.take(fund, {"gp": data["build"]["cost_gp"]}, "spend", reason=f"{data['name']} — {word}")
                fund_touched = True
            print(describe(data))
        elif data is None:
            raise StrongholdError("the party has no stronghold (found one first)")
        elif args.cmd == "show":
            print(describe(data))
            for e in list(reversed(data["log"]))[:8]:
                print(f"  {time.strftime('%d/%m', time.localtime(e['t']))}  {e['text']}")
            return 0
        elif args.cmd == "present":
            data["present"] = args.state == "on"
            print(f"{data['name']}: " + ("someone of the party oversees the works" if data["present"]
                                         else "nobody of the party there — the works go at a third of the pace"))
        elif args.cmd == "pass":
            for line in pass_days(data, args.days, fund) or ["nothing to report"]:
                print(line)
            fund_touched = True
        elif args.cmd == "upkeep":
            if args.action != "pay":
                owed = int(data.get("debt_cp", 0)) + int(round(daily_cp(data) * float(data.get("owed_days", 0))))
                print(f"{inventory.format_cp(daily_cp(data))}/day; owed now {inventory.format_cp(owed)}")
                return 0
            if args.payer == "fund":
                lines = pay_now(data, fund)
                fund_touched = True
            else:
                lines = pay_now(data, None, lambda cp: inventory.op_pay(args.payer, cp, camp))
            print("\n".join(lines))
        elif args.cmd == "hire":
            if any(_key(s["name"]) == _key(args.name) for s in data["staff"]):
                raise StrongholdError(f"{args.name} works there already")
            data["staff"].append({"name": _clean(args.name, 50), "role": _clean(args.role, 60),
                                  "wage_cp": _cp(args.wage) if args.wage else 0})
            print(note(data, f"{args.name} joins the staff" + (f" as {args.role}" if args.role else ""),
                       "hire.role" if args.role else "hire", name=_clean(args.name, 50), role=_clean(args.role, 60)))
        elif args.cmd == "fire":
            before = len(data["staff"])
            data["staff"] = [s for s in data["staff"] if _key(s["name"]) != _key(args.name)]
            if len(data["staff"]) == before:
                raise StrongholdError(f"nobody called {args.name} works there")
            print(note(data, f"{args.name} leaves the staff", "fire", name=_clean(args.name, 50)))
        elif args.cmd == "improve":
            cost = _cp(args.cost)
            if args.pay == "fund":
                if fund is None:
                    raise StrongholdError("no common fund to pay from")
                treasury.take(fund, {"gp": cost // 100, "sp": cost % 100 // 10, "cp": cost % 10}, "spend",
                              reason=f"{data['name']}: {args.name}")
                fund_touched = True
            data["improvements"].append({"name": _clean(args.name, 80), "cost_cp": cost, "days_needed": max(1, args.days),
                                         "days_done": 0.0, "upkeep_cp": _cp(args.upkeep) if args.upkeep else 0,
                                         "done": False})
            print(note(data, f"Works begin: {args.name}, {inventory.format_cp(cost)}, {args.days} days", "improve",
                       name=_clean(args.name, 80), coins=inventory.format_cp(cost), days=max(1, args.days)))
        elif args.cmd == "business":
            out = business(data, args.days, fund, args.by)
            fund_touched = True
            print("\n".join(out["lines"]))
        elif args.cmd == "set":
            if args.upkeep:
                data["upkeep_cp"] = _cp(args.upkeep)
            for key in ("skilled", "untrained"):
                if getattr(args, key) is not None:
                    data[key] = max(0, getattr(args, key))
            for key, n in (("place", 80), ("note", 240), ("name", 80)):
                if getattr(args, key) is not None:
                    data[key] = _clean(getattr(args, key), n) or data[key]
            print(describe(data))
        elif args.cmd == "close":
            os.remove(path)
            print(f"{data['name']}: no longer the party's." + (f" Debt left unpaid: {inventory.format_cp(data['debt_cp'])}."
                                                              if data.get("debt_cp") else ""))
            if camp == _active_campaign():
                _announce()
            return 0
        save(path, data)
        if fund_touched and fund is not None:
            treasury.save(fpath, fund)
        if camp == _active_campaign():
            _announce(data["log"][-1:] and [data["log"][-1]["text"]])
    except (StrongholdError, treasury.TreasuryError, inventory.InventoryError) as e:
        print(f"stronghold: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
