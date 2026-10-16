#!/usr/bin/env python3
"""group_check.py — a group check on the players' phones (PHB 2014 p. 175).

When the party does something together — sneak past the camp, climb the
cliff, cross the bog — everyone makes the check, and if at least half of
them succeed, the whole group succeeds. Each phone gets its own roll with its
own bonus from the sheet (the skill, or the ability for a plain check);
disadvantage comes by itself where the sheet says so (exhaustion; armour that
hampers Stealth). The result goes into the story and is printed for the DM.

    group_check.py Stealth --dc 13
    group_check.py Furtività --dc 13 --group Collina          # one group of a split party
    group_check.py Athletics --dc 15 --who "Lyra, Hernio"
    group_check.py Survival --dc 12 --advantage advantage      # a guide, good weather…
    group_check.py Strength --dc 10                            # a plain ability check

A player may Help another instead of rolling for themselves (the Help action):
the helped character rolls with advantage and the helper's request is dropped.
Skills and abilities in English or Italian.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import ssl
import subprocess
import sys
import time
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from runtime_paths import rt  # noqa: E402,F401  (also puts scripts/ on the path)

SKILLS = {
    "acrobatics": ("Acrobatics", "DEX"), "animal handling": ("Animal Handling", "WIS"), "arcana": ("Arcana", "INT"),
    "athletics": ("Athletics", "STR"), "deception": ("Deception", "CHA"), "history": ("History", "INT"),
    "insight": ("Insight", "WIS"), "intimidation": ("Intimidation", "CHA"), "investigation": ("Investigation", "INT"),
    "medicine": ("Medicine", "WIS"), "nature": ("Nature", "INT"), "perception": ("Perception", "WIS"),
    "performance": ("Performance", "CHA"), "persuasion": ("Persuasion", "CHA"), "religion": ("Religion", "INT"),
    "sleight of hand": ("Sleight of Hand", "DEX"), "stealth": ("Stealth", "DEX"), "survival": ("Survival", "WIS"),
}
SKILL_IT = {"acrobazia": "acrobatics", "addestrare animali": "animal handling", "arcano": "arcana", "atletica": "athletics",
            "inganno": "deception", "storia": "history", "intuizione": "insight", "intimidire": "intimidation",
            "indagare": "investigation", "medicina": "medicine", "natura": "nature", "percezione": "perception",
            "intrattenere": "performance", "persuasione": "persuasion", "religione": "religion",
            "rapidita di mano": "sleight of hand", "rapidità di mano": "sleight of hand", "furtivita": "stealth",
            "furtività": "stealth", "sopravvivenza": "survival"}
ABILITIES = {"strength": "STR", "forza": "STR", "dexterity": "DEX", "destrezza": "DEX", "constitution": "CON",
             "costituzione": "CON", "intelligence": "INT", "intelligenza": "INT", "wisdom": "WIS", "saggezza": "WIS",
             "charisma": "CHA", "carisma": "CHA"}
ABILITY_IT = {"STR": "FOR", "DEX": "DES", "CON": "COS", "INT": "INT", "WIS": "SAG", "CHA": "CAR"}
LABELS_IT = {"Acrobatics": "Acrobazia", "Animal Handling": "Addestrare Animali", "Arcana": "Arcano", "Athletics": "Atletica",
             "Deception": "Inganno", "History": "Storia", "Insight": "Intuizione", "Intimidation": "Intimidire",
             "Investigation": "Indagare", "Medicine": "Medicina", "Nature": "Natura", "Perception": "Percezione",
             "Performance": "Intrattenere", "Persuasion": "Persuasione", "Religion": "Religione",
             "Sleight of Hand": "Rapidità di Mano", "Stealth": "Furtività", "Survival": "Sopravvivenza",
             "STR": "Forza", "DEX": "Destrezza", "CON": "Costituzione", "INT": "Intelligenza", "WIS": "Saggezza", "CHA": "Carisma"}


class GroupCheckError(Exception):
    """A check that cannot be asked."""


def what_check(word: str) -> tuple:
    """'Furtività' → ('Stealth', 'DEX'); 'Forza' → ('', 'STR')."""
    w = " ".join((word or "").strip().lower().split())
    w = SKILL_IT.get(w, w)
    if w in SKILLS:
        return SKILLS[w]
    if w in ABILITIES:
        return "", ABILITIES[w]
    if w.upper() in ABILITY_IT.values() or w.upper() in ABILITY_IT:
        return "", next((k for k, v in ABILITY_IT.items() if v == w.upper()), w.upper())
    raise GroupCheckError(f"{word!r} is neither a skill nor an ability")


def _ability_mod(text: str, ability: str) -> int:
    m = re.search(r"^\|\s*(?:STR|FOR)\s*\|.*$", text, re.M | re.I)
    if not m:
        return 0
    rows = [ln for ln in text[m.start():].splitlines() if ln.strip().startswith("|")][:3]
    if len(rows) < 3:
        return 0
    head = [c.strip().upper() for c in rows[0].strip().strip("|").split("|")]
    vals = [c.strip() for c in rows[2].strip().strip("|").split("|")]
    for name in (ability, ABILITY_IT.get(ability, ability)):
        if name in head and head.index(name) < len(vals):
            mod = re.search(r"\(\s*([+-−]?\d+)\s*\)", vals[head.index(name)])
            if mod:
                return int(mod.group(1).replace("−", "-"))
            score = re.match(r"\d+", vals[head.index(name)])
            if score:
                return (int(score.group()) - 10) // 2
    return 0


def bonus(text: str, skill: str, ability: str) -> int:
    """The sheet's bonus: the skill row when there is one, else the ability modifier."""
    if skill:
        names = [skill, LABELS_IT.get(skill, skill)]
        for name in names:
            m = re.search(r"^\|\s*" + re.escape(name) + r"\s*\|[^\n]*?([+-−]\d+)", text, re.M | re.I)
            if m:
                return int(m.group(1).replace("−", "-"))
    return _ability_mod(text, ability)


def disadvantage(text: str, skill: str) -> str:
    """Why the sheet gives disadvantage on this check ('' when it does not)."""
    cond = re.search(r"Conditions:\**\s*([^\n]*)", text, re.I)
    if cond and re.search(r"(?:exhaustion|indebolimento)\s*[1-6]", cond.group(1), re.I):
        return "exhaustion"
    if skill == "Stealth":
        ac = re.search(r"\*\*AC:\*\*[^\n]*", text)
        if ac and re.search(r"(?:svantaggio[^\n]*furtivit|stealth[^\n]*disadvantage|disadvantage[^\n]*stealth)", ac.group(0), re.I):
            return "armour"
    return ""


def outcome(totals: dict, dc: int) -> dict:
    """{name: total or None} → who succeeded, and whether the group did (half or more of those who rolled)."""
    rolled = {n: t for n, t in totals.items() if t is not None}
    ok = [n for n, t in rolled.items() if t >= dc]
    need = math.ceil(len(rolled) / 2) if rolled else 1
    return {"ok": ok, "failed": [n for n in rolled if n not in ok], "missing": [n for n, t in totals.items() if t is None],
            "need": need, "success": bool(rolled) and len(ok) >= need}


def story_line(label: str, dc: int, out: dict, lang: str) -> str:
    n, tot = len(out["ok"]), len(out["ok"]) + len(out["failed"])
    if lang == "it":
        return (f"🎲 Prova di gruppo — {label} (CD {dc}): {n} su {tot} " + ("ci riescono" if n != 1 else "ci riesce")
                + (" — RIUSCITA" if out["success"] else " — FALLITA"))
    return f"🎲 Group check — {label} (DC {dc}): {n} of {tot} succeed — " + ("SUCCESS" if out["success"] else "FAILURE")


# ─── Talking to the display ───────────────────────────────────────────────────

def _request(path: str, body: dict | None = None):
    import urllib.error
    import urllib.request
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


def _party() -> list:
    try:
        with open(rt("stats.json"), encoding="utf-8") as f:
            return [str(p.get("name")) for p in (json.load(f).get("players") or []) if p.get("name")]
    except (OSError, ValueError, AttributeError):
        return []


def main(argv: list | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    ap = argparse.ArgumentParser(description="A group check on the players' phones.")
    ap.add_argument("check", help="a skill or an ability, English or Italian")
    ap.add_argument("--dc", type=int, required=True)
    ap.add_argument("--who", default="", help='these characters, "A, B" (default: everyone at the table)')
    ap.add_argument("--group", default="", help="one group of a split party (split.py)")
    ap.add_argument("--advantage", choices=["normal", "advantage", "disadvantage"], default="normal",
                    help="for everyone (the sheet may still add disadvantage to someone)")
    ap.add_argument("--campaign", default="")
    ap.add_argument("--timeout", type=int, default=180, help="seconds to wait for the phones")
    args = ap.parse_args(argv)

    try:
        skill, ability = what_check(args.check)
        import inventory
        import media
        import paths
        camp = media.safe_campaign(args.campaign) if args.campaign else (media.active_campaign() or "")
        lang = paths.campaign_language(camp) if camp else "en"
        if args.group:
            import split
            who, _label = split.audience(split._campaign(args.campaign), args.group, "")
        else:
            who = [n.strip() for n in args.who.split(",") if n.strip()] or _party()
        if not who:
            raise GroupCheckError("nobody at the table (use --who)")
        if not 1 <= args.dc <= 40:
            raise GroupCheckError("--dc between 1 and 40")
        name_of = (LABELS_IT.get(skill or ability, skill or ability) if lang == "it" else (skill or ability))
        label = (f"Prova di gruppo: {name_of}" if lang == "it" else f"Group check: {name_of}")
        asks = []
        for name in who:
            try:
                path = inventory.find_sheet(name, camp)
                text = inventory._read(path)
                real = inventory.display_name(path)
            except inventory.InventoryError:
                raise GroupCheckError(f"no sheet for {name}")
            why = disadvantage(text, skill)
            adv = args.advantage
            if why:
                adv = "normal" if adv == "advantage" else "disadvantage"
            asks.append({"name": real, "mod": bonus(text, skill, ability), "adv": adv, "why": why})
    except (GroupCheckError, Exception) as e:
        if not isinstance(e, GroupCheckError):
            e = GroupCheckError(str(e))
        print(f"group_check: {e}. Nothing was asked.", file=sys.stderr)
        return 1

    ids = {}
    for a in asks:
        status, body = _request("/dice-request", {"characters": [a["name"]], "spec": "1d20", "modifier": a["mod"],
                                                  "advantage": a["adv"], "label": label, "dc": args.dc})
        if status != 200 or not isinstance(body, dict):
            print("group_check: " + ("the display is not running" if status == 0 else f"HTTP {status}")
                  + " — nothing was asked.", file=sys.stderr)
            return 1
        ids[a["name"]] = body["request_id"]
    print(f"{label}, DC {args.dc} — asked: " + ", ".join(
        f"{a['name']} {a['mod']:+d}" + (f" ({a['adv']}: {a['why']})" if a["why"] else (f" ({a['adv']})" if a["adv"] != "normal" else ""))
        for a in asks), flush=True)

    totals = {n: None for n in ids}
    detail = {}
    deadline = time.time() + max(5, args.timeout)
    while time.time() < deadline and any(t is None for t in totals.values()):
        for name, rid in ids.items():
            if totals[name] is not None:
                continue
            status, st = _request(f"/dice-request/{rid}")
            if status == 200 and isinstance(st, dict) and st.get("complete"):
                res = next((r for r in st.get("results") or [] if r.get("character", "").lower() == name.lower()), None)
                if res:
                    totals[name], detail[name] = int(res["total"]), res
                elif st.get("helping") and name.lower() in [h.lower() for h in st["helping"]]:
                    totals[name] = "help"
                else:
                    totals[name] = "gone"
        time.sleep(0.5)
    helpers = [n for n, t in totals.items() if t == "help"]
    clean = {n: (t if isinstance(t, int) else None) for n, t in totals.items() if t != "help"}
    out = outcome(clean, args.dc)
    for name in clean:
        r = detail.get(name)
        if r is None:
            print(f"  {name}: no roll" + (" (time is up)" if totals[name] is None else ""))
            continue
        nat = (r.get("kept") or [None])[0]
        print(f"  {name}: {r['total']} (d20 {nat}{int(r.get('modifier') or 0):+d}"
              + (f", {r['advantage']}" if r.get("advantage") not in (None, "normal") else "")
              + (f", helped by {r['helped_by']}" if r.get("helped_by") else "") + ") — "
              + ("success" if r["total"] >= args.dc else "failure"))
    for name in helpers:
        print(f"  {name}: helped instead of rolling")
    line = story_line(LABELS_IT.get(skill or ability, skill or ability) if lang == "it" else (skill or ability),
                      args.dc, out, lang)
    print(f"→ {len(out['ok'])} of {len(out['ok']) + len(out['failed'])} succeed (half or more needed: {out['need']}): "
          + ("the GROUP SUCCEEDS" if out["success"] else "the GROUP FAILS")
          + (f". No roll from: {', '.join(out['missing'])}" if out["missing"] else ""))
    try:
        subprocess.run([sys.executable, os.path.join(_HERE, "send.py"), "--dice"], input=line, text=True,
                       encoding="utf-8", capture_output=True, timeout=15, env=dict(os.environ, PYTHONUTF8="1"))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
