#!/usr/bin/env python3
"""factions.py — what each faction thinks of the party, as a score that moves.

The Factions panel used to show a word pushed by hand (`--factions`), lost with
the display's memory and with no sense of how close the party is to the next
step. Here a faction has a reputation from -10 to +10, kept with the campaign
in <campaign>/factions.json; the word follows from the number:

    -10 … -6  Hostile      -5 … -2  Suspicious      -1 … +1  Neutral
     +2 … +5  Friendly     +6 … +10 Allied

    factions.py add "Casa Valtrenna" --score -3 --leader "Ser Bertrand" --base "Il campo" \
        --goal "Riprendersi la corona" --attitude "Vi ha cacciati dal campo"
    factions.py add "La Confraternita della Cenere" --standing hostile --secret   # by word: its middle score
    factions.py up "Valtrenna" 2 --reason "Avete riportato il frammento"          # down for the other way
    factions.py to "Valtrenna" 0 --reason "Patto davanti al prete"
    factions.py set "Valtrenna" --attitude "…" --goal ""                          # "" empties a field; --name renames
    factions.py member "Valtrenna" "Ser Bertrand, il capofamiglia"                # someone known to belong
    factions.py learn "Valtrenna" "Hanno un frammento della corona"               # something the party found out
    factions.py drop "Valtrenna" --member "Bertrand"                              # or --known
    factions.py reveal "Confraternita"      # the players see it from now on (hide puts it back)
    factions.py remove "Valtrenna"
    factions.py list                        # all of them, the secret ones too, with their history
    factions.py show "Valtrenna"
    factions.py push                        # send the list to the display again
    factions.py import                      # take in the factions the display holds (pushed by hand before)

Every move is remembered with its reason; the panel shows the last few, so the
players know why the Watch looks at them that way. Write only what the party
knows: every player reads this panel. A faction can be named by any part of its
name that means one faction only. Anything that does not match exits non-zero
and changes nothing.
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

MIN_SCORE, MAX_SCORE = -10, 10
# (lowest score, word) from the top down; the first band the score reaches is its word.
BANDS = ((6, "Allied"), (2, "Friendly"), (-1, "Neutral"), (-5, "Suspicious"), (MIN_SCORE, "Hostile"))
# A word given instead of a score lands in the middle of its band.
WORD_SCORE = {"allied": 8, "friendly": 3, "neutral": 0, "unfriendly": -3, "suspicious": -3, "hostile": -8}
WORD_ALIASES = {"alleata": "allied", "alleato": "allied", "alleati": "allied", "amichevole": "friendly",
                "amici": "friendly", "neutrale": "neutral", "sfavorevole": "unfriendly", "diffidente": "suspicious",
                "sospettosa": "suspicious", "sospettosi": "suspicious", "ostile": "hostile", "ostili": "hostile",
                "nemica": "hostile", "nemici": "hostile"}
FIELDS = ("summary", "leader", "base", "goal", "attitude")
LIMITS = {"name": 80, "summary": 600, "leader": 120, "base": 120, "goal": 240, "attitude": 240}
LINE = 240
MAX_LINES = 30          # members or known facts in one faction
HISTORY_KEPT = 40       # moves remembered per faction
HISTORY_SHOWN = 5       # moves the panel shows


class FactionError(Exception):
    """A request that cannot be applied; the factions are left as they were."""


# ─── Data ─────────────────────────────────────────────────────────────────────

def _campaign(campaign: str = "") -> str:
    import media
    camp = media.safe_campaign(campaign) if campaign else media.active_campaign()
    if not camp:
        raise FactionError("no active campaign — pass --campaign or load one first")
    return camp


def data_path(campaign: str = "") -> str:
    import media
    return str(media.find_campaign(_campaign(campaign)) / "factions.json")


def load(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        return []
    except ValueError as e:
        raise FactionError(f"factions.json is not valid JSON ({e})")
    rows = data.get("factions") if isinstance(data, dict) else data
    return [r for r in rows or [] if isinstance(r, dict) and r.get("name")]


def save(path: str, factions: list) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"factions": factions}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _key(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def _clean(text: str, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def find(factions: list, name: str) -> dict:
    """The faction `name` means: exact name first, else a unique part of it."""
    want = _key(name)
    if not want:
        raise FactionError("which faction?")
    hits = [f for f in factions if _key(f["name"]) == want] or [f for f in factions if want in _key(f["name"])]
    if not hits:
        known = ", ".join(f["name"] for f in factions) or "none yet"
        raise FactionError(f"no faction called {name!r} (factions: {known})")
    if len(hits) > 1:
        raise FactionError(f"{name!r} matches several factions: " + ", ".join(f["name"] for f in hits))
    return hits[0]


def standing(score: int) -> str:
    """The word for a score: Hostile, Suspicious, Neutral, Friendly or Allied."""
    return next(word for low, word in BANDS if score >= low)


def score_of(word: str) -> int:
    """The score a standing word stands for (the middle of its band)."""
    w = (word or "").strip().lower()
    w = WORD_ALIASES.get(w, w)
    if w not in WORD_SCORE:
        raise FactionError(f"a standing is one of hostile, suspicious, neutral, friendly, allied — not {word!r}")
    return WORD_SCORE[w]


def _score(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise FactionError(f"a reputation is a whole number from {MIN_SCORE} to {MAX_SCORE}, not {value!r}")
    if not MIN_SCORE <= n <= MAX_SCORE:
        raise FactionError(f"a reputation goes from {MIN_SCORE} to {MAX_SCORE}; {n} is out of range")
    return n


# ─── Operations ───────────────────────────────────────────────────────────────

def add(factions: list, name: str, score=None, word: str = "", secret: bool = False,
        members: list | None = None, known: list | None = None, **fields) -> dict:
    name = _clean(name, LIMITS["name"])
    if not name:
        raise FactionError("a faction needs a name")
    if any(_key(f["name"]) == _key(name) for f in factions):
        raise FactionError(f"there is already a faction called {name!r} (move it, or remove it first)")
    if score is not None and word:
        raise FactionError("give --score or --standing, not both")
    value = _score(score) if score is not None else score_of(word) if word else 0
    faction = {"id": secrets.token_hex(4), "name": name, "score": value, "secret": bool(secret),
               "members": [], "known": [], "history": [], "ts": time.time()}
    for field in FIELDS:
        text = _clean(fields.get(field) or "", LIMITS[field])
        if text:
            faction[field] = text
    for line in members or []:
        add_line(faction, "members", line)
    for line in known or []:
        add_line(faction, "known", line)
    factions.append(faction)
    return faction


def change(factions: list, faction: dict, name: str | None = None, **fields) -> list:
    """Set the given fields (None = leave, "" = empty). Returns what changed."""
    changed = []
    if name is not None:
        new = _clean(name, LIMITS["name"])
        if not new:
            raise FactionError("a faction needs a name")
        if any(f is not faction and _key(f["name"]) == _key(new) for f in factions):
            raise FactionError(f"there is already a faction called {new!r}")
        if new != faction["name"]:
            faction["name"] = new
            changed.append("name")
    for field in FIELDS:
        value = fields.get(field)
        if value is None:
            continue
        text = _clean(value, LIMITS[field])
        if text != faction.get(field, ""):
            if text:
                faction[field] = text
            else:
                faction.pop(field, None)
            changed.append(field)
    return changed


def move(faction: dict, by: int = 0, to=None, reason: str = "") -> int:
    """Raise or lower the reputation; returns the change actually made (kept within -10…+10)."""
    before = int(faction.get("score", 0))
    after = _score(to) if to is not None else max(MIN_SCORE, min(MAX_SCORE, before + int(by)))
    faction["score"] = after
    delta = after - before
    if delta:
        faction.setdefault("history", []).append(
            {"delta": delta, "score": after, "reason": _clean(reason, LINE), "ts": time.time()})
        del faction["history"][:-HISTORY_KEPT]
    return delta


def add_line(faction: dict, which: str, text: str) -> bool:
    """One more member or known fact; False when it was there already."""
    text = _clean(text, LINE)
    if not text:
        raise FactionError("write something: the line is empty")
    lines = faction.setdefault(which, [])
    if any(_key(t) == _key(text) for t in lines):
        return False
    if len(lines) >= MAX_LINES:
        raise FactionError(f"{faction['name']} already has {MAX_LINES} {which}: drop one first")
    lines.append(text)
    return True


def drop(faction: dict, member: str = "", known: str = "") -> str:
    if bool(member) == bool(known):
        raise FactionError("drop what? give --member or --known (one of them)")
    which, part = ("members", member) if member else ("known", known)
    lines = faction.get(which) or []
    want = _key(part)
    hits = [i for i, t in enumerate(lines) if want in _key(t)]
    if not hits:
        raise FactionError(f"{faction['name']} has no {which} line with {part!r}")
    if len(hits) > 1:
        raise FactionError(f"{part!r} matches several lines: " + " | ".join(lines[i] for i in hits))
    return lines.pop(hits[0])


def take_in(factions: list, rows: list) -> list:
    """Add the display's factions this file does not have yet. Returns their names."""
    new = []
    for row in rows or []:
        if not isinstance(row, dict) or not _clean(row.get("name", ""), LIMITS["name"]):
            continue
        if any(_key(f["name"]) == _key(row["name"]) for f in factions):
            continue
        try:
            value = score_of(str(row.get("standing") or "neutral"))
        except FactionError:
            value = 0
        faction = add(factions, row["name"], score=value, **{f: row.get(f, "") for f in FIELDS})
        for which in ("members", "known"):
            for line in row.get(which) or []:
                if isinstance(line, str) and _clean(line, LINE):
                    add_line(faction, which, line)
        new.append(faction["name"])
    return new


def public_view(factions: list) -> list:
    """What the Factions panel gets: every faction that is not secret, as the panel draws it."""
    out = []
    for f in factions:
        if f.get("secret"):
            continue
        score = int(f.get("score", 0))
        row = {"name": f["name"], "standing": standing(score), "score": score}
        row.update({k: f[k] for k in FIELDS if f.get(k)})
        for which in ("members", "known"):
            if f.get(which):
                row[which] = list(f[which])
        moves = [h for h in f.get("history") or [] if isinstance(h, dict) and h.get("delta")]
        if moves:
            row["history"] = [{"delta": h["delta"], "reason": h.get("reason", "")} for h in moves[-HISTORY_SHOWN:]][::-1]
        out.append(row)
    return out


def _signed(n: int) -> str:
    return f"+{n}" if n > 0 else str(n)


def describe(faction: dict, history: bool = False) -> str:
    score = int(faction.get("score", 0))
    tags = ["secret"] if faction.get("secret") else []
    lines = [f"{faction['name']}: {_signed(score)} {standing(score)}" + (f"  [{', '.join(tags)}]" if tags else "")]
    lines += [f"  {f}: {faction[f]}" for f in FIELDS if faction.get(f)]
    lines += [f"  member: {m}" for m in faction.get("members") or []]
    lines += [f"  · {k}" for k in faction.get("known") or []]
    if history:
        lines += [f"  {_signed(h['delta'])} → {_signed(h['score'])}" + (f"  {h['reason']}" if h.get("reason") else "")
                  for h in faction.get("history") or []]
    return "\n".join(lines)


def event(faction: dict, delta: int = 0, new: bool = False, reason: str = "") -> dict | None:
    """What the screens are told about a change; None for a secret faction."""
    if faction.get("secret"):
        return None
    score = int(faction.get("score", 0))
    return {"name": faction["name"], "delta": delta, "score": score, "standing": standing(score),
            "new": new, "reason": _clean(reason, LINE)}


def _display_factions() -> list:
    """The factions the display is showing, from its saved stats (works with the display off)."""
    from runtime_paths import rt
    try:
        with open(rt("stats.json"), encoding="utf-8") as f:
            rows = json.load(f).get("factions")
    except (OSError, ValueError, AttributeError):
        return []
    return rows if isinstance(rows, list) else []


def _announce(evt: dict | None = None) -> None:
    """Tell the display the factions changed. Silent when it is not running."""
    try:
        import send
        send._post(f"{send.BASE_URL}/factions", json.dumps({"event": evt} if evt else {}).encode("utf-8"),
                   send._read_token())
    except Exception:
        pass   # the display is off: the factions are saved and will be there at the next load


# ─── Command line ─────────────────────────────────────────────────────────────

def _add_fields(parser, default) -> None:
    for field in FIELDS:
        parser.add_argument("--" + field, default=default)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="What each faction thinks of the party, kept with the campaign.")
    ap.add_argument("--campaign", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add", help="a faction the party has to reckon with")
    p.add_argument("name")
    p.add_argument("--score", type=int, default=None, help=f"{MIN_SCORE} to {MAX_SCORE} (default 0)")
    p.add_argument("--standing", default="", help="hostile, suspicious, neutral, friendly or allied")
    p.add_argument("--secret", action="store_true", help="the players do not see it until `reveal`")
    p.add_argument("--member", action="append", default=[])
    p.add_argument("--known", action="append", default=[])
    _add_fields(p, "")
    for cmd in ("up", "down"):
        p = sub.add_parser(cmd, help=("better" if cmd == "up" else "worse") + " reputation")
        p.add_argument("name")
        p.add_argument("n", type=int, nargs="?", default=1)
        p.add_argument("--reason", default="")
    p = sub.add_parser("to", help="set the reputation")
    p.add_argument("name")
    p.add_argument("score")
    p.add_argument("--reason", default="")
    p = sub.add_parser("set", help="change a faction's fields")
    p.add_argument("name")
    p.add_argument("--name", dest="new_name", default=None)
    _add_fields(p, None)
    for cmd, what in (("member", "someone known to belong"), ("learn", "something the party found out")):
        p = sub.add_parser(cmd, help=what)
        p.add_argument("name")
        p.add_argument("text")
    p = sub.add_parser("drop", help="take a member or a known fact away")
    p.add_argument("name")
    p.add_argument("--member", default="")
    p.add_argument("--known", default="")
    for cmd in ("reveal", "hide", "remove", "show"):
        sub.add_parser(cmd).add_argument("name")
    for cmd in ("list", "push", "import"):
        sub.add_parser(cmd)
    args = ap.parse_args(argv)

    try:
        import media
        camp = _campaign(args.campaign)
        path = data_path(camp)
        factions = load(path)
        evt = None
        if args.cmd == "list":
            print("\n".join(describe(f, history=True) for f in factions) or "no factions yet")
            return 0
        if args.cmd == "show":
            print(describe(find(factions, args.name), history=True))
            return 0
        if args.cmd in ("push", "import") and camp != media.active_campaign():
            raise FactionError(f"{camp} is not the campaign on the display")
        if args.cmd == "push":
            _announce()
            print(f"{len(public_view(factions))} faction(s) sent to the display")
            return 0
        if args.cmd == "import":
            new = take_in(factions, _display_factions())
            print("\n".join("+ " + n for n in new) or "nothing to take in: every faction on the display is here already")
        elif args.cmd == "add":
            faction = add(factions, args.name, args.score, args.standing, args.secret, args.member, args.known,
                          **{f: getattr(args, f) for f in FIELDS})
            evt = event(faction, new=True)
            print("+ " + describe(faction))
        elif args.cmd in ("up", "down", "to"):
            faction = find(factions, args.name)
            if args.cmd == "to":
                delta = move(faction, to=args.score, reason=args.reason)
            else:
                if args.n < 1:
                    raise FactionError("by how much? (a positive number)")
                delta = move(faction, by=args.n if args.cmd == "up" else -args.n, reason=args.reason)
            evt = event(faction, delta, reason=args.reason) if delta else None
            before = int(faction["score"]) - delta
            print(describe(faction))
            if not delta:
                print(f"  (it was already at {_signed(faction['score'])} — nothing moved)")
            elif standing(before) != standing(faction["score"]):
                print(f"  ★ {standing(before)} → {standing(faction['score'])}: let the faction act on it.")
        elif args.cmd == "set":
            faction = find(factions, args.name)
            if not change(factions, faction, args.new_name, **{f: getattr(args, f) for f in FIELDS}):
                raise FactionError("set what? give --name or one of --" + ", --".join(FIELDS) + " (with a new value)")
            print(describe(faction))
        elif args.cmd in ("member", "learn"):
            faction = find(factions, args.name)
            fresh = add_line(faction, "members" if args.cmd == "member" else "known", args.text)
            print(describe(faction) + ("" if fresh else "\n  (nothing new — it was there already)"))
        elif args.cmd == "drop":
            print("- " + drop(find(factions, args.name), args.member, args.known))
        elif args.cmd in ("reveal", "hide"):
            faction = find(factions, args.name)
            faction["secret"] = args.cmd == "hide"
            evt = event(faction, new=True)
            print(describe(faction))
        elif args.cmd == "remove":
            faction = find(factions, args.name)
            factions.remove(faction)
            print("- " + faction["name"])
        save(path, factions)
        if camp == media.active_campaign():
            _announce(evt)
    except FactionError as e:
        print(f"factions: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
