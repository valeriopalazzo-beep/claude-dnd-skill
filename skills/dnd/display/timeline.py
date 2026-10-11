#!/usr/bin/env python3
"""timeline.py — what has happened in the campaign, in the order of the world's dates.

The session log tells the story evening by evening; this is the same story by
the calendar of the world: what happened on which day, so that "how long ago
was the fire?" has an answer every player can look up.

    timeline.py add "Il rogo della strega è fermato" --note "Corradi è disarmato; Ada resta da Gunda"
    timeline.py add "Arrivo a Vallombra" --date "12 Brumosa 1147" --time evening --session 1
    timeline.py add "La Custode scrive al Duca" --date "9 Brumosa 1147" --secret    # yours alone, until revealed
    timeline.py set "rogo" --time morning --note "…"          # also --title, --date, --session
    timeline.py reveal "custode"                              # the players see it from now on (hide: back to secret)
    timeline.py remove "rogo"
    timeline.py list                                          # all of them, the secret ones too

With no --date an event is of today: the campaign calendar's date and time of
day (calendar.py). A date is written as the calendar writes it ("14 Brumosa
1147", the year may be left out for the current one). Times of day: midnight,
dawn, morning, midday, afternoon, dusk, evening, night (Italian words are
understood). A campaign without a calendar can still keep a timeline: --date
is then any text, and the events stay in the order they were added.

The events live in <campaign>/timeline.json. Every screen has a Timeline
panel. An event can be named by any part of its title that means one event
only. Anything that does not match exits non-zero and changes nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

# The times of day the display has words for, with the hour that orders them within a day.
TIMES = {"midnight": 0, "dawn": 5, "morning": 8, "midday": 12, "afternoon": 15, "dusk": 18, "evening": 19, "night": 22}
TIME_ALIASES = {"mezzanotte": "midnight", "alba": "dawn", "mattina": "morning", "mattino": "morning",
                "mezzogiorno": "midday", "noon": "midday", "pomeriggio": "afternoon", "tramonto": "dusk",
                "sera": "evening", "notte": "night"}
TITLE, NOTE, DATE = 120, 400, 60
MAX_EVENTS = 2000


class TimelineError(Exception):
    """A request that cannot be applied; the timeline is left as it was."""


# ─── Data ─────────────────────────────────────────────────────────────────────

def _campaign(campaign: str = "") -> str:
    import media
    camp = media.safe_campaign(campaign) if campaign else media.active_campaign()
    if not camp:
        raise TimelineError("no active campaign — pass --campaign or load one first")
    return camp


def data_path(campaign: str = "") -> str:
    import media
    return str(media.find_campaign(_campaign(campaign)) / "timeline.json")


def load(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        return []
    except ValueError as e:
        raise TimelineError(f"timeline.json is not valid JSON ({e})")
    rows = data.get("events") if isinstance(data, dict) else data
    return [e for e in rows or [] if isinstance(e, dict) and e.get("title")]


def save(path: str, events: list) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"events": events}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _key(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def _clean(text: str, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def find(events: list, title: str) -> dict:
    """The event `title` means: the exact title first, else a part only one event has."""
    want = _key(title)
    if not want:
        raise TimelineError("which event?")
    hits = [e for e in events if _key(e["title"]) == want] or [e for e in events if want in _key(e["title"])]
    if not hits:
        raise TimelineError(f"no event like {title!r} on the timeline")
    if len(hits) > 1:
        raise TimelineError(f"{title!r} matches several events: "
                            + " | ".join(f"{e['title']} ({e.get('date') or 'no date'})" for e in hits))
    return hits[0]


# ─── The campaign's calendar ──────────────────────────────────────────────────

def calendar_of(campaign: str) -> tuple:
    """(scripts/calendar.py as a module, the campaign's calendar) — (None, {}) when it keeps none.

    The script is loaded under another name: as `calendar` it would stand in
    for the standard library's in whatever imports this module.
    """
    import importlib.util
    import media
    if not (media.find_campaign(campaign) / "calendar.json").is_file():
        return None, {}
    try:
        spec = importlib.util.spec_from_file_location("dnd_campaign_calendar", os.path.join(_SCRIPTS, "calendar.py"))
        lib = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lib)
        cal = lib._load(campaign)
    except Exception:
        return None, {}
    return (lib, cal) if cal.get("months") or cal.get("month_length") else (None, {})


def time_key(value: str) -> str:
    v = (value or "").strip().lower()
    if not v:
        return ""
    v = TIME_ALIASES.get(v, v)
    if v not in TIMES:
        raise TimelineError(f"time of day is one of {', '.join(TIMES)} — not {value!r}")
    return v


def when(date: str = "", at: str = "", lib=None, cal: dict | None = None) -> dict:
    """The date fields of an event: today when no date is given, as the calendar writes it otherwise."""
    at = time_key(at)
    date = _clean(date, DATE)
    if lib is None or not cal:
        return {"date": date, "day": None, "month": None, "year": None, "abs": None, "time": at}
    if not date:
        day, month, year = cal.get("day", 1), cal.get("month", 1), cal.get("year", 1)
        at = at or lib._tod_key(cal.get("hour", 8))
    else:
        try:
            day, month, year = lib._parse_date(cal, date)
        except lib.CalendarError as e:
            raise TimelineError(str(e))
        year = cal.get("year", 1) if year is None else year
    return {"date": lib._date_text(cal, day, month, year), "day": day, "month": month, "year": year,
            "abs": lib._abs_day(cal, day, month, year), "time": at}


# ─── Operations ───────────────────────────────────────────────────────────────

def _session(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise TimelineError(f"session is a number (got {value!r})")
    if not 0 < n < 10000:
        raise TimelineError("session is a number from 1 up")
    return n


def add(events: list, title: str, stamp: dict, note: str = "", session=None, secret: bool = False) -> dict:
    title = _clean(title, TITLE)
    if not title:
        raise TimelineError("an event needs a title")
    if any(_key(e["title"]) == _key(title) and e.get("date") == stamp.get("date") for e in events):
        raise TimelineError(f"{title!r} is on the timeline already, on that date")
    if len(events) >= MAX_EVENTS:
        raise TimelineError(f"the timeline holds {MAX_EVENTS} events already")
    event = {"id": secrets.token_hex(4), "title": title, "note": _clean(note, NOTE), **stamp,
             "session": _session(session), "secret": bool(secret),
             "seq": max((int(e.get("seq") or 0) for e in events), default=0) + 1, "ts": time.time()}
    events.append(event)
    return event


def ordered(events: list) -> list:
    """Oldest first: by the world's date, then the time of day, then the order they were added."""
    return sorted(events, key=lambda e: (e.get("abs") is None, e.get("abs") or 0,
                                         TIMES.get(e.get("time") or "", -1), int(e.get("seq") or 0)))


def public_view(events: list) -> list:
    """What the players' screens get: what is not secret, oldest first."""
    return [{"id": e.get("id", ""), "title": e["title"], "note": e.get("note", ""), "date": e.get("date", ""),
             "abs": e.get("abs"), "time": e.get("time", "") if e.get("time") in TIMES else "",
             "session": e.get("session")} for e in ordered(events) if not e.get("secret")]


def describe(event: dict) -> str:
    head = " · ".join(x for x in (event.get("date") or "no date", event.get("time", ""),
                                  f"session {event['session']}" if event.get("session") else "") if x)
    return f"[{head}] {event['title']}" + ("  (secret)" if event.get("secret") else "") \
        + (f"\n    {event['note']}" if event.get("note") else "")


def _announce(event: dict | None = None) -> None:
    """Tell the display the timeline changed. Silent when it is not running."""
    try:
        import send
        send._post(f"{send.BASE_URL}/timeline", json.dumps({"event": event} if event else {}).encode("utf-8"),
                   send._read_token())
    except Exception:
        pass   # the display is off: the timeline is saved and will be there next time


# ─── Command line ─────────────────────────────────────────────────────────────

def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="What has happened in the campaign, by the world's dates.")
    ap.add_argument("--campaign", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add", help="something happened")
    p.add_argument("title")
    p.add_argument("--date", default="", help="as the calendar writes it (default: today)")
    p.add_argument("--time", default="", help=", ".join(TIMES))
    p.add_argument("--note", default="")
    p.add_argument("--session", default=None, help="the session it was played in")
    p.add_argument("--secret", action="store_true", help="only the DM sees it, until revealed")
    p = sub.add_parser("set", help="change an event")
    p.add_argument("name")
    p.add_argument("--title", default=None)
    p.add_argument("--date", default=None)
    p.add_argument("--time", default=None, help='"" takes the time of day away')
    p.add_argument("--note", default=None)
    p.add_argument("--session", default=None)
    for cmd in ("reveal", "hide", "remove"):
        sub.add_parser(cmd).add_argument("name")
    sub.add_parser("list")
    args = ap.parse_args(argv)

    try:
        import media
        camp = _campaign(args.campaign)
        path = data_path(camp)
        events = load(path)
        event = None
        if args.cmd == "list":
            print("\n".join(describe(e) for e in ordered(events)) or "nothing on the timeline yet")
            return 0
        if args.cmd == "add":
            lib, cal = calendar_of(camp)
            new = add(events, args.title, when(args.date, args.time, lib, cal), args.note, args.session, args.secret)
            event = None if new["secret"] else {"title": new["title"]}
            print("+ " + describe(new))
        elif args.cmd == "set":
            target = find(events, args.name)
            if all(v is None for v in (args.title, args.date, args.time, args.note, args.session)):
                raise TimelineError("set what? give --title, --date, --time, --note or --session")
            if args.title is not None:
                title = _clean(args.title, TITLE)
                if not title:
                    raise TimelineError("an event needs a title")
                target["title"] = title
            if args.date is not None or args.time is not None:
                lib, cal = calendar_of(camp)
                if args.date is None:
                    target["time"] = time_key(args.time)
                elif not args.date and lib is not None:
                    raise TimelineError("which date? (to mean today, remove the event and add it again)")
                else:
                    target.update(when(args.date, args.time if args.time is not None else target.get("time", ""),
                                       lib, cal))
            if args.note is not None:
                target["note"] = _clean(args.note, NOTE)
            if args.session is not None:
                target["session"] = _session(args.session)
            print(describe(target))
        elif args.cmd in ("reveal", "hide"):
            target = find(events, args.name)
            target["secret"] = args.cmd == "hide"
            event = {"title": target["title"]} if args.cmd == "reveal" else None
            print(describe(target))
        elif args.cmd == "remove":
            target = find(events, args.name)
            events.remove(target)
            print("- " + target["title"])
        save(path, events)
        if camp == media.active_campaign():
            _announce(event)
    except TimelineError as e:
        print(f"timeline: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
