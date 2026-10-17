#!/usr/bin/env python3
"""downtime.py — what the characters do between adventures, chosen from the phone.

Between sessions (or when the party settles somewhere for a while) the DM opens
a stretch of free time; each player picks, on their own screen, what their
character does with it — the downtime activities of the Player's Handbook
(2014, p. 187) — and says what exactly. At the next session the DM closes it:
long projects (crafting an item, learning a language or a tool) move forward
day by day and are remembered from one stretch to the next, the costs are
worked out (and paid from the sheets with --charge), and the DM gets one line
per character to narrate from.

    downtime.py open 7 --note "Una settimana a Vallombra, in attesa della neve"
    downtime.py open 10 --only "Lyra, Hernio"          # only these characters
    downtime.py show                                   # who chose what so far
    downtime.py close                                  # the outcome, projects moved on
    downtime.py close --charge                         # …and the costs paid from the sheets
    downtime.py cancel                                 # close it with nothing done
    downtime.py projects                               # the long projects under way

Activities: craft (fabbricare: 5 gp of progress a day, materials for half of
that), work (lavorare: a modest lifestyle paid, wealthy with Performance),
recuperate (riprendersi: three days, then a DC 15 Constitution save), research
(ricerca: 1 gp a day, the DM decides what is found), train (addestrarsi: a
language or a tool in 250 days at 1 gp a day), carouse (gozzovigliare: the
DM's carousing table), business (gestire un'attività: d100 + days, DMG p. 129 —
rolled on the party's stronghold by `close` when there is one) and other
(altro: anything else, said in words).

The choices live in <campaign>/downtime.json, so they wait for the DM even if
the display is switched off in between. When every player has chosen, the DM
is told with the next check_input.py as `[[DOWNTIME: …]]`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

ACTIVITIES = ("craft", "work", "recuperate", "research", "train", "carouse", "business", "other")
ALIASES = {"fabbricare": "craft", "creare": "craft", "lavorare": "work", "lavoro": "work", "profession": "work",
           "riprendersi": "recuperate", "recupero": "recuperate", "ricerca": "research", "ricercare": "research",
           "addestrarsi": "train", "addestramento": "train", "training": "train", "gozzovigliare": "carouse",
           "attività": "business", "gestire": "business", "gestione": "business", "altro": "other"}
MAX_DAYS = 365
TRAIN_DAYS = 250            # PHB 2014: a language or a tool proficiency
CRAFT_GP_PER_DAY = 5        # market value made per day; materials cost half
DETAIL = 300
_PRICE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(gp|mo|po)\b", re.I)


class DowntimeError(Exception):
    """A request that cannot be applied; nothing was changed."""


# ─── Data ─────────────────────────────────────────────────────────────────────

def _active_campaign() -> str:
    import media
    return media.active_campaign() or ""


def _campaign(campaign: str = "") -> str:
    import media
    camp = media.safe_campaign(campaign) if campaign else media.active_campaign()
    if not camp:
        raise DowntimeError("no active campaign — pass --campaign or load one first")
    return camp


def data_path(campaign: str = "") -> str:
    import media
    return str(media.find_campaign(_campaign(campaign)) / "downtime.json")


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        data = {}
    except ValueError as e:
        raise DowntimeError(f"downtime.json is not valid JSON ({e})")
    if not isinstance(data, dict):
        data = {}
    data.setdefault("window", None)
    data.setdefault("projects", {})
    data.setdefault("history", [])
    return data


def save(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _clean(text, limit: int) -> str:
    return " ".join(re.sub(r"[`\\$<>]", "", str(text or "")).split())[:limit]


def _key(name: str) -> str:
    return "".join(ch for ch in (name or "").casefold() if ch.isalnum())


def activity_of(word: str) -> str:
    w = (word or "").strip().lower()
    w = ALIASES.get(w, w)
    if w not in ACTIVITIES:
        raise DowntimeError(f"{word!r} is not a downtime activity ({', '.join(ACTIVITIES)})")
    return w


def price_gp(text: str):
    """'Pozione di guarigione 50gp' → 50.0, or None."""
    m = _PRICE_RE.search(text or "")
    return float(m.group(1).replace(",", ".")) if m else None


# ─── The window ───────────────────────────────────────────────────────────────

def open_window(data: dict, days: int, players: list, note: str = "") -> dict:
    if data.get("window") and not data["window"].get("closed"):
        raise DowntimeError("a stretch of downtime is open already — close or cancel it first")
    if not 1 <= int(days) <= MAX_DAYS:
        raise DowntimeError(f"between 1 and {MAX_DAYS} days")
    players = list(dict.fromkeys(_clean(p, 50) for p in players if _clean(p, 50)))
    if not players:
        raise DowntimeError("nobody to ask: no characters at the table (use --only)")
    data["window"] = {"id": secrets.token_hex(4), "days": int(days), "note": _clean(note, DETAIL),
                      "players": players, "choices": {}, "opened": time.time(), "closed": False, "told": False}
    return data["window"]


def choose(data: dict, character: str, activity: str, detail: str = "") -> tuple:
    """A player's choice (it can be changed while the window is open). Returns (name, everyone chose)."""
    win = data.get("window")
    if not win or win.get("closed"):
        raise DowntimeError("there is no downtime to choose for")
    name = next((p for p in win["players"] if _key(p) == _key(character)), None)
    if not name:
        raise DowntimeError(f"{character} is not one of those asked")
    kind = activity_of(activity)
    detail = _clean(detail, DETAIL)
    if kind in ("craft", "research", "train", "other") and not detail:
        raise DowntimeError("say what, exactly (what to craft, what to research, what to learn…)")
    win["choices"][name] = {"activity": kind, "detail": detail, "ts": time.time()}
    return name, set(win["choices"]) >= set(win["players"])


def _project(data: dict, name: str, kind: str, what: str) -> dict:
    rows = data["projects"].setdefault(name, [])
    hit = next((p for p in rows if p["kind"] == kind and _key(p["what"]) == _key(what)), None)
    if hit is None:
        need = TRAIN_DAYS if kind == "train" else None
        price = price_gp(what) if kind == "craft" else None
        hit = {"kind": kind, "what": what, "days": 0, "gp": 0.0, "progress_gp": 0.0, "need_days": need,
               "price_gp": price, "done": False}
        rows.append(hit)
    return hit


def outcome(data: dict, name: str, choice: dict, days: int) -> dict:
    """What a stretch of `days` days of this activity gives and costs. Moves long projects on."""
    kind, what = choice["activity"], choice.get("detail", "")
    out = {"character": name, "activity": kind, "detail": what, "days": days, "cost_gp": 0.0, "text": ""}
    if kind == "craft":
        p = _project(data, name, kind, what)
        p["days"] += days
        p["progress_gp"] += CRAFT_GP_PER_DAY * days
        out["cost_gp"] = CRAFT_GP_PER_DAY * days / 2
        if p["price_gp"]:
            p["progress_gp"] = min(p["progress_gp"], p["price_gp"])
            p["done"] = p["progress_gp"] >= p["price_gp"]
            out["text"] = (f"crafting {what}: {p['progress_gp']:g} of {p['price_gp']:g} gp of work"
                           + (" — FINISHED: add it to the sheet" if p["done"] else "")
                           + f"; raw materials {out['cost_gp']:g} gp. Needs proficiency with the tools it takes; "
                           "while crafting they live a modest lifestyle for free (comfortable at half price).")
        else:
            out["text"] = (f"crafting {what}: {p['progress_gp']:g} gp of work so far (no price given — compare it with the "
                           f"item's market value); raw materials {out['cost_gp']:g} gp. Needs proficiency with the tools; "
                           "while crafting they live a modest lifestyle for free (comfortable at half price).")
        p["gp"] += out["cost_gp"]
    elif kind == "train":
        p = _project(data, name, kind, what)
        p["days"] += days
        out["cost_gp"] = float(days)
        p["gp"] += days
        p["done"] = p["days"] >= TRAIN_DAYS
        out["text"] = (f"training in {what}: {min(p['days'], TRAIN_DAYS)} of {TRAIN_DAYS} days, {days} gp"
                       + (" — LEARNED: write the proficiency on the sheet" if p["done"] else "") + ". Needs a teacher.")
    elif kind == "research":
        out["cost_gp"] = float(days)
        out["text"] = (f"researching {what}: {days} days, {days} gp on top of their lifestyle (books, bribes, a library). "
                       "Decide what is found — one or two real leads, from the secrets you prepared.")
    elif kind == "work":
        out["text"] = (f"working{(' as ' + what) if what else ''}: {days} days of a modest lifestyle paid "
                       "(wealthy instead if they perform with Performance proficiency, comfortable as a member of a guild).")
    elif kind == "recuperate":
        out["text"] = ("recuperating: " + ("a DC 15 Constitution save at the end — on a success, one effect that keeps "
                                          "them from regaining HP ends, or advantage on saves against one disease or "
                                          "poison for 24 hours." if days >= 3 else "less than the 3 days it takes: no benefit."))
    elif kind == "carouse":
        out["text"] = (f"carousing{(' — ' + what) if what else ''}: {days} days of revelry at their lifestyle's cost; "
                       "roll on your carousing table (DMG) for what comes of it.")
    elif kind == "business":
        out["text"] = (f"running a business{(' — ' + what) if what else ''}: d100 + {min(days, 30)} on the DMG table (p. 129) "
                       "— with the party's stronghold, downtime.py close rolls it (stronghold.py business).")
    else:
        out["text"] = f"{what}: decide what {days} days of it bring."
    return out


def _carouse_rumor(campaign: str, who: str) -> str:
    """Days of revelry bring a rumor with them, without a check (rumors.py)."""
    try:
        import rumors
        got = rumors.give(campaign, who, 1)
        if not got:
            return ""
        r = got[0]
        rumors._announce({"what": "heard", "who": who, "n": 1})
        return f" The revelry brings a rumor ({r['truth']}): «{r['text']}» — tell it."
    except Exception:
        return ""


def close_window(data: dict) -> list:
    """End the stretch: outcomes per character (those who did not choose are listed too)."""
    win = data.get("window")
    if not win or win.get("closed"):
        raise DowntimeError("no downtime is open")
    results = []
    for name in win["players"]:
        choice = win["choices"].get(name)
        if not choice:
            results.append({"character": name, "activity": None, "days": win["days"], "cost_gp": 0.0,
                            "text": "did not choose — decide with the player, or let the days pass."})
            continue
        results.append(outcome(data, name, choice, win["days"]))
    win["closed"] = True
    data["history"].append({"id": win["id"], "days": win["days"], "note": win.get("note", ""),
                            "results": results, "closed": time.time()})
    del data["history"][:-20]
    return results


def format_ready(win: dict) -> str:
    parts = [f"{n}: {c['activity']}" + (f" ({c['detail']})" if c.get("detail") else "")
             for n, c in win.get("choices", {}).items()]
    return (f"[[DOWNTIME: every player has chosen for the {win['days']} days of downtime — " + "; ".join(parts)
            + ". Run downtime.py close (--charge) when you play it out.]]")


def describe_window(win: dict | None) -> str:
    if not win:
        return "no downtime"
    state = "closed" if win.get("closed") else "open"
    lines = [f"{win['days']} days of downtime ({state})" + (f" — {win['note']}" if win.get("note") else "")]
    for name in win["players"]:
        c = win["choices"].get(name)
        lines.append(f"  {name}: " + (f"{c['activity']}" + (f" — {c['detail']}" if c.get("detail") else "") if c else "not chosen yet"))
    return "\n".join(lines)


def public_view(data: dict, me: str = "", dm: bool = False) -> dict | None:
    """What a screen sees: the open window, who has chosen, one's own choice (the DM sees all)."""
    win = data.get("window")
    if not win or win.get("closed"):
        return None
    mine = next((c for n, c in win["choices"].items() if me and _key(n) == _key(me)), None)
    view = {"id": win["id"], "days": win["days"], "note": win.get("note", ""), "players": list(win["players"]),
            "chosen": [n for n in win["players"] if n in win["choices"]], "activities": list(ACTIVITIES),
            "mine": {"activity": mine["activity"], "detail": mine.get("detail", "")} if mine else None,
            "asked": bool(me) and any(_key(p) == _key(me) for p in win["players"])}
    projects = []
    for name, rows in (data.get("projects") or {}).items():
        if dm or (me and _key(name) == _key(me)):
            projects += [{"character": name, "kind": p["kind"], "what": p["what"], "days": p["days"],
                          "need_days": p.get("need_days"), "progress_gp": p.get("progress_gp", 0),
                          "price_gp": p.get("price_gp"), "done": p.get("done", False)} for p in rows if not p.get("done")]
    view["projects"] = projects
    if dm:
        view["choices"] = {n: dict(c) for n, c in win["choices"].items()}
    return view


# ─── Telling the DM (written by the display server, drained by check_input.py) ─

def _notes_file() -> str:
    from runtime_paths import rt
    return rt("downtime_notes.json")


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


def _announce(event: dict) -> None:
    """Tell the display. Silent when it is not running."""
    try:
        import send
        send._post(f"{send.BASE_URL}/downtime", json.dumps({"event": event}).encode("utf-8"), send._read_token())
    except Exception:
        pass


def _run_business(camp: str, result: dict) -> str:
    """A character ran the party's stronghold: roll it there (stronghold.py), or leave the DM the table."""
    try:
        import stronghold
        import treasury
        path, fpath = stronghold.data_path(camp), stronghold.fund_path(camp)
        data = stronghold.load(path)
        if data is None:
            return result["text"]
        fund = treasury.load(fpath)
        out = stronghold.business(data, result["days"], fund, result["character"])
        stronghold.save(path, data)
        if fund is not None:
            treasury.save(fpath, fund)
        return f"running {data['name']}: " + "; ".join(out["lines"])
    except Exception as e:
        return result["text"] + f" (stronghold.py could not roll it: {e})"


def _party() -> list:
    from runtime_paths import rt
    try:
        with open(rt("stats.json"), encoding="utf-8") as f:
            players = json.load(f).get("players") or []
    except (OSError, ValueError, AttributeError):
        return []
    return [str(p.get("name")) for p in players if isinstance(p, dict) and p.get("name")]


# ─── Command line ─────────────────────────────────────────────────────────────

def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="Downtime activities, chosen from the players' phones.")
    ap.add_argument("--campaign", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("open", help="open a stretch of downtime")
    p.add_argument("days", type=int)
    p.add_argument("--note", default="")
    p.add_argument("--only", default="", help='only these characters, "A, B" (default: everyone at the table)')
    p = sub.add_parser("close", help="end it: outcomes, projects moved on")
    p.add_argument("--charge", action="store_true", help="pay the costs from the sheets (inventory.py)")
    for cmd in ("show", "cancel", "projects"):
        sub.add_parser(cmd)
    args = ap.parse_args(argv)

    try:
        camp = _campaign(args.campaign)
        path = data_path(camp)
        data = load(path)
        event = None
        if args.cmd == "show":
            print(describe_window(data.get("window")))
            return 0
        if args.cmd == "projects":
            rows = [(n, p) for n, ps in data["projects"].items() for p in ps]
            for n, p in rows:
                goal = (f"{p['progress_gp']:g}/{p['price_gp']:g} gp" if p["kind"] == "craft" and p.get("price_gp")
                        else f"{p['days']}/{p['need_days']} days" if p.get("need_days") else f"{p['days']} days")
                print(f"{n}: {p['kind']} {p['what']} — {goal}" + (" — done" if p.get("done") else ""))
            if not rows:
                print("no projects under way")
            return 0
        if args.cmd == "open":
            players = [n.strip() for n in args.only.split(",") if n.strip()] or _party()
            win = open_window(data, args.days, players, args.note)
            event = {"what": "open", "id": win["id"], "days": win["days"]}
            print(describe_window(win))
        elif args.cmd == "cancel":
            if not data.get("window") or data["window"].get("closed"):
                raise DowntimeError("no downtime is open")
            data["window"]["closed"] = True
            event = {"what": "closed"}
            print("downtime cancelled")
        elif args.cmd == "close":
            results = close_window(data)
            event = {"what": "closed"}
            charged = []
            for r in results:
                if r.get("activity") == "business":
                    r["text"] = _run_business(camp, r)
                if r.get("activity") == "carouse":
                    r["text"] += _carouse_rumor(camp, r["character"])
                line = f"{r['character']}: {r['text']}"
                if args.charge and r["cost_gp"]:
                    try:
                        import inventory
                        cp = int(round(r["cost_gp"] * 100))
                        left = inventory.op_pay(r["character"], cp, camp)
                        charged.append(f"{r['character']} paid {inventory.format_cp(cp)} ({inventory.format_coins(left)} left)")
                    except Exception as e:
                        charged.append(f"{r['character']} could NOT pay {r['cost_gp']:g} gp: {e}")
                elif r["cost_gp"]:
                    line += f" [cost {r['cost_gp']:g} gp — not charged]"
                print(line)
            for c in charged:
                print("  " + c)
        save(path, data)
        if camp == _active_campaign() and event:
            _announce(event)
    except DowntimeError as e:
        print(f"downtime: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
