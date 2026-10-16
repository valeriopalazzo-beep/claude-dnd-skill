#!/usr/bin/env python3
"""trap.py — traps: noticed in passing, searched for, disarmed, sprung.

The DM sets a trap where the party will go (a sample trap from the SRD, or one
built from its numbers); the script keeps it in <campaign>/traps.json and does
the rules (SRD 5.1 "Traps", DMG 2014 pp. 120-123):

  • passing by — each PC's passive Wisdom (Perception), read from the sheet,
    against the DC to spot it; whoever notices can be told in a secret note;
  • looking for it — Wisdom (Perception) to spot, Intelligence (Investigation)
    to work it out, Dexterity with thieves' tools to disarm (Intelligence
    (Arcana) for a magic trap): the roll is asked on that player's phone with
    the modifier from their sheet and the DC, and the result is applied —
    found, disarmed, or (for a trap whose failure springs it) sprung;
  • springing it — attack rolls against AC, saving throws asked on the PCs'
    phones (foes' saves rolled here), damage rolled and halved on a save,
    conditions named: one line per target for the DM to apply.

    trap.py presets                                         # the SRD sample traps
    trap.py rate --level 3                                  # DC, attack and damage by severity (SRD table)
    trap.py add "Dardi del corridoio" --preset poison-darts --where "Corridoio est" --at 7,3
    trap.py add "Lastra instabile" --spot 12 --disarm 13 --fail-springs \\
                --save DEX --dc 13 --damage "2d10 bludgeoning" --half --trigger "lastra a pressione"
    trap.py list [--all]
    trap.py show "dardi"
    trap.py pass "dardi" [--who "Lyra, Hernio"] [--tell "Noti dei forellini nel muro."]
    trap.py check "dardi" search Lyra                       # also: investigate, disarm [--arcana] [--no-tools]
    trap.py check "dardi" disarm Lyra --roll 17             # the roll was made already
    trap.py check "dardi" search Lyra --here                # roll it here (roll_mode auto)
    trap.py spring "dardi" --targets "Lyra, Hernio, g" [--ac g:15] [--save-bonus g:+2] [--here]
    trap.py set "dardi" --status armed|noticed|found|disarmed|sprung
    trap.py remove "dardi"

A trap is named by any part of its name that means one trap only. Damage is
written as dice and a type, joined with +: "1 piercing + 2d10 poison". The
DM's screen (/dm-screen → Traps) lists them; players never see a trap, and a
search asked on a phone names neither the trap nor its DC. Anything that does
not match exits non-zero and changes nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import secrets
import subprocess
import sys
import time

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import paths  # noqa: E402  (also forces UTF-8 output on Windows)

STATUSES = ("armed", "noticed", "found", "disarmed", "sprung")
ABILITIES = ("STR", "DEX", "CON", "INT", "WIS", "CHA")
ABILITY_WORDS = {"str": "STR", "for": "STR", "forza": "STR", "strength": "STR", "dex": "DEX", "des": "DEX",
                 "destrezza": "DEX", "dexterity": "DEX", "con": "CON", "cos": "CON", "costituzione": "CON",
                 "constitution": "CON", "int": "INT", "intelligenza": "INT", "intelligence": "INT", "wis": "WIS",
                 "sag": "WIS", "saggezza": "WIS", "wisdom": "WIS", "cha": "CHA", "car": "CHA", "carisma": "CHA",
                 "charisma": "CHA"}
CHECKS = {"search": ("Perception", "spot"), "investigate": ("Investigation", "investigate"),
          "disarm": ("thieves' tools", "disarm"), "cerca": ("Perception", "spot"),
          "indaga": ("Investigation", "investigate"), "disattiva": ("thieves' tools", "disarm")}

# SRD 5.1 "Traps": Trap Save DCs and Attack Bonuses, Damage Severity by Level.
SEVERITY = {"setback": ((10, 11), (3, 5)), "dangerous": ((12, 15), (6, 8)), "deadly": ((16, 20), (9, 12))}
SEVERITY_WORDS = {"imprevisto": "setback", "battuta d'arresto": "setback", "pericoloso": "dangerous",
                  "pericolosa": "dangerous", "letale": "deadly", "mortale": "deadly"}
DAMAGE_BY_LEVEL = ((4, {"setback": "1d10", "dangerous": "2d10", "deadly": "4d10"}),
                   (10, {"setback": "2d10", "dangerous": "4d10", "deadly": "10d10"}),
                   (16, {"setback": "4d10", "dangerous": "10d10", "deadly": "18d10"}),
                   (20, {"setback": "10d10", "dangerous": "18d10", "deadly": "24d10"}))

# The SRD's sample traps, with their numbers. Falling damage: 1d6 per 10 feet (PHB p. 183).
PRESETS = {
    "collapsing-roof": {
        "it": "Crollo del soffitto", "trigger": "trip wire 3 inches off the ground", "spot": 10, "disarm": 15,
        "fail_springs": True, "tools_optional": True,
        "save": {"ability": "DEX", "dc": 15, "damage": "4d10 bludgeoning", "half": True},
        "note": "Anyone who sees the ceiling can tell it may collapse; the beams are only wedged in place (an action knocks "
                "one over and springs it). Afterwards the area is difficult terrain."},
    "falling-net": {
        "it": "Rete che cade", "trigger": "trip wire 3 inches off the ground", "spot": 10, "disarm": 15,
        "fail_springs": True, "tools_optional": True, "auto": {"condition": "restrained"},
        "save": {"ability": "STR", "dc": 10, "condition": "prone"},
        "note": "Covers a 10-foot square. An action and a DC 10 Strength check frees oneself or another within reach. "
                "Net AC 10, 20 HP; 5 slashing damage frees one 5-foot square."},
    "fire-breathing-statue": {
        "it": "Statua che sputa fuoco", "magic": True, "trigger": "hidden pressure plate (more than 20 lb)", "spot": 15,
        "dispel": 13, "save": {"ability": "DEX", "dc": 13, "damage": "4d10 fire", "half": True},
        "note": "30-foot cone of fire. Scorch marks show at the same DC; detect magic shows evocation on the statue. "
                "An iron spike under the plate stops it; dispel magic (DC 13) destroys it."},
    "simple-pit": {
        "it": "Fossa semplice", "trigger": "cloth over the hole", "spot": 10, "auto": {"damage": "1d6 bludgeoning"},
        "note": "10 feet deep: 1d6 falling damage per 10 feet (deeper pits, more dice)."},
    "hidden-pit": {
        "it": "Fossa nascosta", "trigger": "trapdoor cover like the floor", "spot": 15, "investigate": 15,
        "auto": {"damage": "1d6 bludgeoning"},
        "note": "Perception notices no foot traffic over it; Investigation confirms the cover. 10-20 feet deep "
                "(1d6 per 10 feet). An iron spike or arcane lock keeps the cover shut."},
    "locking-pit": {
        "it": "Fossa richiudibile", "trigger": "spring-loaded trapdoor", "spot": 15, "investigate": 15,
        "auto": {"damage": "1d6 bludgeoning"},
        "note": "The cover snaps shut on the victim: DC 20 Strength to pry it open, or smash it; from inside, DC 15 "
                "Dexterity with thieves' tools on the spring if it can be reached and seen."},
    "spiked-pit": {
        "it": "Fossa con spuntoni", "trigger": "trapdoor cover like the floor", "spot": 15, "investigate": 15,
        "auto": {"damage": "1d6 bludgeoning + 2d10 piercing"},
        "note": "Poisoned spikes: whoever takes the piercing damage also makes a DC 13 Constitution save, 4d10 poison "
                "(half on a success) — add --save CON --dc 13 --save-damage \"4d10 poison\" --half."},
    "poison-darts": {
        "it": "Dardi avvelenati", "trigger": "hidden pressure plate (more than 20 lb)", "spot": 15, "investigate": 15,
        "hit": {"attack": 8, "shots": 4, "damage": "1d4 piercing"},
        "save": {"ability": "CON", "dc": 15, "damage": "2d10 poison", "half": True, "after_hit": True},
        "note": "Four darts at random targets within 10 feet of the plate (vision does not matter). Spot = the holes; "
                "Investigation = the plate. A spike under the plate or the holes stuffed stops it."},
    "poison-needle": {
        "it": "Ago avvelenato", "trigger": "opening the lock without the key", "investigate": 20, "disarm": 15,
        "fail_springs": True, "auto": {"damage": "1 piercing + 2d10 poison"},
        "save": {"ability": "CON", "dc": 15, "condition": "poisoned (1 hour)"},
        "note": "Failing to pick the lock springs it."},
    "rolling-sphere": {
        "it": "Sfera rotolante", "trigger": "pressure plate (20 lb or more)", "spot": 15, "investigate": 15,
        "save": {"ability": "DEX", "dc": 15, "damage": "10d10 bludgeoning", "condition": "prone"},
        "note": "Everyone rolls initiative; the sphere has +8 and moves 60 feet in a straight line on its turn (difficult "
                "terrain to share its space). Each creature whose space it enters saves. An action and DC 20 Strength "
                "within 5 feet slows it by 15 feet. A spike under the plate stops it."},
    "sphere-of-annihilation": {
        "it": "Sfera annientatrice", "magic": True, "trigger": "the stone mouth", "investigate": 20, "dispel": 18,
        "note": "DC 20 Intelligence (Arcana) reveals the sphere of annihilation in the mouth; matter that enters is "
                "destroyed. A sympathy enchantment may pull creatures in (dispel magic DC 18 removes it)."},
}
PRESET_WORDS = {"crollo-del-soffitto": "collapsing-roof", "crollo-del-tetto": "collapsing-roof", "rete": "falling-net",
                "rete-che-cade": "falling-net", "statua": "fire-breathing-statue",
                "statua-che-sputa-fuoco": "fire-breathing-statue", "fossa": "simple-pit", "fossa-semplice": "simple-pit",
                "fossa-nascosta": "hidden-pit", "fossa-richiudibile": "locking-pit",
                "fossa-con-spuntoni": "spiked-pit", "dardi": "poison-darts", "dardi-avvelenati": "poison-darts",
                "ago": "poison-needle", "ago-avvelenato": "poison-needle", "sfera-rotolante": "rolling-sphere",
                "masso": "rolling-sphere", "sfera-annientatrice": "sphere-of-annihilation"}


class TrapError(Exception):
    """A request that cannot be applied; the traps are left as they were."""


# ─── Data ─────────────────────────────────────────────────────────────────────

def _active_campaign() -> str:
    try:
        return (paths.runtime_dir() / ".campaign").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _campaign(campaign: str = "") -> str:
    camp = campaign or _active_campaign()
    if not camp:
        raise TrapError("no active campaign — pass --campaign or load one first")
    try:
        paths.find_campaign(camp)
    except Exception:
        raise TrapError(f"no campaign called {camp!r}")
    return camp


def data_path(campaign: str) -> str:
    return str(paths.find_campaign(campaign) / "traps.json")


def load(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        return []
    except ValueError as e:
        raise TrapError(f"traps.json is not valid JSON ({e})")
    rows = data.get("traps") if isinstance(data, dict) else data
    return [t for t in rows or [] if isinstance(t, dict) and t.get("name")]


def save(path: str, traps: list) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"traps": traps}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _key(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def find(traps: list, name: str) -> dict:
    want = _key(name)
    if not want:
        raise TrapError("which trap?")
    hits = [t for t in traps if _key(t["name"]) == want] or [t for t in traps if want in _key(t["name"])]
    if not hits:
        raise TrapError(f"no trap called {name!r} (traps: {', '.join(t['name'] for t in traps) or 'none'})")
    if len(hits) > 1:
        raise TrapError(f"{name!r} matches several traps: " + ", ".join(t["name"] for t in hits))
    return hits[0]


# ─── Dice and damage ──────────────────────────────────────────────────────────

_TERM = re.compile(r"^\s*(?:(\d{1,2})d(\d{1,3})|(\d{1,3}))\s*([^\d+]*?)\s*$", re.I)


def parse_damage(text: str) -> list:
    """'1 piercing + 2d10 poison' → [(count, sides, flat, type)]; [] for nothing."""
    out = []
    for term in (text or "").split("+"):
        if not term.strip():
            continue
        m = _TERM.match(term)
        if not m:
            raise TrapError(f"damage {term.strip()!r} not understood (write e.g. 2d10 poison, or 1 piercing)")
        count, sides, flat, kind = m.groups()
        if count and not (1 <= int(count) <= 40 and 2 <= int(sides) <= 100):
            raise TrapError(f"damage {term.strip()!r}: 1 to 40 dice of 2 to 100 sides")
        out.append((int(count or 0), int(sides or 0), int(flat or 0), (kind or "").strip().lower()))
    return out


def roll_damage(text: str, rng=None) -> tuple:
    """(total, '2d10 poison: 4+7 = 11 · 1 piercing') for a damage string."""
    rng = rng or (lambda sides: secrets.randbelow(sides) + 1)
    total, parts = 0, []
    for count, sides, flat, kind in parse_damage(text):
        if count:
            dice = [rng(sides) for _ in range(count)]
            sub = sum(dice)
            parts.append(f"{count}d{sides}{' ' + kind if kind else ''}: {'+'.join(map(str, dice))} = {sub}")
        else:
            sub = flat
            parts.append(f"{flat}{' ' + kind if kind else ''}")
        total += sub
    return total, " · ".join(parts)


def dice_only(text: str) -> str:
    """The dice of a damage string without its flat numbers — what a critical hit rolls again."""
    return " + ".join(f"{c}d{sides} {kind}".strip() for c, sides, _flat, kind in parse_damage(text) if c)


def d20(rng=None) -> int:
    return (rng or (lambda sides: secrets.randbelow(sides) + 1))(20)


def rate(level: int) -> dict:
    """The SRD's numbers for a trap at that character level: severity → (DCs, attack bonuses, damage)."""
    if not 1 <= level <= 20:
        raise TrapError("the level is 1 to 20")
    damage = next(d for top, d in DAMAGE_BY_LEVEL if level <= top)
    return {sev: {"dc": dcs, "attack": att, "damage": damage[sev]} for sev, (dcs, att) in SEVERITY.items()}


# ─── Character sheets ─────────────────────────────────────────────────────────

def _signed(text: str) -> int:
    return int(text.replace("−", "-").replace("*", "").strip())


def _table_after(text: str, heading: str) -> dict:
    """The six-column ability table under a heading: {'STR': '17 (+3)', …}."""
    m = re.search(rf"^##\s+{heading}.*$", text, re.M | re.I)
    if not m:
        return {}
    rows = [l for l in text[m.end():].splitlines() if l.strip().startswith("|")][:3]
    if len(rows) < 3:
        return {}
    head = [c.strip().upper() for c in rows[0].strip().strip("|").split("|")]
    vals = [c.strip() for c in rows[2].strip().strip("|").split("|")]
    return {h: v for h, v in zip(head, vals) if h in ABILITIES}


def sheet_numbers(text: str) -> dict:
    """What the traps need from a sheet: passive Perception, AC, ability and save modifiers, skills, tools."""
    out = {"mods": {}, "saves": {}, "skills": {}}
    for ab, cell in _table_after(text, "Ability Scores").items():
        m = re.search(r"\(([+\-−]?\d+)\)", cell)
        if m:
            out["mods"][ab] = _signed(m.group(1))
    for ab, cell in _table_after(text, "Saving Throws").items():
        m = re.search(r"[+\-−]?\d+", cell)
        if m:
            out["saves"][ab] = _signed(m.group(0))
    for row in re.finditer(r"^\|\s*([A-Za-z' ]+?)\s*\|\s*(STR|DEX|CON|INT|WIS|CHA)\s*\|\s*([+\-−]?\d+)", text, re.M):
        out["skills"][row.group(1)] = _signed(row.group(3))
    m = re.search(r"proficiency bonus\s*:?\**\s*\+?(\d)", text, re.I)
    level = re.search(r"\*\*Level:\*\*\s*(\d+)", text)
    out["pb"] = int(m.group(1)) if m else 2 + (max(1, int(level.group(1))) - 1) // 4 if level else 2
    m = re.search(r"(?:Passive Perception|Percezione passiva)\s*:?\**\s*(\d+)", text, re.I)
    out["passive"] = int(m.group(1)) if m else 10 + out["skills"].get("Perception", out["mods"].get("WIS", 0))
    m = re.search(r"\*\*AC:\*\*\s*(\d+)", text)
    out["ac"] = int(m.group(1)) if m else None
    tools = re.search(r"(?:Tool proficiencies|Competenze negli strumenti|Strumenti)\s*:?\**\s*(.*)", text, re.I)
    out["tools"] = None
    if tools:
        t = re.search(r"(thieves'?\s*tools|arnesi\s+da\s+scasso)\s*(?:\(\s*([+\-−]?\d+))?", tools.group(1), re.I)
        if t:
            out["tools"] = _signed(t.group(2)) if t.group(2) else out["mods"].get("DEX", 0) + out["pb"]
    m = re.search(r"(?:spell save DC|CD dei tiri salvezza)\D{0,4}(\d{1,2})", text, re.I)
    out["spell_dc"] = int(m.group(1)) if m else None
    return out


def party(campaign: str) -> dict:
    """Name on the sheet → its numbers, for every PC of the campaign."""
    folder = paths.find_campaign(campaign) / "characters"
    out = {}
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        first = text.splitlines()[0] if text else ""
        out[first[2:].strip() if first.startswith("# ") else path.stem] = sheet_numbers(text)
    return out


def who(names: list, wanted: str) -> str:
    want = _key(wanted)
    hits = [n for n in names if _key(n) == want] or [n for n in names if _key(n).split()[0] == want] \
        or [n for n in names if want in _key(n)]
    if len(hits) != 1:
        raise TrapError(f"{wanted!r} is " + ("not a PC of this campaign" if not hits else "more than one PC: " + ", ".join(hits))
                        + f" (PCs: {', '.join(names) or 'none'})")
    return hits[0]


def absent(campaign: str) -> set:
    try:
        text = (paths.find_campaign(campaign) / "state.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    m = re.search(r"^[ \t]*(?:[-*][ \t]*)?absent:[ \t]*(.*)$", text, re.M | re.I)
    if not m or m.group(1).strip().lower() in ("", "none", "nessuno"):
        return set()
    return {_key(n) for n in m.group(1).split(",") if n.strip()}


# ─── The trap ─────────────────────────────────────────────────────────────────

def build(name: str, preset: str = "", **kw) -> dict:
    """A new trap from a preset and/or explicit numbers (explicit ones win)."""
    name = " ".join((name or "").split())[:80]
    if not name:
        raise TrapError("a trap needs a name")
    trap = {"name": name, "status": "armed", "noticed_by": [], "found_by": [], "log": [], "created": time.time()}
    if preset:
        key = PRESET_WORDS.get(preset.lower(), preset.lower())
        if key not in PRESETS:
            raise TrapError(f"no preset {preset!r} (trap.py presets lists them)")
        trap.update(json.loads(json.dumps({k: v for k, v in PRESETS[key].items() if k != "it"})))
        trap["preset"] = key
    for k in ("where", "trigger", "note", "at"):
        if kw.get(k):
            trap[k] = " ".join(str(kw[k]).split())[:300]
    for k in ("spot", "investigate", "disarm", "dispel"):
        if kw.get(k) is not None:
            if not 1 <= int(kw[k]) <= 35:
                raise TrapError(f"--{k} is a DC between 1 and 35")
            trap[k] = int(kw[k])
    for k in ("magic", "fail_springs", "resets"):
        if kw.get(k):
            trap[k] = True
    if kw.get("attack") is not None or kw.get("hit_damage"):
        hit = dict(trap.get("hit") or {})
        if kw.get("attack") is not None:
            hit["attack"] = int(kw["attack"])
        if kw.get("hit_damage"):
            parse_damage(kw["hit_damage"])
            hit["damage"] = kw["hit_damage"].strip()
        hit["shots"] = max(1, min(20, int(kw.get("shots") or hit.get("shots") or 1)))
        trap["hit"] = hit
    if kw.get("save") or kw.get("dc") is not None or kw.get("save_damage") or kw.get("condition"):
        sv = dict(trap.get("save") or {})
        if kw.get("save"):
            ab = ABILITY_WORDS.get(str(kw["save"]).lower(), str(kw["save"]).upper())
            if ab not in ABILITIES:
                raise TrapError(f"--save is an ability (STR DEX CON INT WIS CHA), not {kw['save']!r}")
            sv["ability"] = ab
        if kw.get("dc") is not None:
            sv["dc"] = int(kw["dc"])
        if kw.get("save_damage"):
            parse_damage(kw["save_damage"])
            sv["damage"] = kw["save_damage"].strip()
        if kw.get("half"):
            sv["half"] = True
        if kw.get("condition"):
            sv["condition"] = " ".join(kw["condition"].split())[:60]
        if not sv.get("ability") or not sv.get("dc"):
            raise TrapError("a saving throw needs --save <ability> and --dc")
        trap["save"] = sv
    if kw.get("damage"):
        parse_damage(kw["damage"])
        target = "save" if trap.get("save") and not trap["save"].get("damage") and not kw.get("save_damage") else "auto"
        if target == "save":
            trap["save"]["damage"] = kw["damage"].strip()
        else:
            trap["auto"] = {**(trap.get("auto") or {}), "damage": kw["damage"].strip()}
    if not any(trap.get(k) for k in ("hit", "save", "auto")) and trap.get("preset") != "sphere-of-annihilation":
        raise TrapError("what does it do? give --damage, --save/--dc, --attack/--hit-damage or a --preset")
    if not any(trap.get(k) for k in ("spot", "investigate")):
        trap.setdefault("log", []).append("no DC to spot it: only an action that clearly reveals it finds it")
    return trap


def describe(t: dict) -> str:
    dcs = " · ".join(f"{label} DC {t[k]}" for k, label in (("spot", "spot"), ("investigate", "investigate"),
                                                          ("disarm", "disarm"), ("dispel", "dispel magic")) if t.get(k))
    lines = [f"{t['name']} [{t['status']}]" + (" (magic)" if t.get("magic") else "")
             + (f" — {t['where']}" if t.get("where") else "") + (f" @ {t['at']}" if t.get("at") else "")]
    if t.get("trigger"):
        lines.append(f"  trigger: {t['trigger']}")
    if dcs:
        lines.append("  " + dcs + (" — a failed disarm springs it" if t.get("fail_springs") else ""))
    if t.get("hit"):
        h = t["hit"]
        lines.append(f"  attack +{h.get('attack', 0)}" + (f" ×{h['shots']}" if h.get("shots", 1) > 1 else "")
                     + (f", {h['damage']}" if h.get("damage") else ""))
    if t.get("auto"):
        a = t["auto"]
        lines.append("  no roll: " + ", ".join(x for x in (a.get("damage"), a.get("condition")) if x))
    if t.get("save"):
        s = t["save"]
        lines.append(f"  {s['ability']} save DC {s['dc']}" + (" (whoever is hit)" if s.get("after_hit") else "")
                     + (f": {s['damage']}" + (", half on a success" if s.get("half") else ", none on a success")
                        if s.get("damage") else "")
                     + (f"; failed: {s['condition']}" if s.get("condition") else ""))
    if t.get("noticed_by"):
        lines.append(f"  noticed in passing by: {', '.join(t['noticed_by'])}")
    if t.get("found_by"):
        lines.append(f"  found by: {', '.join(t['found_by'])}")
    if t.get("note"):
        lines.append(f"  note: {t['note']}")
    return "\n".join(lines)


def passive(trap: dict, numbers: dict) -> tuple:
    """(noticed, missed): [(name, passive)] against the DC to spot it."""
    dc = trap.get("spot")
    noticed, missed = [], []
    for name, nums in numbers.items():
        (noticed if dc and nums["passive"] >= dc else missed).append((name, nums["passive"]))
    return noticed, missed


def check_bonus(trap: dict, kind: str, nums: dict, arcana: bool = False, tools: bool = True) -> tuple:
    """(modifier, label, DC, advantage) of a check against the trap."""
    skill, dc_key = CHECKS[kind]
    if dc_key == "disarm" and trap.get("magic") and arcana:
        dc = trap.get("disarm") or trap.get("dispel")
        return nums["skills"].get("Arcana", nums["mods"].get("INT", 0)), "Intelligence (Arcana)", dc, "normal"
    if arcana and dc_key == "spot":
        if not trap.get("magic"):
            raise TrapError("Arcana finds magic traps only")
        return nums["skills"].get("Arcana", nums["mods"].get("INT", 0)), "Intelligence (Arcana)", trap.get("spot"), "normal"
    dc = trap.get(dc_key)
    if dc_key == "disarm":
        if dc is None:
            raise TrapError(f"{trap['name']} has no disarm DC — say what it takes (trap.py set … --disarm N)")
        if not tools:
            if not trap.get("tools_optional"):
                raise TrapError("without thieves' tools this one cannot be disarmed by a check")
            return nums["mods"].get("DEX", 0), "Dexterity, an edged tool (disadvantage)", dc, "disadvantage"
        if nums.get("tools") is None:
            return nums["mods"].get("DEX", 0), "Dexterity with thieves' tools (not proficient)", dc, "normal"
        return nums["tools"], "Dexterity with thieves' tools", dc, "normal"
    if dc is None:
        raise TrapError(f"{trap['name']} has no {'spot' if dc_key == 'spot' else 'investigate'} DC")
    ability = "Wisdom" if skill == "Perception" else "Intelligence"
    return nums["skills"].get(skill, nums["mods"].get("WIS" if skill == "Perception" else "INT", 0)), \
        f"{ability} ({skill})", dc, "normal"


def apply_check(trap: dict, kind: str, character: str, total: int, dc: int) -> list:
    """Move the trap on after a check. Returns lines for the DM."""
    dc_key = CHECKS[kind][1]
    ok = total >= dc
    lines = [f"{character}: {total} vs DC {dc} — {'success' if ok else 'failure'}"]
    if dc_key in ("spot", "investigate") and ok:
        if character not in trap["found_by"]:
            trap["found_by"].append(character)
        if trap["status"] in ("armed", "noticed"):
            trap["status"] = "found"
        lines.append("FOUND: " + ("they see it — " if dc_key == "spot" else "they understand how it works — ")
                     + "describe what gives it away" + (" (draw ^ on the battle map)" if trap.get("at") else ""))
    elif dc_key == "disarm":
        if ok:
            trap["status"] = "disarmed"
            lines.append("DISARMED: it will not go off")
        elif trap.get("fail_springs"):
            trap["status"] = "sprung"
            lines.append(f"IT GOES OFF: the failed attempt springs it — trap.py spring {json.dumps(trap['name'])} "
                         f"--targets \"{character}, …\"")
        else:
            lines.append("not disarmed; the trap is still armed (they may try again if they have the time)")
    trap["log"].append(f"{time.strftime('%Y-%m-%d %H:%M')} {kind} {character}: {total} vs {dc}")
    return lines


def spring(trap: dict, targets: list, rng=None, save_roll=None) -> list:
    """Spring the trap on `targets` [(name, ac, save_bonus, is_pc)]. `save_roll(name, bonus, dc, ability)` → total.

    Returns lines, one block per target, for the DM to apply."""
    out = [f"{trap['name']} goes off" + (f" ({trap['trigger']})" if trap.get("trigger") else "") + ":"]
    hit_by = {name: 0 for name, *_ in targets}
    hit_dmg = {name: [] for name, *_ in targets}
    if trap.get("hit") and targets:
        h = trap["hit"]
        for shot in range(h.get("shots", 1)):
            name, ac, _bonus, _pc = targets[secrets.randbelow(len(targets))] if h.get("shots", 1) > 1 \
                else targets[shot % len(targets)]
            roll = d20(rng)
            total = roll + int(h.get("attack", 0))
            hits = roll != 1 and (roll == 20 or ac is None or total >= ac)
            line = f"  {'dart' if h.get('shots', 1) > 1 else 'attack'} → {name}: {total} (d20 {roll} +{h.get('attack', 0)})" \
                   + (f" vs AC {ac}" if ac is not None else " (AC unknown: it hits if that beats its AC)")
            if hits:
                dmg, detail = roll_damage(h.get("damage", ""), rng) if h.get("damage") else (0, "")
                if roll == 20 and h.get("damage"):
                    extra, extra_detail = roll_damage(dice_only(h["damage"]), rng)
                    dmg, detail = dmg + extra, detail + f" · critical: {extra_detail}"
                hit_by[name] += 1
                hit_dmg[name].append(dmg)
                line += f" — HIT{' (critical)' if roll == 20 else ''}" + (f", {dmg} ({detail})" if h.get("damage") else "")
            else:
                line += " — miss"
            out.append(line)
    for name, ac, bonus, is_pc in targets:
        parts, total_dmg = [], sum(hit_dmg[name])
        if trap.get("auto"):
            a = trap["auto"]
            if a.get("damage"):
                dmg, detail = roll_damage(a["damage"], rng)
                total_dmg += dmg
                parts.append(f"{dmg} ({detail})")
            if a.get("condition"):
                parts.append(a["condition"].upper())
        sv = trap.get("save")
        # One save per hit when the save rides on a hit (each poisoned dart), else one.
        for _ in range(hit_by[name] if sv and sv.get("after_hit") else 1 if sv else 0):
            rolled = save_roll(name, bonus, sv["dc"], sv["ability"]) if save_roll else None
            if rolled is None:
                rolled = d20(rng) + (bonus or 0)
            ok = rolled >= sv["dc"]
            saved = f"{sv['ability']} save {rolled} vs DC {sv['dc']}: {'success' if ok else 'FAILED'}"
            if sv.get("damage"):
                dmg, detail = roll_damage(sv["damage"], rng)
                dmg = dmg // 2 if ok and sv.get("half") else (0 if ok else dmg)
                total_dmg += dmg
                saved += f", {dmg} ({detail}" + (", halved" if ok and sv.get("half") else "") + ")"
            if sv.get("condition") and not ok:
                saved += f", {sv['condition'].upper()}"
            parts.append(saved)
        if not parts and not hit_by[name]:
            parts.append("untouched")
        out.append(f"  {name}: " + " · ".join(parts) + (f" → {total_dmg} damage in all" if total_dmg else ""))
    trap["status"] = "armed" if trap.get("resets") else "sprung"
    trap["log"].append(f"{time.strftime('%Y-%m-%d %H:%M')} sprung on {', '.join(n for n, *_ in targets) or 'nobody'}")
    if trap.get("note"):
        out.append(f"  note: {trap['note']}")
    return out


# ─── The table: phones, notes, the DM's screen ────────────────────────────────

def _display(script: str) -> str:
    return str(paths.display_dir() / script)


def ask_roll(character: str, modifier: int, label: str, dc=None, advantage: str = "normal", timeout: int = 180):
    """Ask the player's phone for a d20 (send.py --dice-request --wait). The total, or None when nothing came back.

    `dc` is shown on the phone: None keeps it for the DM (a search must not say there is something to find)."""
    cmd = [sys.executable, _display("send.py"), "--dice-request", "--character", character, "--spec", "1d20",
           "--modifier", str(modifier), "--label", label, "--advantage", advantage,
           "--wait", "--wait-timeout", str(timeout)] + (["--dc", str(dc)] if dc is not None else [])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout + 15,
                           env=dict(os.environ, PYTHONUTF8="1"))
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in r.stdout.splitlines():
        m = re.match(rf"^{re.escape(character)}: (-?\d+) \(", line.strip())
        if m:
            return int(m.group(1))
    return None


def ask_rolls(requests: list, timeout: int = 180) -> dict:
    """Several phones at once: [(character, modifier, label, dc)] → {character: total or None}."""
    procs = {}
    for character, modifier, label, dc in requests:
        cmd = [sys.executable, _display("send.py"), "--dice-request", "--character", character, "--spec", "1d20",
               "--modifier", str(modifier), "--label", label, "--dc", str(dc), "--wait", "--wait-timeout", str(timeout)]
        try:
            procs[character] = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                                encoding="utf-8", env=dict(os.environ, PYTHONUTF8="1"))
        except OSError:
            procs[character] = None
    out = {}
    for character, p in procs.items():
        out[character] = None
        if p is None:
            continue
        try:
            stdout, _ = p.communicate(timeout=timeout + 15)
        except subprocess.TimeoutExpired:
            p.kill()
            continue
        for line in stdout.splitlines():
            m = re.match(rf"^{re.escape(character)}: (-?\d+) \(", line.strip())
            if m:
                out[character] = int(m.group(1))
    return out


def tell(character: str, text: str) -> bool:
    """A secret note to one player (whisper.py, no teaser for the others)."""
    try:
        r = subprocess.run([sys.executable, _display("whisper.py"), "--to", character, "--no-teaser", text],
                           capture_output=True, text=True, encoding="utf-8", timeout=15, env=dict(os.environ, PYTHONUTF8="1"))
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _foe_numbers(campaign: str) -> dict:
    """AC of the monsters and NPCs tracker.py knows."""
    try:
        data = json.loads((paths.find_campaign(campaign) / "tracker.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {str(v.get("name", k)): v.get("ac") for k, v in (data or {}).items() if isinstance(v, dict)}


def _pairs(values: list, what: str) -> dict:
    out = {}
    for v in values or []:
        name, sep, n = v.rpartition(":")
        if not sep or not name.strip():
            raise TrapError(f"{what} is NAME:N, not {v!r}")
        try:
            out[_key(name)] = int(n.replace("+", ""))
        except ValueError:
            raise TrapError(f"{what} is NAME:N, not {v!r}")
    return out


# ─── Command line ─────────────────────────────────────────────────────────────

def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="Traps: noticed, searched for, disarmed, sprung.")
    ap.add_argument("--campaign", "-c", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("presets", help="the SRD sample traps")
    p = sub.add_parser("rate", help="DCs, attack bonuses and damage by severity for a level (SRD)")
    p.add_argument("--level", type=int, required=True)
    p = sub.add_parser("add", help="set a trap")
    p.add_argument("name")
    p.add_argument("--preset", default="")
    for k in ("where", "trigger", "note", "at"):
        p.add_argument(f"--{k}", default="")
    for k in ("spot", "investigate", "disarm", "dispel", "attack", "shots", "dc"):
        p.add_argument(f"--{k}", type=int, default=None)
    p.add_argument("--hit-damage", default="", help="damage of each hit (with --attack)")
    p.add_argument("--save", default="", help="the ability of the saving throw")
    p.add_argument("--save-damage", default="", help="damage the save is against")
    p.add_argument("--damage", default="", help="the damage: against the save if there is one, else automatic")
    p.add_argument("--condition", default="", help="what a failed save adds (prone, restrained, poisoned (1 hour)…)")
    for k in ("half", "magic", "fail-springs", "resets"):
        p.add_argument(f"--{k}", action="store_true")
    p = sub.add_parser("list", help="the traps still to be dealt with")
    p.add_argument("--all", action="store_true", help="disarmed and sprung ones too")
    sub.add_parser("show").add_argument("name")
    p = sub.add_parser("pass", help="the party passes by: passive Perception against the DC to spot it")
    p.add_argument("name")
    p.add_argument("--who", default="", help="who passes (default: every PC who is not absent)")
    p.add_argument("--tell", default="", help="a secret note for each one who notices")
    p = sub.add_parser("check", help="search, investigate or disarm: the roll on the player's phone")
    p.add_argument("name")
    p.add_argument("kind", choices=sorted(CHECKS))
    p.add_argument("character")
    p.add_argument("--roll", type=int, default=None, help="the total, when it was rolled already")
    p.add_argument("--here", action="store_true", help="roll it here instead of on the phone")
    p.add_argument("--arcana", action="store_true", help="Intelligence (Arcana), for a magic trap")
    p.add_argument("--no-tools", action="store_true", help="without thieves' tools: an edged tool, at disadvantage")
    p = sub.add_parser("spring", help="the trap goes off")
    p.add_argument("name")
    p.add_argument("--targets", required=True, help="who is in it: PCs by name, foes by name or token letter")
    p.add_argument("--ac", action="append", default=[], metavar="NAME:N", help="AC of a target the sheets do not give")
    p.add_argument("--save-bonus", action="append", default=[], metavar="NAME:N", help="a foe's save bonus (default +0)")
    p.add_argument("--here", action="store_true", help="roll the PCs' saves here instead of on their phones")
    p = sub.add_parser("set", help="change a trap")
    p.add_argument("name")
    p.add_argument("--status", choices=STATUSES)
    for k in ("spot", "investigate", "disarm", "dispel"):
        p.add_argument(f"--{k}", type=int, default=None)
    for k in ("where", "note", "at"):
        p.add_argument(f"--{k}", default=None)
    sub.add_parser("remove").add_argument("name")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "presets":
            for key, t in PRESETS.items():
                print(f"{key} ({t['it']})" + (" — magic" if t.get("magic") else "")
                      + "\n" + "\n".join(describe(build(t["it"], key)).splitlines()[1:]))
            return 0
        if args.cmd == "rate":
            for sev, r in rate(args.level).items():
                print(f"{sev:<9} save DC {r['dc'][0]}-{r['dc'][1]} · attack +{r['attack'][0]} to +{r['attack'][1]} · "
                      f"damage {r['damage']}")
            print("(SRD 5.1 / DMG p. 121 — setback rarely kills, dangerous can, deadly likely does)")
            return 0
        camp = _campaign(args.campaign)
        path = data_path(camp)
        traps = load(path)
        if args.cmd == "list":
            rows = [t for t in traps if args.all or t["status"] not in ("disarmed", "sprung")]
            print("\n".join(describe(t).splitlines()[0] for t in rows) or "no trap " + ("set" if args.all else "left armed"))
            return 0
        if args.cmd == "show":
            print(describe(find(traps, args.name)))
            return 0
        if args.cmd == "add":
            if any(_key(t["name"]) == _key(args.name) for t in traps):
                raise TrapError(f"there is a trap called {args.name!r} already")
            trap = build(args.name, args.preset, where=args.where, trigger=args.trigger, note=args.note, at=args.at,
                         spot=args.spot, investigate=args.investigate, disarm=args.disarm, dispel=args.dispel,
                         attack=args.attack, shots=args.shots, hit_damage=args.hit_damage, save=args.save, dc=args.dc,
                         save_damage=args.save_damage, damage=args.damage, condition=args.condition, half=args.half,
                         magic=args.magic, fail_springs=args.fail_springs, resets=args.resets)
            traps.append(trap)
            save(path, traps)
            print("+ " + describe(trap))
            return 0
        trap = find(traps, args.name)
        if args.cmd == "remove":
            traps.remove(trap)
            save(path, traps)
            print(f"- {trap['name']} removed")
            return 0
        if args.cmd == "set":
            changed = False
            if args.status:
                trap["status"], changed = args.status, True
            for k in ("spot", "investigate", "disarm", "dispel"):
                if getattr(args, k) is not None:
                    trap[k], changed = getattr(args, k), True
            for k in ("where", "note", "at"):
                if getattr(args, k) is not None:
                    trap[k], changed = " ".join(getattr(args, k).split())[:300], True
            if not changed:
                raise TrapError("nothing to change")
            save(path, traps)
            print(describe(trap))
            return 0

        numbers = party(camp)
        if args.cmd == "pass":
            if trap["status"] in ("disarmed", "sprung"):
                raise TrapError(f"{trap['name']} is {trap['status']}")
            if args.who:
                chosen = {who(list(numbers), n): None for n in args.who.split(",") if n.strip()}
            else:
                away = absent(camp)
                chosen = {n: None for n in numbers if _key(n) not in away}
            noticed, missed = passive(trap, {n: numbers[n] for n in chosen})
            if not trap.get("spot"):
                print(f"{trap['name']} has no DC to spot it: passing by, nobody notices (only a search or an action finds it)")
                return 0
            for name, score in noticed:
                print(f"NOTICED: {name} (passive Perception {score} vs DC {trap['spot']})")
                if name not in trap["noticed_by"]:
                    trap["noticed_by"].append(name)
                if args.tell:
                    print(f"  secret note to {name}: " + ("sent" if tell(name, args.tell) else "NOT sent (display off?)"))
            for name, score in missed:
                print(f"missed:  {name} (passive Perception {score} vs DC {trap['spot']})")
            if noticed and trap["status"] == "armed":
                trap["status"] = "noticed"
            trap["log"].append(f"{time.strftime('%Y-%m-%d %H:%M')} passed by {', '.join(chosen)}: "
                               f"noticed by {', '.join(n for n, _ in noticed) or 'nobody'}")
            save(path, traps)
            if not noticed:
                print("Nobody notices it. If they walk on, it goes off: trap.py spring …")
            return 0
        if args.cmd == "check":
            name = who(list(numbers), args.character)
            mod, label, dc, adv = check_bonus(trap, args.kind, numbers[name], args.arcana, not args.no_tools)
            if dc is None:
                raise TrapError(f"{trap['name']} has no DC for that")
            if args.roll is not None:
                total = args.roll
            elif args.here:
                rolls = [d20(), d20()]
                total = (min(rolls) if adv == "disadvantage" else rolls[0]) + mod
                print(f"rolled here: d20 {rolls if adv == 'disadvantage' else rolls[0]} {mod:+d}")
            else:
                print(f"asking {name}'s phone: {label} {mod:+d}, DC {dc}…", file=sys.stderr)
                found = CHECKS[args.kind][1] == "disarm"
                total = ask_roll(name, mod, f"{label} — {trap['name']}" if found else label, dc if found else None, adv)
                if total is None:
                    raise TrapError("no roll came back (display off, or nobody rolled) — ask it at the table and pass --roll N")
            for line in apply_check(trap, args.kind, name, total, dc):
                print(line)
            save(path, traps)
            return 0
        if args.cmd == "spring":
            if trap["status"] == "disarmed":
                raise TrapError(f"{trap['name']} is disarmed")
            acs, bonuses = _pairs(args.ac, "--ac"), _pairs(args.save_bonus, "--save-bonus")
            foes = _foe_numbers(camp)
            targets = []
            for raw in [n.strip() for n in args.targets.split(",") if n.strip()]:
                try:
                    name = who(list(numbers), raw)
                    nums = numbers[name]
                    ab = (trap.get("save") or {}).get("ability", "DEX")
                    targets.append((name, acs.get(_key(name), nums["ac"]), nums["saves"].get(ab, nums["mods"].get(ab, 0)), True))
                except TrapError:
                    foe_ac = next((ac for n, ac in foes.items() if _key(n) == _key(raw)), None)
                    targets.append((raw, acs.get(_key(raw), foe_ac), bonuses.get(_key(raw), 0), False))
            sv = trap.get("save")
            asked = {}
            if sv and not args.here:
                # The PCs roll their saves on their phones, all at once.
                requests = [(n, b, f"{sv['ability']} save — {trap['name']}", sv["dc"]) for n, _ac, b, pc in targets if pc]
                if requests and not sv.get("after_hit"):
                    print(f"asking for {sv['ability']} saves on {', '.join(r[0] for r in requests)}'s phones…", file=sys.stderr)
                    asked = ask_rolls(requests)

            def save_roll(name, bonus, dc, ability):
                if asked.get(name) is not None:
                    return asked[name]
                if sv and sv.get("after_hit") and not args.here and any(n == name and pc for n, _a, _b, pc in targets):
                    got = ask_roll(name, bonus, f"{ability} save — {trap['name']}", dc)
                    if got is not None:
                        return got
                return None
            for line in spring(trap, targets, save_roll=save_roll):
                print(line)
            if sv and any(pc and asked.get(n) is None for n, _a, _b, pc in targets) and not args.here \
                    and not sv.get("after_hit"):
                print("  (a PC's save did not come back from the phone and was rolled here)")
            print("Apply it: --stat-hp for the PCs, tracker.py hit for the foes, the conditions with --stat-condition-add.")
            save(path, traps)
            return 0
    except TrapError as e:
        print(f"trap: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
