#!/usr/bin/env python3
"""rumors.py — what is said in the taverns, true, false or half true.

The DM writes the rumors from the campaign's state (the secrets still to come
out, the quests, the factions, the threats); the characters pick them up by
listening in a tavern — a Charisma check to find the people who talk (PHB 2014
p. 179) — or carousing in their downtime. The players see what they heard,
never whether it is true.

    rumors.py seeds                                   # what the campaign offers to write rumors from
    rumors.py add "Dicono che l'Isola compri prigionieri" --truth true --about "segreto: anime" --where "Ferrovalle"
    rumors.py add "La Gran Maestra è morta da anni" --truth false
    rumors.py add "Il Rosso è cugino di un barcaiolo" --truth half
    rumors.py hear --who "Berta" [--where "Locanda del Pistone"] [--dc 10]   # her Charisma check, on the phone
    rumors.py hear --who "Berta" --total 14                                  # rolled already
    rumors.py tell "Berta" "isola"                    # one given by hand (by id or part of the text)
    rumors.py list                                    # all of them, true or not, who heard them
    rumors.py forget "isola"

A check under the DC brings nothing; at the DC one rumor, 5 over two, 10 over
three (a reading of the PHB's "find the right person to talk to", not a number
from the book). The rumors nobody has heard come first; a rumor with --where
is said only there. Everything lives in <campaign>/rumors.json; every screen
has a Rumors panel with what the party has heard. downtime.py close gives a
carousing character one rumor without a check.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import secrets
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

TRUTHS = ("true", "false", "half")
TRUTH_ALIASES = {"vera": "true", "vero": "true", "falsa": "false", "falso": "false", "mezza": "half",
                 "mezza verità": "half", "mezzo": "half", "partial": "half", "half-true": "half"}
MAX_RUMORS = 120
LIMIT = 300


class RumorError(Exception):
    """A request that cannot be applied; nothing was changed."""


# ─── Data ─────────────────────────────────────────────────────────────────────

def _campaign(campaign: str = "") -> str:
    import media
    camp = media.safe_campaign(campaign) if campaign else media.active_campaign()
    if not camp:
        raise RumorError("no active campaign — pass --campaign or load one first")
    return camp


def data_path(campaign: str = "") -> str:
    import media
    return str(media.find_campaign(_campaign(campaign)) / "rumors.json")


def load(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        return []
    except ValueError as e:
        raise RumorError(f"rumors.json is not valid JSON ({e})")
    rows = data.get("rumors") if isinstance(data, dict) else data
    return [r for r in rows or [] if isinstance(r, dict) and r.get("text")]


def save(path: str, rumors: list) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"rumors": rumors}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _key(text: str) -> str:
    return "".join(ch for ch in (text or "").casefold() if ch.isalnum())


def _clean(text, limit: int) -> str:
    return " ".join(re.sub(r"[`\\$<>]", "", str(text or "")).split())[:limit]


def truth_of(word: str) -> str:
    w = (word or "").strip().lower()
    w = TRUTH_ALIASES.get(w, w)
    if w not in TRUTHS:
        raise RumorError(f"--truth is true, false or half — not {word!r}")
    return w


def find(rumors: list, what: str) -> dict:
    want = (what or "").strip()
    if not want:
        raise RumorError("which rumor? its id or a part of its text")
    by_id = [r for r in rumors if r.get("id") == want]
    hits = by_id or [r for r in rumors if _key(want) in _key(r["text"])]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise RumorError(f"no rumor matching {what!r}")
    raise RumorError(f"{what!r} matches {len(hits)} rumors — use the id (rumors.py list)")


def add(rumors: list, text: str, truth: str, about: str = "", where: str = "") -> dict:
    text = _clean(text, LIMIT)
    if not text:
        raise RumorError("a rumor needs words")
    if any(_key(r["text"]) == _key(text) for r in rumors):
        raise RumorError("that rumor is already there")
    if len(rumors) >= MAX_RUMORS:
        raise RumorError(f"{MAX_RUMORS} rumors already — forget the stale ones")
    r = {"id": secrets.token_hex(3), "text": text, "truth": truth_of(truth), "about": _clean(about, 120),
         "where": _clean(where, 80), "heard": [], "ts": time.time()}
    rumors.append(r)
    return r


def said_here(r: dict, where: str) -> bool:
    """A rumor tied to a place is said only there; one with no place anywhere."""
    if not r.get("where"):
        return True
    return bool(where) and (_key(r["where"]) in _key(where) or _key(where) in _key(r["where"]))


def how_many(total: int, dc: int) -> int:
    """Under the DC nothing; at it one rumor, 5 over two, 10 over three."""
    if total < dc:
        return 0
    return 1 + (total - dc >= 5) + (total - dc >= 10)


def pick(rumors: list, who: str, n: int, where: str = "", rng=None) -> list:
    """Up to n rumors this character has not heard: the ones nobody has heard first."""
    rng = rng or random.Random()
    pool = [r for r in rumors if said_here(r, where) and not any(_key(h["who"]) == _key(who) for h in r.get("heard") or [])]
    fresh = [r for r in pool if not r.get("heard")]
    old = [r for r in pool if r.get("heard")]
    rng.shuffle(fresh)
    rng.shuffle(old)
    return (fresh + old)[:max(0, n)]


def hear(r: dict, who: str, where: str = "", when: str = "") -> None:
    r.setdefault("heard", []).append({"who": _clean(who, 50), "where": _clean(where, 80), "when": _clean(when, 60),
                                      "ts": time.time()})


def public_view(rumors: list) -> list:
    """What the players' screens get: the rumors the party heard — never whether they are true."""
    rows = []
    for r in rumors:
        if not r.get("heard"):
            continue
        first = r["heard"][0]
        rows.append({"id": r["id"], "text": r["text"], "by": [h["who"] for h in r["heard"]],
                     "where": first.get("where", ""), "when": first.get("when", ""), "ts": r["heard"][-1].get("ts", 0)})
    return sorted(rows, key=lambda x: -x["ts"])


def dm_view(rumors: list) -> dict:
    return {"unheard": sum(1 for r in rumors if not r.get("heard")),
            "heard": [{"text": r["text"], "truth": r["truth"], "by": [h["who"] for h in r["heard"]]}
                      for r in rumors if r.get("heard")][-12:]}


def describe(r: dict) -> str:
    tag = {"true": "TRUE", "false": "FALSE", "half": "HALF TRUE"}[r["truth"]]
    heard = ", ".join(h["who"] for h in r.get("heard") or [])
    return (f"[{r['id']}] {tag}: {r['text']}" + (f"  (about {r['about']})" if r.get("about") else "")
            + (f"  — only in {r['where']}" if r.get("where") else "") + (f"  — heard by {heard}" if heard else "  — not heard yet"))


# ─── What to write them from ──────────────────────────────────────────────────

def seeds(campaign: str) -> list:
    """Lines for the DM: the campaign's secrets still to come out, open quests, factions, threats, rumoured places."""
    import media
    folder = media.find_campaign(campaign)
    out = []
    try:
        prep = (folder / "prep.md").read_text(encoding="utf-8")
        m = re.search(r"^##\s*\d*\.?\s*Secrets[^\n]*\n(.*?)(?=^##\s|\Z)", prep, re.S | re.M | re.I)
        for line in (m.group(1) if m else "").splitlines():
            s = re.match(r"^\s*[-*]\s*\[ \]\s*(.+)$", line)
            if s:
                out.append(f"secret (not out yet): {s.group(1).strip()}")
    except OSError:
        pass
    def _json(name):
        try:
            return json.loads((folder / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    q = _json("quests.json")
    for quest in (q.get("quests") if isinstance(q, dict) else q) or []:
        if isinstance(quest, dict) and quest.get("status", "active") in ("active", "threat"):
            out.append(f"quest ({quest.get('status', 'active')}): {quest.get('name', '?')}"
                       + (f" — {quest['summary']}" if quest.get("summary") else ""))
    f = _json("factions.json")
    for fac in (f.get("factions") if isinstance(f, dict) else f) or []:
        if isinstance(fac, dict) and fac.get("name"):
            out.append(f"faction: {fac['name']}" + (f" ({fac.get('standing')})" if fac.get("standing") else "")
                       + (" [secret]" if fac.get("secret") else ""))
    c = _json("clocks.json")
    for clock in (c.get("clocks") if isinstance(c, dict) else c) or []:
        if isinstance(clock, dict) and clock.get("name"):
            out.append(f"clock{' [secret]' if clock.get('secret') else ''}: {clock['name']} "
                       f"{clock.get('filled', 0)}/{clock.get('segments', '?')}")
    rg = _json("region.json")
    for place in (rg or {}).get("places") or []:
        if isinstance(place, dict) and place.get("status") == "rumor":
            out.append(f"place only heard of: {place.get('name')}" + (f" — {place['note']}" if place.get("note") else ""))
    p = _json("people.json")
    for person in (p.get("people") if isinstance(p, dict) else p) or []:
        if isinstance(person, dict) and person.get("name"):
            out.append(f"person met: {person['name']}" + (f" — {person['role']}" if person.get("role") else ""))
    return out


# ─── The table ────────────────────────────────────────────────────────────────

def _today(campaign: str) -> str:
    try:
        import timeline
        lib, cal = timeline.calendar_of(campaign)
        return lib._date_text(cal) if lib else ""
    except Exception:
        return ""


def _here(campaign: str) -> str:
    try:
        import region_map
        path = region_map.data_path(campaign)
        return region_map.load(path).get("party", "") if os.path.isfile(path) else ""
    except Exception:
        return ""


def _story(line: str) -> None:
    try:
        subprocess.run([sys.executable, os.path.join(_HERE, "send.py"), "--dice"], input=line, text=True,
                       encoding="utf-8", capture_output=True, timeout=15, env=dict(os.environ, PYTHONUTF8="1"))
    except Exception:
        pass


def _announce(event: dict | None = None) -> None:
    try:
        import send
        send._post(f"{send.BASE_URL}/rumors", json.dumps({"event": event} if event else {}).encode("utf-8"),
                   send._read_token())
    except Exception:
        pass


def give(campaign: str, who: str, n: int, where: str = "", rng=None) -> list:
    """`who` hears up to n rumors (said where they are); saved. The rumors heard."""
    path = data_path(campaign)
    rumors = load(path)
    got = pick(rumors, who, n, where, rng)
    when = _today(campaign)
    for r in got:
        hear(r, who, where, when)
    if got:
        save(path, rumors)
    return got


def main(argv: list | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    ap = argparse.ArgumentParser(description="What is said in the taverns, true, false or half true.")
    ap.add_argument("--campaign", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seeds")
    p = sub.add_parser("add")
    p.add_argument("text")
    p.add_argument("--truth", required=True, help="true, false or half")
    p.add_argument("--about", default="", help="what it is about, for you")
    p.add_argument("--where", default="", help="said only in this place")
    p = sub.add_parser("hear", help="a character listens to the talk: a Charisma check")
    p.add_argument("--who", required=True)
    p.add_argument("--where", default="", help="where they listen (default: where the region map has the party)")
    p.add_argument("--dc", type=int, default=10)
    p.add_argument("--total", type=int, help="the check is already rolled: its total")
    p.add_argument("--timeout", type=int, default=180)
    p = sub.add_parser("tell", help="give a character one rumor by hand")
    p.add_argument("who")
    p.add_argument("rumor")
    p.add_argument("--where", default="")
    sub.add_parser("list")
    sub.add_parser("forget").add_argument("rumor")
    args = ap.parse_args(argv)
    try:
        import media
        camp = _campaign(args.campaign)
        path = data_path(camp)
        rumors = load(path)
        event = None
        import paths
        it = paths.campaign_language(camp) == "it"
        if args.cmd == "seeds":
            lines = seeds(camp)
            print("\n".join(lines) or "nothing in the campaign to write rumors from yet")
            print(f"\n{sum(1 for r in rumors if not r.get('heard'))} rumor(s) not heard yet. Write some true, some false, "
                  "some half true — `rumors.py add`.")
            return 0
        if args.cmd == "list":
            print("\n".join(describe(r) for r in rumors) or "no rumors — rumors.py seeds, then add")
            return 0
        if args.cmd == "add":
            r = add(rumors, args.text, args.truth, args.about, args.where)
            print("+ " + describe(r))
        elif args.cmd == "forget":
            r = find(rumors, args.rumor)
            rumors.remove(r)
            print("- " + r["text"])
            event = {"what": "forget"}
        elif args.cmd == "tell":
            import inventory
            r = find(rumors, args.rumor)
            try:
                who = inventory.display_name(__import__("spellbook").find_sheet(args.who, camp))
            except Exception:
                who = _clean(args.who, 50)
            hear(r, who, args.where or _here(camp), _today(camp))
            print(f"{who} hears: {describe(r)}")
            event = {"what": "heard", "who": who, "n": 1}
        elif args.cmd == "hear":
            import travel                       # its _ask: one roll on a phone
            import group_check
            import inventory
            import spellbook
            try:
                sheet = spellbook.find_sheet(args.who, camp)
            except Exception as e:
                raise RumorError(str(e))
            who = inventory.display_name(sheet)
            mod = group_check._ability_mod(inventory._read(sheet), "CHA")
            where = args.where or _here(camp)
            if not any(said_here(r, where) for r in rumors):
                raise RumorError("no rumors to hear" + (f" in {where}" if where else "") + " — rumors.py seeds, then add some")
            total = args.total
            if total is None:
                label = "Raccogliere voci (Carisma)" if it else "Gather rumors (Charisma)"
                total = travel._ask(who, mod, "normal", label, args.dc, args.timeout)
                if total is None:
                    raise RumorError(f"{who} did not roll in time — nothing changed; ask again or pass --total")
            n = how_many(total, args.dc)
            print(f"{who}: Charisma {total} vs DC {args.dc} — " + (f"{n} rumor(s)" if n else "nobody has much to say"))
            got = pick(rumors, who, n, where)
            for r in got:
                hear(r, who, where, _today(camp))
                print("  " + describe(r))
            if n and not got:
                print("  (they have heard everything that is said here — write new rumors)")
            if n:
                _story("🗣 " + (f"{who} raccoglie voci{(' a ' + where) if where else ''}: {len(got)} da raccontare"
                               if it else f"{who} gathers rumors{(' in ' + where) if where else ''}: {len(got)} to tell"))
            event = {"what": "heard", "who": who, "n": len(got)} if got else None
            print("Tell them in the story (true, false or half true as listed); the Rumors panel has them now."
                  if got else "")
        save(path, rumors)
        if camp == media.active_campaign():
            _announce(event)
    except RumorError as e:
        print(f"rumors: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    except Exception as e:
        if type(e).__name__ == "TravelError":
            print(f"rumors: {e}. Nothing was changed.", file=sys.stderr)
            return 1
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
