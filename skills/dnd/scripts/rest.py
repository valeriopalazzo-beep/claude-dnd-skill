#!/usr/bin/env python3
"""rest.py — short and long rests, taken from the players' own screens.

The DM opens a rest; each resting player's screen shows a Rest panel. On a
short rest they spend Hit Dice one at a time (the display rolls the die and
adds the CON modifier) and say when they are done; on a long rest one tap
brings HP to full, gives Hit Dice back and empties the spell slots' Used
column. Everything is written to the character sheet and to the display at
once, and the DM is told with [[REST: …]] lines (check_input.py).

    rest.py open short                      # every character at the table rests
    rest.py open long --only "Lyra, Hernio" # only these two
    rest.py status                          # who has finished
    rest.py close                           # end it, whoever is still resting

The same can be done without the display, character by character:

    rest.py -c <campaign> show "Ajeje"
    rest.py -c <campaign> hd "Ajeje" [--count 2]   # spend Hit Dice (rolled here)
    rest.py -c <campaign> short "Ajeje"            # the short rest is over for Ajeje
    rest.py -c <campaign> long "Ajeje"             # a long rest

What is read from the sheet: "**HP:** 9 / 17 | **Temp HP:** 0", "**Hit Dice:**
2d10 (remaining: 1)", "**Death Saves:** Successes: 0 | Failures: 0", the CON
column of the Ability Scores table and the Spell Slots table. When the
display holds a live count of HP or Hit Dice (the DM pushes them during
play), that count wins over the sheet's. A short rest gives a Warlock's
pact slots back; a long rest gives back half the Hit Dice (2014) or all of
them (2024). Other features that recharge are the DM's to restore. Anything
that cannot be done exits non-zero and leaves the sheet as it was.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import secrets
import sys

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import paths  # noqa: E402  (also forces UTF-8 output on Windows)
import inventory  # noqa: E402  (finding, reading and writing a sheet)
import spellbook  # noqa: E402  (the spell slots)

_HP = re.compile(r"(\*\*(?:HP|PF):\*\*\s*)(\d+)(\s*/\s*)(\d+)")
_TEMP = re.compile(r"(\*\*(?:Temp HP|PF temporanei):\*\*\s*)(\d+)", re.I)
_HD = re.compile(r"(\*\*(?:Hit Dice|Dadi Vita):\*\*\s*)(\d+)\s*d\s*(\d+)(?:\s*\(\s*(?:remaining|rimasti)\s*:\s*(\d+)\s*\))?", re.I)
_DEATH = re.compile(r"(\*\*Death Saves:\*\*\s*Successes:\s*)(\d+)(\s*\|\s*Failures:\s*)(\d+)", re.I)
_SCORES_HEAD = re.compile(r"^##\s+Ability Scores\b.*$", re.M)
_CLASS = re.compile(r"\*\*(?:Class|Classe):\*\*\s*([^|\n]*)", re.I)
KINDS = ("short", "long")


class RestError(Exception):
    """A request that cannot be applied; the sheet is left as it was."""


# ─── Reading the sheet ────────────────────────────────────────────────────────

def read_hp(text: str) -> dict | None:
    m = _HP.search(text)
    if not m:
        return None
    t = _TEMP.search(text)
    return {"current": int(m.group(2)), "max": int(m.group(4)), "temp": int(t.group(2)) if t else 0}


def read_hit_dice(text: str) -> dict | None:
    m = _HD.search(text)
    if not m:
        return None
    count, die = int(m.group(2)), int(m.group(3))
    left = int(m.group(4)) if m.group(4) is not None else count
    return {"die": die, "max": count, "remaining": max(0, min(left, count))}


def con_mod(text: str) -> int:
    """The CON modifier from the Ability Scores table ('16 (+3)'); 0 when it cannot be read."""
    m = _SCORES_HEAD.search(text)
    if not m:
        return 0
    rows = [ln for ln in text[m.end():].splitlines() if ln.strip().startswith("|")][:3]
    if len(rows) < 3:
        return 0
    head = [c.strip().upper() for c in rows[0].strip().strip("|").split("|")]
    vals = [c.strip() for c in rows[2].strip().strip("|").split("|")]
    for name in ("CON", "COS"):
        if name in head and head.index(name) < len(vals):
            cell = vals[head.index(name)]
            mod = re.search(r"\(\s*([+-−]?\d+)\s*\)", cell)
            if mod:
                return int(mod.group(1).replace("−", "-"))
            score = re.match(r"\d+", cell)
            if score:
                return (int(score.group()) - 10) // 2
    return 0


def is_warlock(text: str) -> bool:
    m = _CLASS.search(text)
    return bool(m and re.search(r"warlock", m.group(1), re.I))


def state(text: str, live: dict | None = None) -> dict:
    """HP, Hit Dice and CON of a sheet, with the display's live HP / Hit Dice when given."""
    hp = read_hp(text)
    hd = read_hit_dice(text)
    live = live or {}
    lhp = live.get("hp") if isinstance(live.get("hp"), dict) else None
    if hp and lhp and isinstance(lhp.get("current"), int):
        hp = {"current": lhp["current"], "max": int(lhp.get("max") or hp["max"]), "temp": int(lhp.get("temp") or 0)}
    lhd = live.get("hit_dice") if isinstance(live.get("hit_dice"), dict) else None
    if hd and lhd and isinstance(lhd.get("remaining"), int):
        hd = dict(hd, remaining=max(0, min(lhd["remaining"], hd["max"])))
    return {"hp": hp, "hit_dice": hd, "con": con_mod(text), "warlock": is_warlock(text)}


# ─── Writing it ───────────────────────────────────────────────────────────────

def write_hp(text: str, current: int, temp: int | None = None) -> str:
    text = _HP.sub(lambda m: f"{m.group(1)}{current}{m.group(3)}{m.group(4)}", text, count=1)
    if temp is not None:
        text = _TEMP.sub(lambda m: f"{m.group(1)}{temp}", text, count=1)
    return text


def write_hit_dice(text: str, remaining: int) -> str:
    return _HD.sub(lambda m: f"{m.group(1)}{m.group(2)}d{m.group(3)} (remaining: {remaining})", text, count=1)


def clear_death_saves(text: str) -> str:
    return _DEATH.sub(lambda m: f"{m.group(1)}0{m.group(3)}0", text, count=1)


# ─── The rests ────────────────────────────────────────────────────────────────

def spend_hit_die(text: str, live: dict | None = None, roll: int | None = None) -> tuple:
    """Spend one Hit Die. Returns (the new sheet, what happened).

    What happened: {"die", "roll", "con", "gained", "hp_before", "hp", "max", "left", "hd_max"}.
    `roll` is the die's result when it was rolled elsewhere; otherwise it is rolled here.
    """
    st = state(text, live)
    hp, hd = st["hp"], st["hit_dice"]
    if not hp:
        raise RestError('the sheet has no "HP: current / max" line')
    if not hd:
        raise RestError('the sheet has no "Hit Dice: NdM (remaining: N)" line')
    if hd["remaining"] < 1:
        raise RestError("no Hit Dice left")
    if hp["current"] >= hp["max"]:
        raise RestError("HP are already full")
    if roll is None:
        roll = secrets.randbelow(hd["die"]) + 1
    gained = max(0, roll + st["con"])
    new_hp = min(hp["max"], hp["current"] + gained)
    left = hd["remaining"] - 1
    text = write_hit_dice(write_hp(text, new_hp), left)
    return text, {"die": hd["die"], "roll": roll, "con": st["con"], "gained": new_hp - hp["current"],
                  "hp_before": hp["current"], "hp": new_hp, "max": hp["max"], "temp": hp["temp"],
                  "left": left, "hd_max": hd["max"]}


def finish_short(text: str) -> tuple:
    """The end of a short rest: a Warlock's pact slots come back. Returns (sheet, what happened)."""
    slots_back = 0
    if is_warlock(text):
        text, slots_back = spellbook.restore(text)
    return text, {"slots_back": slots_back}


def long_rest(text: str, ruleset: str = "2014", live: dict | None = None) -> tuple:
    """A long rest: full HP, no temporary HP, Hit Dice back, every slot back, death saves cleared."""
    st = state(text, live)
    hp, hd = st["hp"], st["hit_dice"]
    if not hp:
        raise RestError('the sheet has no "HP: current / max" line')
    done = {"hp_before": hp["current"], "hp": hp["max"], "max": hp["max"], "hd_back": 0, "left": 0, "hd_max": 0}
    text = write_hp(text, hp["max"], 0)
    if hd:
        regain = hd["max"] if str(ruleset) == "2024" else max(1, hd["max"] // 2)
        left = min(hd["max"], hd["remaining"] + regain)
        done.update(hd_back=left - hd["remaining"], left=left, hd_max=hd["max"])
        text = write_hit_dice(text, left)
    text, done["slots_back"] = spellbook.restore(text)
    return clear_death_saves(text), done


# ─── What the players did, for the DM ─────────────────────────────────────────

def _notes_file() -> str:
    return str(paths.runtime_dir() / "rest_notes.json")


def notes_add(note: dict) -> None:
    path = _notes_file()
    try:
        with open(path, encoding="utf-8") as f:
            notes = json.load(f)
        if not isinstance(notes, list):
            notes = []
    except (OSError, ValueError):
        notes = []
    notes.append(note)
    try:
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(notes, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def format_note(note: dict) -> str:
    who, kind = note.get("character", "?"), note.get("kind")
    if kind == "hd":
        return (f"[[REST: {who} spent a Hit Die: d{note.get('die')} rolled {note.get('roll')} "
                f"{int(note.get('con') or 0):+d} CON = {note.get('gained')} HP — now {note.get('hp')}/{note.get('max')} HP, "
                f"{note.get('left')} of {note.get('hd_max')} Hit Dice left. Already on the sheet and the display.]]")
    if kind == "short":
        extra = (f" {note['slots_back']} pact slot(s) back." if note.get("slots_back") else "")
        return (f"[[REST: {who} finished the short rest.{extra} Second Wind is ready again on the display; "
                "restore by hand any other feature of theirs that comes back on a short rest.]]")
    if kind == "long":
        return (f"[[REST: {who} finished the long rest: HP {note.get('hp')}/{note.get('max')}, "
                f"{note.get('hd_back')} Hit Dice back ({note.get('left')} of {note.get('hd_max')}), "
                f"{note.get('slots_back')} spell slot(s) back, death saves cleared — already on the sheet and the display. "
                "Restore by hand the features that come back on a long rest.]]")
    if kind == "over":
        rest = "short" if note.get("rest") == "short" else "long"
        return (f"[[REST: the {rest} rest is over for everybody. Move the clock: calendar.py rest {rest}"
                + (", and tracker.py clear --all" if rest == "long" else "") + ".]]")
    return ""


def over_pending() -> bool:
    """True when a rest has ended for everybody and the DM has not been told yet."""
    try:
        with open(_notes_file(), encoding="utf-8") as f:
            notes = json.load(f)
    except (OSError, ValueError):
        return False
    return isinstance(notes, list) and any(isinstance(n, dict) and n.get("kind") == "over" for n in notes)


def drain_notes() -> list:
    """Rest news not yet reported, as lines for the DM; clears the list."""
    path = _notes_file()
    try:
        with open(path, encoding="utf-8") as f:
            notes = json.load(f)
        os.remove(path)
    except (OSError, ValueError):
        return []
    return [line for line in (format_note(n) for n in notes if isinstance(n, dict)) if line]


# ─── The display ──────────────────────────────────────────────────────────────

def _live(name: str) -> dict:
    """The display's own copy of a character (HP, Hit Dice), {} when it has none."""
    try:
        with open(paths.runtime_dir() / "stats.json", encoding="utf-8") as f:
            players = json.load(f).get("players") or []
    except (OSError, ValueError, AttributeError):
        return {}
    want = inventory._key(name)
    return next((p for p in players if isinstance(p, dict) and inventory._key(p.get("name", "")) == want), {})


def _request(path: str, body: dict | None = None):
    """(status, parsed JSON or None) from the display; status 0 when it is not running."""
    import ssl
    import urllib.error
    import urllib.request
    sys.path.insert(0, str(paths.display_dir()))
    import send
    headers = {"Content-Type": "application/json"}
    token = send._read_token()
    if token:
        headers["X-DND-Token"] = token
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(send.BASE_URL + path, data=data, headers=headers,
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=5, context=send._SSL_CTX) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except (OSError, ssl.SSLError):
        return 0, None
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, None


def _push(name: str, text: str, second_wind: bool = False) -> None:
    """Put the sheet's HP, Hit Dice and slots on the display."""
    hp, hd = read_hp(text), read_hit_dice(text)
    player: dict = {"name": name}
    if hp:
        player["hp"] = {"current": hp["current"], "max": hp["max"], "temp": hp["temp"]}
    if hd:
        player["hit_dice"] = {"remaining": hd["remaining"], "max": hd["max"]}
    slots = {str(k): v for k, v in spellbook.read_slots(text).items()}
    if slots:
        player["spell_slots"] = slots
    if second_wind and "second_wind" in _live(name):
        player["second_wind"] = True
    inventory._push(["--json", json.dumps({"players": [player]}, ensure_ascii=False)])


def _wild_shape_back(campaign: str, name: str) -> None:
    """A short or long rest brings the druid's Wild Shape uses back (display/shapes.py)."""
    try:
        if str(paths.display_dir()) not in sys.path:
            sys.path.insert(0, str(paths.display_dir()))
        import shapes
        if shapes.rested(campaign, name):
            print(f"  {name}: Wild Shape uses back")
    except Exception:
        pass


def _print_status(rest: dict | None) -> None:
    if not rest:
        print("No rest is open.")
        return
    done = set(rest.get("done") or [])
    print(f"{'Short' if rest.get('kind') == 'short' else 'Long'} rest")
    for name in rest.get("characters") or []:
        print(f"  {'✓' if name in done else '·'} {name}")


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="Short and long rests, on the sheet and on the players' screens.")
    ap.add_argument("--campaign", "-c", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("open", help="open a rest on the resting players' screens")
    p.add_argument("kind", choices=KINDS)
    p.add_argument("--only", default="", help='the characters who rest, "A, B" (default: everybody at the table)')
    sub.add_parser("close", help="end the rest on the screens")
    sub.add_parser("status", help="who is resting and who has finished")
    sub.add_parser("notes", help="print what the players did since last time, then clear it")
    sub.add_parser("show").add_argument("character")
    p = sub.add_parser("hd", help="spend Hit Dice (rolled here)")
    p.add_argument("character")
    p.add_argument("--count", type=int, default=1)
    sub.add_parser("short", help="the short rest is over for this character").add_argument("character")
    sub.add_parser("long", help="a long rest for this character").add_argument("character")
    args = ap.parse_args(argv)

    if args.cmd == "notes":
        for line in drain_notes():
            print(line)
        return 0
    if args.cmd in ("open", "close", "status"):
        if args.cmd == "status":
            status, body = _request("/rest")
        elif args.cmd == "close":
            status, body = _request("/rest", {"close": True})
        else:
            only = [n.strip() for n in args.only.split(",") if n.strip()]
            status, body = _request("/rest", {"kind": args.kind, "characters": only})
        if status != 200 or not isinstance(body, dict):
            why = body.get("error") if isinstance(body, dict) else ""
            print("rest: " + ("the display is not running" if status == 0 else f"HTTP {status}{': ' + why if why else ''}")
                  + ". Nothing was changed.", file=sys.stderr)
            return 1
        _print_status(body.get("rest"))
        if args.cmd == "open":
            print("→ on the resting players' screens; their choices reach you as [[REST: …]] lines")
        return 0

    try:
        camp = args.campaign or inventory.active_campaign()
        path = spellbook.find_sheet(args.character, camp)
        name = inventory.display_name(path)
        text = inventory._read(path)
        on_display = not args.campaign or args.campaign == inventory.active_campaign()
        live = _live(name) if on_display else {}
        if args.cmd == "show":
            st = state(text, live)
            hp, hd = st["hp"], st["hit_dice"]
            print(name)
            print(f"  HP {hp['current']}/{hp['max']}" + (f" (+{hp['temp']} temp)" if hp and hp["temp"] else "") if hp else "  HP: not on the sheet")
            print(f"  Hit Dice {hd['remaining']} of {hd['max']} (d{hd['die']} {st['con']:+d})" if hd else "  Hit Dice: not on the sheet")
            return 0
        if args.cmd == "hd":
            if not 1 <= args.count <= 20:
                raise RestError("--count is between 1 and 20")
            for i in range(args.count):
                try:
                    text, done = spend_hit_die(text, live)
                except RestError:
                    if i == 0:
                        raise
                    break
                live = {}       # from now on the sheet is the live count
                print(f"{name}: d{done['die']} = {done['roll']} {done['con']:+d} → +{done['gained']} HP, "
                      f"{done['hp']}/{done['max']} HP, {done['left']} of {done['hd_max']} Hit Dice left")
        elif args.cmd == "short":
            text, done = finish_short(text)
            print(f"{name}: short rest over" + (f", {done['slots_back']} pact slot(s) back" if done["slots_back"] else ""))
        else:
            ruleset = paths.campaign_ruleset(camp) if camp else "2014"
            text, done = long_rest(text, ruleset, live)
            print(f"{name}: long rest — HP {done['hp']}/{done['max']}, {done['hd_back']} Hit Dice back "
                  f"({done['left']} of {done['hd_max']}), {done['slots_back']} slot(s) back")
        inventory._write(path, text)
        if on_display:
            _push(name, text, second_wind=args.cmd in ("short", "long"))
        if args.cmd in ("short", "long"):
            _wild_shape_back(camp, name)
    except (RestError, spellbook.SpellError, inventory.InventoryError) as e:
        print(f"rest: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
