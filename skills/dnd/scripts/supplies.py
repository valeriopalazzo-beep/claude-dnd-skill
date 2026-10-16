#!/usr/bin/env python3
"""supplies.py — the party's food, water and light, spent as the hours go by.

A journey is not only a line on the region map: every day on the road eats a
ration and a day of water per mouth, and every hour underground burns a torch.
Here the party's stock lives in <campaign>/supplies.json and is spent by the
clock: `calendar.py advance` and `calendar.py rest` call `tick`, so the lit
torches and lanterns burn down hour by hour and, while the party is on the
march (living off what it carries), each new day eats its share.

    supplies.py set rations 30 water 20 torches 10 oil 2     # what the party carries (Italian words too)
    supplies.py add torches 5                               # bought, found, foraged
    supplies.py use rations 2                               # eaten or lost outside the clock
    supplies.py mouths 6                                    # who eats from it (mule included)
    supplies.py march on --hot                              # on the road: each day eats its share (hot: double water)
    supplies.py march off                                   # in town, at an inn: the stock is not touched
    supplies.py light torch --keep                          # a torch is lit (--keep lights the next when it burns out)
    supplies.py light lantern                               # a lantern burns 6 hours on a flask of oil
    supplies.py light torch --by Aldric                     # who holds it: the battle map lights around them
    supplies.py carry torch Mira                            # a burning torch changes hands
    supplies.py douse all                                   # or: douse torch
    supplies.py pass 90m                                    # time with no calendar: 90m, 3h, 2d
    supplies.py day 2                                       # two days eaten now, marching or not
    supplies.py show
    supplies.py push                                        # send it to the display again

Rations and water are counted in days for one person ("acqua 20" = twenty
person-days of water: a waterskin holds about half a day). A torch burns 1
hour, a candle 1 hour, a lantern or a lamp 6 hours per flask of oil (PHB
2014). When the food or water is not enough, the script says so and gives
the rule (PHB p. 185): without food a character lasts 3 + CON modifier days
(at least 1), then gains a level of exhaustion each day; with half the water,
a DC 15 Constitution save or a level of exhaustion; with less, a level
automatically; two levels for one already exhausted. Anything that does not match exits non-zero and changes
nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

KINDS = ("rations", "water", "torches", "oil", "candles")
ALIASES = {"razioni": "rations", "razione": "rations", "cibo": "rations", "food": "rations", "ration": "rations",
           "acqua": "water", "torce": "torches", "torcia": "torches", "torch": "torches",
           "olio": "oil", "ampolle": "oil", "flasks": "oil", "candele": "candles", "candela": "candles",
           "candle": "candles"}
# What burns, for how many minutes, and what it eats from the stock when lit.
LIGHTS = {"torch": (60, "torches"), "lantern": (360, "oil"), "lamp": (360, "oil"), "candle": (60, "candles")}
LIGHT_ALIASES = {"torcia": "torch", "torce": "torch", "torches": "torch", "lanterna": "lantern",
                 "lanterns": "lantern", "lampada": "lamp", "lamps": "lamp", "candela": "candle", "candele": "candle",
                 "candles": "candle"}
MAX_STOCK = 99999
MAX_LIGHTS = 12


class SupplyError(Exception):
    """A request that cannot be applied; the supplies are left as they were."""


# ─── Data ─────────────────────────────────────────────────────────────────────

def _campaign(campaign: str = "") -> str:
    import paths
    camp = campaign or _active_campaign()
    if not camp:
        raise SupplyError("no active campaign — pass --campaign or load one first")
    try:
        paths.find_campaign(camp)
    except Exception:
        raise SupplyError(f"no campaign called {camp!r}")
    return camp


def _active_campaign() -> str:
    import paths
    try:
        return (paths.runtime_dir() / ".campaign").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def data_path(campaign: str) -> str:
    import paths
    return str(paths.find_campaign(campaign) / "supplies.json")


def blank() -> dict:
    return {"stock": {k: 0 for k in KINDS}, "mouths": 1, "march": False, "hot": False, "lights": [],
            "hungry_days": 0}


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        return blank()
    except ValueError as e:
        raise SupplyError(f"supplies.json is not valid JSON ({e})")
    out = blank()
    if isinstance(data, dict):
        out.update({k: data[k] for k in out if k in data})
        out["stock"] = {k: int((data.get("stock") or {}).get(k, 0) or 0) for k in KINDS}
        out["lights"] = [l for l in data.get("lights") or [] if isinstance(l, dict) and l.get("kind") in LIGHTS]
    return out


def save(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def kind_of(word: str) -> str:
    w = (word or "").strip().lower()
    w = ALIASES.get(w, w)
    if w not in KINDS:
        raise SupplyError(f"{word!r} is not a supply (rations, water, torches, oil, candles)")
    return w


def light_of(word: str) -> str:
    w = (word or "").strip().lower()
    w = LIGHT_ALIASES.get(w, w)
    if w not in LIGHTS:
        raise SupplyError(f"{word!r} is not a light (torch, lantern, lamp, candle)")
    return w


def _count(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise SupplyError(f"a quantity is a whole number, not {value!r}")
    if not 0 <= n <= MAX_STOCK:
        raise SupplyError(f"{n} is out of range")
    return n


def pairs(tokens: list) -> list:
    """['rations', '30', 'acqua', '20'] → [('rations', 30), ('water', 20)]."""
    if not tokens or len(tokens) % 2:
        raise SupplyError("give pairs: a supply and a number (rations 30 water 20)")
    return [(kind_of(tokens[i]), _count(tokens[i + 1])) for i in range(0, len(tokens), 2)]


def parse_span(text: str) -> int:
    """'90m' / '3h' / '2d' / '1,5h' → minutes."""
    m = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(m|min|minuti|minutes|h|ore|hours|d|g|giorni|days)\s*", str(text or ""), re.I)
    if not m:
        raise SupplyError(f"not a span of time: {text!r} (write 90m, 3h or 2d)")
    n = float(m.group(1).replace(",", "."))
    unit = m.group(2).lower()[0]
    return int(round(n * (1 if unit == "m" else 60 if unit in ("h", "o") else 1440)))


# ─── Operations ───────────────────────────────────────────────────────────────

def days_left(data: dict) -> float | None:
    """How many days the food and water last at this pace; None when nobody eats from it."""
    mouths = max(0, int(data.get("mouths") or 0))
    if not mouths:
        return None
    water = data["stock"]["water"] / (2 if data.get("hot") else 1)
    return min(data["stock"]["rations"], water) / mouths


def light(data: dict, kind: str, keep: bool = False, by: str = "") -> dict:
    """Light one (it takes its torch, candle or flask of oil from the stock); `by` is who holds it."""
    kind = light_of(kind)
    minutes, stock = LIGHTS[kind]
    if data["stock"][stock] < 1:
        raise SupplyError(f"no {stock} left to light a {kind}")
    if len(data["lights"]) >= MAX_LIGHTS:
        raise SupplyError(f"{MAX_LIGHTS} lights are burning already")
    data["stock"][stock] -= 1
    entry = {"kind": kind, "left": minutes, "keep": bool(keep)}
    if " ".join((by or "").split()):
        entry["by"] = " ".join(by.split())[:60]
    data["lights"].append(entry)
    return entry


def carry(data: dict, kind: str, by: str) -> dict:
    """A burning light changes hands: the first of that kind nobody holds, else the first of that kind."""
    kind = light_of(kind)
    who = " ".join((by or "").split())[:60]
    if not who:
        raise SupplyError("carried by whom?")
    burning = [l for l in data["lights"] if l["kind"] == kind]
    if not burning:
        raise SupplyError(f"no {kind} is burning")
    entry = next((l for l in burning if not l.get("by")), burning[0])
    entry["by"] = who
    return entry


def douse(data: dict, kind: str = "all") -> int:
    """Put lights out (what is left of them is lost). Returns how many."""
    before = len(data["lights"])
    if kind in ("all", "tutte", "tutto"):
        data["lights"] = []
    else:
        k = light_of(kind)
        data["lights"] = [l for l in data["lights"] if l["kind"] != k]
    return before - len(data["lights"])


def burn(data: dict, minutes: int) -> list:
    """Let the lights burn for `minutes`. Returns what happened, as lines."""
    news, kept = [], []
    for entry in data["lights"]:
        left = int(entry.get("left", 0)) - minutes
        while left <= 0 and entry.get("keep"):
            stock = LIGHTS[entry["kind"]][1]
            if data["stock"][stock] < 1:
                break
            data["stock"][stock] -= 1
            left += LIGHTS[entry["kind"]][0]
            news.append(f"a {entry['kind']} burned out; another one is lit ({data['stock'][stock]} {stock} left)")
        if left > 0:
            entry["left"] = left
            kept.append(entry)
        else:
            news.append(f"the {entry['kind']} goes out" + (" — none left to light another" if entry.get("keep") else ""))
    data["lights"] = kept
    if news and not kept:
        news.append("NO LIGHT: the party is in the dark unless someone sees in it")
    return news


def eat(data: dict, days: int) -> list:
    """`days` days of food and water for every mouth. Returns warnings, as lines."""
    news = []
    mouths = max(0, int(data.get("mouths") or 0))
    for _ in range(max(0, days)):
        need_food = mouths
        need_water = mouths * (2 if data.get("hot") else 1)
        food = min(need_food, data["stock"]["rations"])
        water = min(need_water, data["stock"]["water"])
        data["stock"]["rations"] -= food
        data["stock"]["water"] -= water
        if food < need_food:
            data["hungry_days"] = int(data.get("hungry_days") or 0) + 1
            news.append(f"FOOD: only {food} of {need_food} rations today — {need_food - food} go hungry "
                        f"(day {data['hungry_days']} without enough food). Each can go 3 + CON modifier days "
                        "(at least 1) without food; after that, one level of exhaustion per day (half a ration counts as half a day), "
                        "which does not go until they eat a full day's ration; a day of normal meals resets the count.")
        else:
            data["hungry_days"] = 0
        if water < need_water:
            # PHB 2014 p. 185: half the water → DC 15 Constitution save at the end of the day or one level of
            # exhaustion; even less → one level automatically; already exhausted → two levels in either case.
            if water * 2 >= need_water:
                rule = ("shared out, each drinks at least half: a DC 15 Constitution save at the end of the day "
                        "or one level of exhaustion")
            else:
                rule = "less than half each: one level of exhaustion at the end of the day, no save"
            news.append(f"WATER: only {water} of {need_water} days of water today — {rule} (two levels in either "
                        "case for a character already exhausted; it does not go until they drink the full amount)")
    left = days_left(data)
    if left is not None and not news and left < 2:
        news.append(f"supplies for {left:.1f} more day(s) at this pace")
    return news


def tick(campaign: str, minutes: int, days_crossed: int = 0, quiet: bool = False) -> list:
    """Time passed (calendar.py): burn the lights and, on the march, eat each new day. Lines of news.

    Does nothing at all for a campaign that keeps no supplies.json."""
    try:
        path = data_path(campaign)
    except Exception:
        return []
    if not os.path.isfile(path):
        return []
    data = load(path)
    news = burn(data, max(0, int(minutes))) if data["lights"] else []
    if data.get("march") and days_crossed > 0:
        news += eat(data, days_crossed)
    save(path, data)
    if campaign == _active_campaign():
        _announce(data, news)
    return news


def public_view(data: dict) -> dict:
    left = days_left(data)
    return {"stock": dict(data["stock"]), "mouths": int(data.get("mouths") or 0), "march": bool(data.get("march")),
            "hot": bool(data.get("hot")), "days_left": None if left is None else round(left, 1),
            "lights": [{"kind": l["kind"], "left": int(l["left"]), "keep": bool(l.get("keep")),
                        **({"by": l["by"]} if l.get("by") else {})} for l in data["lights"]],
            "hungry_days": int(data.get("hungry_days") or 0)}


def describe(data: dict) -> str:
    s = data["stock"]
    left = days_left(data)
    lines = [f"rations {s['rations']} · water {s['water']} · torches {s['torches']} · oil {s['oil']} · candles {s['candles']}",
             f"{data.get('mouths', 0)} mouth(s); " + ("on the march" if data.get("march") else "not marching")
             + (", hot weather (double water)" if data.get("hot") else "")
             + (f"; food and water for {left:.1f} day(s)" if left is not None else "")]
    for l in data["lights"]:
        lines.append(f"  lit: {l['kind']}" + (f" ({l['by']})" if l.get("by") else "") + f", {l['left'] // 60}h{l['left'] % 60:02d} left"
                     + (" (keeps lighting the next)" if l.get("keep") else ""))
    if data.get("hungry_days"):
        lines.append(f"  {data['hungry_days']} day(s) in a row without enough food")
    return "\n".join(lines)


def _announce(data: dict, news: list | None = None) -> None:
    """Tell the display. Silent when it is not running."""
    try:
        import paths
        sys.path.insert(0, str(paths.display_dir()))
        import send
        body = {"supplies": public_view(data)}
        if news:
            body["news"] = [n for n in news][:6]
        send._post(f"{send.BASE_URL}/supplies", json.dumps(body, ensure_ascii=False).encode("utf-8"), send._read_token())
    except Exception:
        pass


# ─── Command line ─────────────────────────────────────────────────────────────

def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="The party's food, water and light, spent as the hours go by.")
    ap.add_argument("--campaign", "-c", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd, what in (("set", "set what the party carries"), ("add", "more of a supply"), ("use", "spend some")):
        sub.add_parser(cmd, help=what).add_argument("pairs", nargs="+")
    sub.add_parser("mouths", help="how many eat from the stock").add_argument("n", type=int)
    p = sub.add_parser("march", help="on the road (each day eats) or not")
    p.add_argument("state", choices=["on", "off", "si", "no"])
    p.add_argument("--hot", action="store_true", help="hot weather: double water")
    p = sub.add_parser("light", help="light a torch, a lantern or a candle")
    p.add_argument("kind")
    p.add_argument("--keep", action="store_true", help="light the next one when it burns out")
    p.add_argument("--by", default="", help="who holds it (the battle map lights around that token)")
    p = sub.add_parser("carry", help="a burning light changes hands")
    p.add_argument("kind")
    p.add_argument("by")
    sub.add_parser("douse", help="put lights out").add_argument("kind", nargs="?", default="all")
    sub.add_parser("pass", help="time passes with no calendar: 90m, 3h, 2d").add_argument("span")
    sub.add_parser("day", help="days of food and water eaten now").add_argument("n", type=int, nargs="?", default=1)
    for cmd in ("show", "push"):
        sub.add_parser(cmd)
    args = ap.parse_args(argv)

    try:
        camp = _campaign(args.campaign)
        path = data_path(camp)
        data = load(path)
        news = []
        if args.cmd == "show":
            print(describe(data))
            return 0
        if args.cmd == "push":
            if camp != _active_campaign():
                raise SupplyError(f"{camp} is not the campaign on the display")
            _announce(data)
            print("supplies sent to the display")
            return 0
        if args.cmd == "set":
            for kind, n in pairs(args.pairs):
                data["stock"][kind] = n
        elif args.cmd == "add":
            for kind, n in pairs(args.pairs):
                data["stock"][kind] = min(MAX_STOCK, data["stock"][kind] + n)
        elif args.cmd == "use":
            wanted = pairs(args.pairs)
            for kind, n in wanted:
                if n > data["stock"][kind]:
                    raise SupplyError(f"only {data['stock'][kind]} {kind} left, not {n}")
            for kind, n in wanted:
                data["stock"][kind] -= n
        elif args.cmd == "mouths":
            if not 0 <= args.n <= 50:
                raise SupplyError("between 0 and 50 mouths")
            data["mouths"] = args.n
        elif args.cmd == "march":
            data["march"] = args.state in ("on", "si")
            data["hot"] = bool(args.hot) and data["march"]
        elif args.cmd == "light":
            entry = light(data, args.kind, args.keep, args.by)
            news.append(f"a {entry['kind']} is lit" + (f" ({entry['by']})" if entry.get("by") else ""))
        elif args.cmd == "carry":
            entry = carry(data, args.kind, args.by)
            news.append(f"{entry['by']} holds the {entry['kind']}")
        elif args.cmd == "douse":
            if not douse(data, args.kind):
                raise SupplyError("nothing like that is burning")
        elif args.cmd == "pass":
            minutes = parse_span(args.span)
            news = burn(data, minutes)
            if data.get("march"):
                news += eat(data, minutes // 1440)
        elif args.cmd == "day":
            if args.n < 1:
                raise SupplyError("how many days? (at least 1)")
            news = eat(data, args.n)
        save(path, data)
        print(describe(data))
        for line in news:
            print("  ⚠ " + line if line.isupper() or line.split(":")[0].isupper() else "  · " + line)
        if camp == _active_campaign():
            _announce(data, news)
    except SupplyError as e:
        print(f"supplies: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
