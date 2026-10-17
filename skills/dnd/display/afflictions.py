#!/usr/bin/env python3
"""afflictions.py — diseases, poisons and madness that run their course.

An affliction is not a single roll: a disease gets worse or better day after
day, a poison lasts its hours, a madness its minutes, hours or for good (DMG
2014 pp. 256-260, the SRD's diseases, poisons and madness). Each one is kept
in <campaign>/afflictions.json with its state, and the clock moves it:

- at every dawn (calendar.py advance / rest / time) each character with a
  disease that calls for it gets the day's saving throw on the phone, and the
  result is applied by the display at once: sewer plague adds or removes a
  level of exhaustion, cackle fever lowers its DC (three failures bring an
  indefinite madness), pale tincture counts the successes towards seven and
  deals 1d6 for each failure; sight rot simply worsens, unless treated;
- midnight tears strike at midnight; the poisons with a duration (oil of
  taggit, essence of ether, torpor…) end when their hours are over;
- a madness is short-term (1d10 minutes), long-term (1d10 × 10 hours) or
  indefinite (until cured), with its effect rolled on the SRD's tables.

    afflictions.py expose Lyra "veleno di serpente"          # the save on the phone, then the effect
    afflictions.py expose Lyra "epidemia fognaria" --total 8  # the save is already rolled
    afflictions.py add Lyra "febbre da gallina"              # infected, no save (it already failed)
    afflictions.py custom Hernio "Febbre di palude" --dc 12 --onset 2d --effect "Brividi e deliri"
    afflictions.py madness Lyra short                        # long | indefinite; --save WIS --dc 15 to resist first
    afflictions.py madness Lyra long --d100 58               # a given row of the table
    afflictions.py treat Lyra "vista putrefatta"             # a dose of eyebright ointment before the night
    afflictions.py cure Lyra "epidemia fognaria"             # or --all; ristorare inferiore and the like
    afflictions.py resolve Lyra "epidemia fognaria" --total 12   # a dawn save rolled without the phone
    afflictions.py ask                                       # send the saves still due to the phones again
    afflictions.py dawn                                      # a dawn with no calendar
    afflictions.py list                                      # the catalogue
    afflictions.py show [--who Lyra]

Names in English or Italian. Conditions (exhaustion, poisoned, unconscious…)
and damage are put on the display; the DM writes them on the sheet. Every
change reaches the DM as an `[[AFFLICTION: …]]` line from check_input.py.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

DAWN_HOUR = 6
MIDNIGHT_HOUR = 0
ABILITIES = ("STR", "DEX", "CON", "INT", "WIS", "CHA")
ABILITY_IT = {"STR": "FOR", "DEX": "DES", "CON": "COS", "INT": "INT", "WIS": "SAG", "CHA": "CAR"}
ABILITY_NAME = {"en": {"CON": "Constitution", "WIS": "Wisdom", "CHA": "Charisma"},
                "it": {"CON": "Costituzione", "WIS": "Saggezza", "CHA": "Carisma"}}
EXHAUSTION = [f"Exhaustion {n}" for n in range(1, 7)]
MADNESS_KINDS = {"short": "short", "breve": "short", "long": "long", "lunga": "long", "indefinite": "indefinite",
                 "indefinita": "indefinite"}
HISTORY_KEPT = 12

# The SRD's diseases (DMG 2014 pp. 257-258). Onset: (unit, low, high) rolled at infection.
# model: exhaustion (a save each dawn moves the level), dc_drop (a made save lowers the DC by 1d6,
# three failures bring an indefinite madness), worsen (no save: one step worse each dawn),
# successes (a save each dawn, so many successes end it, each failure deals damage).
DISEASES = {
    "sewer_plague": {
        "names": {"en": "Sewer plague", "it": "Epidemia fognaria"}, "dc": 11, "onset": ("d", 1, 4), "model": "exhaustion",
        "effect": {
            "en": "Fatigue and cramps: hit dice heal only half, a long rest heals no hit points. Each dawn a DC 11 "
                  "Constitution save: failed, one more level of exhaustion; made, one less — below 1 it is over.",
            "it": "Stanchezza e crampi: i Dadi Vita curano solo la metà, il riposo lungo non fa recuperare punti ferita. "
                  "A ogni alba un TS Costituzione CD 11: se fallisce un livello di indebolimento in più, se riesce uno "
                  "in meno; sotto 1 è guarito."}},
    "cackle_fever": {
        "names": {"en": "Cackle fever", "it": "Febbre da gallina"}, "dc": 13, "onset": ("h", 1, 4), "model": "dc_drop",
        "effect": {
            "en": "Fever and disorientation: one level of exhaustion that nothing removes while it lasts. Any stress "
                  "(a fight, a wound, fear, a nightmare): DC 13 Constitution save or 5 (1d10) psychic damage and "
                  "incapacitated with mad laughter for 1 minute (save again at the end of each turn); who starts a "
                  "turn within 3 m of the laughter saves DC 10 or catches it. Each dawn a Constitution save: made, the "
                  "DC drops by 1d6 — at 0 it is over; three failures bring an indefinite madness. Gnomes are immune.",
            "it": "Febbre e confusione: un livello di indebolimento che nulla toglie finché dura. Ogni momento di "
                  "tensione (combattere, una ferita, la paura, un incubo): TS Costituzione CD 13 o 5 (1d10) danni "
                  "psichici e incapacitato da una risata folle per 1 minuto (ritenta alla fine di ogni turno); chi "
                  "inizia il turno entro 3 m da chi ride fa un TS CD 10 o si contagia. A ogni alba un TS Costituzione: "
                  "se riesce la CD scende di 1d6, a 0 è guarito; tre fallimenti portano una follia indefinita. Gli "
                  "gnomi sono immuni."}},
    "sight_rot": {
        "names": {"en": "Sight rot", "it": "Vista putrefatta"}, "dc": 15, "onset": ("d", 1, 1), "model": "worsen",
        "max": 5, "final": "Blinded", "cure_doses": 3,
        "effect": {
            "en": "Blurred sight: a penalty to attack rolls and ability checks that rely on sight, 1 worse each dawn; "
                  "at -5 blinded until cured (lesser restoration, heal). Eyebright ointment (herbalism kit, 1 hour): "
                  "a dose before the night stops it worsening, three doses cure it.",
            "it": "La vista si annebbia: penalità ai tiri per colpire e alle prove che usano la vista, 1 in più a ogni "
                  "alba; a -5 accecato finché non viene curato (ristorare inferiore, guarigione). Unguento di "
                  "Occhiolucente (borsa da erborista, 1 ora): una dose prima della notte ferma il peggioramento, tre "
                  "dosi guariscono."}},
}

# The SRD's poisons (DMG 2014 p. 258) and the basic poison of the PHB (p. 153).
# fail: conditions on a failed save; hours/minutes: how long they last; damage: rolled on a failure
# (half on a success when half=True); model: a course that goes on after the first save.
POISONS = {
    "assassins_blood": {"names": {"en": "Assassin's blood", "it": "Sangue dell'assassino"}, "type": "ingested", "dc": 10,
                        "damage": "1d12", "half": True, "fail": ["Poisoned"], "hours": 24},
    "burnt_othur_fumes": {"names": {"en": "Burnt othur fumes", "it": "Fumi di othur bruciato"}, "type": "inhaled", "dc": 13,
                          "damage": "3d6", "note": {"en": "save again at the start of each turn: 1d6 poison damage on each failure, three successes end it",
                                                   "it": "ritenta all'inizio di ogni turno: 1d6 danni da veleno a ogni fallimento, tre successi lo fanno finire"}},
    "crawler_mucus": {"names": {"en": "Crawler mucus", "it": "Muco di vermeiena"}, "type": "contact", "dc": 13,
                      "fail": ["Poisoned", "Paralyzed"], "minutes": 1,
                      "note": {"en": "save again at the end of each turn to end the paralysis",
                               "it": "ritenta alla fine di ogni turno per liberarsi dalla paralisi"}},
    "drow_poison": {"names": {"en": "Drow poison", "it": "Veleno drow"}, "type": "injury", "dc": 13,
                    "fail": ["Poisoned"], "hours": 1, "fail_by_5": ["Unconscious"],
                    "note": {"en": "unconscious if the save fails by 5 or more: wakes when damaged or shaken awake",
                             "it": "privo di sensi se fallisce di 5 o più: si sveglia se ferito o scosso"}},
    "essence_of_ether": {"names": {"en": "Essence of ether", "it": "Essenza di etere"}, "type": "inhaled", "dc": 15,
                         "fail": ["Poisoned", "Unconscious"], "hours": 8,
                         "note": {"en": "wakes when damaged or shaken awake", "it": "si sveglia se ferito o scosso"}},
    "malice": {"names": {"en": "Malice", "it": "Malizia"}, "type": "inhaled", "dc": 15, "fail": ["Poisoned", "Blinded"], "hours": 1},
    "midnight_tears": {"names": {"en": "Midnight tears", "it": "Lacrime di mezzanotte"}, "type": "ingested", "dc": 17,
                       "model": "midnight", "damage": "9d6", "half": True,
                       "note": {"en": "nothing until midnight; then a DC 17 Constitution save, 9d6 poison damage (half on a success)",
                                "it": "nessun effetto fino a mezzanotte; allora TS Costituzione CD 17, 9d6 danni da veleno (metà se riesce)"}},
    "oil_of_taggit": {"names": {"en": "Oil of taggit", "it": "Olio di taggit"}, "type": "contact", "dc": 13,
                      "fail": ["Poisoned", "Unconscious"], "hours": 24,
                      "note": {"en": "wakes when damaged", "it": "si sveglia se ferito"}},
    "pale_tincture": {"names": {"en": "Pale tincture", "it": "Tintura pallida"}, "type": "ingested", "dc": 16,
                      "damage": "1d6", "fail": ["Poisoned"], "model": "successes", "need": 7,
                      "note": {"en": "a save every day, 1d6 poison damage on each failure; the damage cannot be healed until seven successes end it",
                               "it": "un TS ogni giorno, 1d6 danni da veleno a ogni fallimento; quei danni non si curano finché sette successi non lo fanno finire"}},
    "purple_worm_poison": {"names": {"en": "Purple worm poison", "it": "Veleno di verme purpureo"}, "type": "injury", "dc": 19,
                           "damage": "12d6", "half": True},
    "serpent_venom": {"names": {"en": "Serpent venom", "it": "Veleno di serpente"}, "type": "injury", "dc": 11,
                      "damage": "3d6", "half": True},
    "torpor": {"names": {"en": "Torpor", "it": "Torpore"}, "type": "ingested", "dc": 15,
               "fail": ["Poisoned", "Incapacitated"], "hours": "4d6"},
    "truth_serum": {"names": {"en": "Truth serum", "it": "Siero della verità"}, "type": "ingested", "dc": 11,
                    "fail": ["Poisoned"], "hours": 1,
                    "note": {"en": "cannot knowingly speak a lie", "it": "non può mentire consapevolmente"}},
    "wyvern_poison": {"names": {"en": "Wyvern poison", "it": "Veleno di viverna"}, "type": "injury", "dc": 15,
                      "damage": "7d6", "half": True},
    "basic_poison": {"names": {"en": "Basic poison", "it": "Veleno base"}, "type": "injury", "dc": 10, "damage": "1d4"},
}

# Which conditions a row of the SRD's madness tables imposes (the effect itself is read from the SRD).
MADNESS_CONDITIONS = {
    "short": [((1, 20), ["Paralyzed"]), ((21, 30), ["Incapacitated"]), ((31, 40), ["Frightened"]),
              ((81, 90), ["Stunned"]), ((91, 100), ["Unconscious"])],
    "long": [((96, 100), ["Unconscious"])],
    "indefinite": [],
}
MADNESS_NAMES = {"short": {"en": "Short-term madness", "it": "Follia a breve termine"},
                 "long": {"en": "Long-term madness", "it": "Follia a lungo termine"},
                 "indefinite": {"en": "Indefinite madness", "it": "Follia indefinita"}}


class AfflictionError(Exception):
    """A request that cannot be applied; nothing was changed."""


# ─── The catalogue ────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    return "".join(ch for ch in (text or "").casefold() if ch.isalnum())


def find(word: str) -> tuple:
    """('disease' | 'poison', key) for a name in English or Italian."""
    w = _norm(word)
    for kind, table in (("disease", DISEASES), ("poison", POISONS)):
        for key, d in table.items():
            if w in (_norm(key), _norm(d["names"]["en"]), _norm(d["names"]["it"])):
                return kind, key
    raise AfflictionError(f"no disease or poison called {word!r} — afflictions.py list, or `custom`")


def roll(expr, rng: random.Random) -> int:
    """'3d6' → a roll; an int stays as it is."""
    if isinstance(expr, int):
        return expr
    m = re.fullmatch(r"\s*(\d*)d(\d+)\s*([+-]\s*\d+)?\s*", str(expr))
    if not m:
        raise AfflictionError(f"not a dice expression: {expr!r}")
    n, sides = int(m.group(1) or 1), int(m.group(2))
    return sum(rng.randint(1, sides) for _ in range(n)) + int((m.group(3) or "0").replace(" ", ""))


def _hours_of(spec: str) -> int:
    """'2d' → 48, '6h' → 6, '1' → 24 (days by default)."""
    m = re.fullmatch(r"\s*(\d+)\s*([dhgo]?)\s*", str(spec or "").lower())
    if not m:
        raise AfflictionError(f"--onset is like 2d or 6h — not {spec!r}")
    return int(m.group(1)) * (1 if m.group(2) in ("h", "o") else 24)


# ─── Madness tables, from the SRD (English and Italian) ───────────────────────

_madness_cache: dict = {}


def madness_table(kind: str, lang: str = "en") -> list:
    """[((low, high), text)] for 'short' | 'long' | 'indefinite', read from the SRD rule 'Madness'."""
    ck = (kind, lang)
    if ck in _madness_cache:
        return _madness_cache[ck]
    rows: list = []
    try:
        import lookup
        rec = lookup.lookup_record("madness", category="rule")
        text = rec.get("description", "") if rec else ""
        if lang != "en" and rec:
            loc = lookup._localize(rec, lang)
            text = (loc or {}).get("description") or text
        tables, cur = [], None
        for line in str(text).splitlines():
            m = re.match(r"\|\s*(\d{2,3})(?:\s*[-–]\s*(\d{2,3}))?\s*\|\s*(.+?)\s*\|\s*$", line)
            if m:
                lo = int(m.group(1))
                hi = int(m.group(2) or m.group(1))
                if cur is None or (lo == 1 and cur):
                    cur = []
                    tables.append(cur)
                cur.append(((lo, 100 if hi in (0, 100) else hi), m.group(3).strip()))
            elif cur is not None and line.strip() and not line.strip().startswith("|"):
                cur = None
        idx = {"short": 0, "long": 1, "indefinite": 2}[kind]
        rows = tables[idx] if len(tables) > idx else []
    except Exception:
        rows = []
    _madness_cache[ck] = rows
    return rows


def madness_effect(kind: str, d100: int) -> dict:
    out = {}
    for lang in ("en", "it"):
        row = next((t for (lo, hi), t in madness_table(kind, lang) if lo <= d100 <= hi), "")
        if row:
            out[lang] = row.strip('"').strip("“”")
    return out


def madness_conditions(kind: str, d100: int, rng: random.Random) -> list:
    if kind == "long" and 56 <= d100 <= 65:
        return ["Blinded"] if rng.randint(1, 4) == 1 else ["Deafened"]      # blinded 25%, deafened 75%
    return next((c for (lo, hi), c in MADNESS_CONDITIONS[kind] if lo <= d100 <= hi), [])


# ─── Data ─────────────────────────────────────────────────────────────────────

def _active_campaign() -> str:
    import media
    return media.active_campaign() or ""


def _campaign(campaign: str = "") -> str:
    import media
    camp = media.safe_campaign(campaign) if campaign else media.active_campaign()
    if not camp:
        raise AfflictionError("no active campaign — pass --campaign or load one first")
    return camp


def data_path(campaign: str = "") -> str:
    import media
    return str(media.find_campaign(_campaign(campaign)) / "afflictions.json")


def blank() -> dict:
    return {"next": 1, "list": []}


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        return blank()
    except ValueError as e:
        raise AfflictionError(f"afflictions.json is not valid JSON ({e})")
    base = blank()
    if isinstance(data, dict):
        base.update({k: data[k] for k in base if k in data})
    base["list"] = [e for e in base["list"] if isinstance(e, dict) and e.get("who")]
    return base


def save(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _new(data: dict, who: str, kind: str, key: str, names: dict, **fields) -> dict:
    entry = {"id": data["next"], "who": who, "kind": kind, "key": key, "names": names, "status": "active",
             "due": 0, "pending": None, "conditions": [], "history": []}
    entry.update(fields)
    data["next"] += 1
    data["list"].append(entry)
    return entry


def entries_of(data: dict, who: str) -> list:
    return [e for e in data["list"] if _norm(e["who"]) == _norm(who)]


def find_entry(data: dict, who: str, name: str) -> dict:
    mine = entries_of(data, who)
    if not mine:
        raise AfflictionError(f"{who} has no affliction")
    if not name:
        if len(mine) == 1:
            return mine[0]
        raise AfflictionError(f"{who} has {len(mine)} afflictions — say which: " + ", ".join(e["names"]["en"] for e in mine))
    w = _norm(name)
    for e in mine:
        if w in (_norm(e["names"].get("en", "")), _norm(e["names"].get("it", "")), _norm(e.get("key", "")), str(e["id"])):
            return e
    raise AfflictionError(f"{who} has no affliction called {name!r}")


def _log(entry: dict, text: str) -> None:
    entry["history"] = (entry.get("history") or [])[-(HISTORY_KEPT - 1):] + [text]


def _changes(who: str) -> dict:
    return {"who": who, "add": [], "remove": [], "damage": 0, "lines": [], "event": "changed", "name": ""}


def _still_needed(data: dict, who: str, except_id) -> set:
    return {c for e in entries_of(data, who) if e["id"] != except_id and e.get("status") == "active"
            for c in e.get("conditions") or []}


def _set_conditions(data: dict, entry: dict, conds: list, ch: dict) -> None:
    """The entry now imposes `conds`: what to add and what to take off the display."""
    old = list(entry.get("conditions") or [])
    entry["conditions"] = list(conds)
    keep = _still_needed(data, entry["who"], entry["id"])
    for c in old:
        if c not in conds and c not in keep:
            ch["remove"].append(c)
    if any(c.startswith("Exhaustion") for c in conds):          # one level at a time on the display
        ch["remove"] += [x for x in EXHAUSTION if x not in conds and x not in keep and x not in ch["remove"]]
    for c in conds:
        if c not in old:
            ch["add"].append(c)


def end(data: dict, entry: dict, why: str) -> dict:
    """The affliction is over: it leaves the list and its conditions leave the display."""
    ch = _changes(entry["who"])
    _set_conditions(data, entry, [], ch)
    data["list"] = [e for e in data["list"] if e["id"] != entry["id"]]
    ch["event"], ch["name"] = "cured", entry["names"]["en"]
    ch["lines"].append(f"{entry['who']}: {entry['names']['en']} is over ({why})")
    return ch


# ─── Getting it ───────────────────────────────────────────────────────────────

def infect(data: dict, who: str, key: str, now_h, rng: random.Random) -> tuple:
    """A disease takes hold (the save failed): (entry, changes). Symptoms come after the onset."""
    d = DISEASES[key]
    unit, lo, hi = d["onset"]
    n = rng.randint(lo, hi)
    hours = n if unit == "h" else n * 24
    entry = _new(data, who, "disease", key, dict(d["names"]), model=d["model"], dc=d["dc"], save="CON",
                 status="incubating", onset_h=None if now_h is None else now_h + hours, onset_text=f"{n}{unit}",
                 level=0, failures=0, successes=0, doses=0, treated=False, effect=dict(d["effect"]))
    _log(entry, f"infected; symptoms in {n} {'hour(s)' if unit == 'h' else 'day(s)'}")
    ch = _changes(who)
    ch["event"], ch["name"] = "secret", d["names"]["en"]
    ch["lines"].append(f"{who} is infected with {d['names']['en']} — symptoms in {n} {'hour(s)' if unit == 'h' else 'day(s)'}"
                       + ("" if now_h is not None else " (no calendar: they show at the next `afflictions.py dawn`)")
                       + ". The players are not told until then.")
    return entry, ch


def custom(data: dict, who: str, name: str, now_h, dc: int, onset: str = "", model: str = "exhaustion",
           effect: str = "", save_ability: str = "CON", need: int = 0) -> tuple:
    if model not in ("exhaustion", "dc_drop", "successes", "worsen"):
        raise AfflictionError("--model is exhaustion, dc_drop, successes or worsen")
    if not 1 <= int(dc) <= 30:
        raise AfflictionError("--dc between 1 and 30")
    name = " ".join(str(name).split())[:60]
    if not name:
        raise AfflictionError("the disease needs a name")
    hours = _hours_of(onset) if onset else 0
    text = " ".join(str(effect or "").split())[:400]
    entry = _new(data, who, "disease", "custom", {"en": name, "it": name}, model=model, dc=int(dc),
                 save=save_ability.upper() if save_ability.upper() in ABILITIES else "CON",
                 status="incubating" if hours else "active", onset_h=None if now_h is None or not hours else now_h + hours,
                 level=0, failures=0, successes=0, doses=0, treated=False, need=max(1, int(need or 3)),
                 max=5, effect={"en": text, "it": text})
    ch = _changes(who)
    if not hours:
        ch = activate(data, entry)
    else:
        ch["event"], ch["name"] = "secret", name
        ch["lines"].append(f"{who} carries {name} — symptoms in {onset}. The players are not told until then.")
    return entry, ch


def activate(data: dict, entry: dict) -> dict:
    """The symptoms show: the disease starts doing what it does."""
    ch = _changes(entry["who"])
    entry["status"] = "active"
    model = entry.get("model")
    if model == "exhaustion":
        entry["level"] = 1
        _set_conditions(data, entry, ["Exhaustion 1"], ch)
    elif model == "dc_drop":
        _set_conditions(data, entry, ["Exhaustion 1"], ch)
    elif model == "worsen":
        entry["level"] = 1
    _log(entry, "symptoms")
    ch["event"], ch["name"] = "new", entry["names"]["en"]
    ch["lines"].append(f"{entry['who']}: the symptoms of {entry['names']['en']} show — {describe_state(entry)}")
    return ch


def poison(data: dict, who: str, key: str, total, now_h, rng: random.Random) -> dict:
    """The save against a poison: total (None = failed with no roll). The effect now, and what lasts."""
    p = POISONS[key]
    failed = total is None or total < p["dc"]
    ch = _changes(who)
    ch["name"] = p["names"]["en"]
    if p.get("model") == "midnight":            # midnight tears: the save is made at midnight, not now
        entry = _new(data, who, "poison", key, dict(p["names"]), model="midnight", dc=p["dc"], save="CON",
                     status="incubating", effect=dict(p["note"]))
        _log(entry, "swallowed")
        ch["event"] = "secret"
        ch["lines"].append(f"{who} has swallowed {p['names']['en']}: at midnight a DC {p['dc']} Constitution save, "
                           f"{p['damage']} poison damage (half on a success). The players are not told.")
        return ch
    if p.get("damage") and (failed or p.get("half")):
        dmg = roll(p["damage"], rng)
        ch["damage"] = dmg if failed else dmg // 2
    lasting = list(p.get("fail") or []) if failed else []
    if failed and p.get("fail_by_5") and total is not None and total <= p["dc"] - 5:
        lasting += p["fail_by_5"]
    verdict = "fails" if failed else "makes"
    ch["lines"].append(f"{who} {verdict} the DC {p['dc']} Constitution save against {p['names']['en']}"
                       + (f" ({total})" if total is not None else "")
                       + (f": {ch['damage']} poison damage" if ch["damage"] else "")
                       + (f"; {', '.join(lasting)}" if lasting else "")
                       + (f" — {p['note']['en']}" if p.get("note") and failed else ""))
    if not lasting and p.get("model") != "successes":
        ch["event"] = "hit" if ch["damage"] else "resisted"
        return ch
    if p.get("model") == "successes" and not failed:
        ch["event"] = "resisted"
        return ch
    hours = p.get("hours")
    hours = roll(hours, rng) if isinstance(hours, str) else hours
    minutes = p.get("minutes")
    until = None
    if now_h is not None and (hours or minutes):
        until = now_h + (hours or 1)
    entry = _new(data, who, "poison", key, dict(p["names"]), model=p.get("model") or "timed", dc=p["dc"], save="CON",
                 until_h=until, hours=hours, minutes=minutes, successes=0, need=p.get("need", 0),
                 effect=dict(p.get("note") or {}),
                 # a save every 24 hours: the first dawn counts only a day after the dose
                 not_before_h=None if now_h is None or p.get("model") != "successes" else now_h + 24)
    timed = [{"name": c, "duration_type": "rounds", "duration_remaining": 10 * minutes} for c in lasting] if minutes else lasting
    _set_conditions(data, entry, lasting, ch)
    if minutes:
        ch["add"] = [t for t in timed]
    _log(entry, f"poisoned ({total if total is not None else 'no roll'})")
    ch["event"] = "new"
    if hours and now_h is None:
        ch["lines"].append(f"(no calendar: {', '.join(lasting)} stay until `afflictions.py cure {who} \"{p['names']['en']}\"` "
                           f"after {hours} hour(s))")
    return ch


def madness(data: dict, who: str, kind: str, now_h, rng: random.Random, d100: int | None = None) -> dict:
    kind = MADNESS_KINDS.get((kind or "").lower(), "")
    if not kind:
        raise AfflictionError("a madness is short, long or indefinite (breve, lunga, indefinita)")
    d100 = int(d100) if d100 else rng.randint(1, 100)
    if not 1 <= d100 <= 100:
        raise AfflictionError("--d100 between 1 and 100")
    effect = madness_effect(kind, d100)
    if not effect.get("en"):
        raise AfflictionError("the SRD's madness tables could not be read (lookup.py rule madness)")
    conds = madness_conditions(kind, d100, rng)
    minutes = rng.randint(1, 10) if kind == "short" else None
    hours = rng.randint(1, 10) * 10 if kind == "long" else None
    until = None if now_h is None or kind == "indefinite" else now_h + (hours or 1)
    entry = _new(data, who, "madness", kind, dict(MADNESS_NAMES[kind]), model="timed" if kind != "indefinite" else "indefinite",
                 d100=d100, minutes=minutes, hours=hours, until_h=until, effect=effect)
    ch = _changes(who)
    _set_conditions(data, entry, conds, ch)
    if minutes and conds:
        ch["add"] = [{"name": c, "duration_type": "rounds", "duration_remaining": 10 * minutes} for c in conds]
    _log(entry, f"d100 {d100}")
    ch["event"], ch["name"] = "new", MADNESS_NAMES[kind]["en"]
    ch["lines"].append(f"{who}: {MADNESS_NAMES[kind]['en'].lower()} (d100 {d100})"
                       + (f" for {minutes} minute(s)" if minutes else f" for {hours} hours" if hours else " until cured")
                       + f" — {effect['en']}" + (f" [{', '.join(conds)}]" if conds else ""))
    if kind != "indefinite" and now_h is None:
        ch["lines"].append(f"(no calendar: it ends with `afflictions.py cure {who} \"{MADNESS_NAMES[kind]['en']}\"`)")
    return ch


# ─── The course ───────────────────────────────────────────────────────────────

def describe_state(e: dict) -> str:
    m = e.get("model")
    if e.get("status") == "incubating":
        return "incubating"
    if m == "exhaustion":
        return f"exhaustion {e.get('level', 0)}, DC {e['dc']} each dawn"
    if m == "dc_drop":
        return f"save DC {e['dc']} each dawn, {e.get('failures', 0)} of 3 failures"
    if m == "worsen":
        return f"-{e.get('level', 0)} to sight" + (" (BLINDED)" if e.get("level", 0) >= e.get("max", 5) else "") \
               + (f", {e.get('doses', 0)} of {DISEASES.get(e['key'], {}).get('cure_doses', 3)} doses" if e.get("doses") else "")
    if m == "successes":
        return f"{e.get('successes', 0)} of {e.get('need', 7)} successes, DC {e['dc']} each dawn"
    if m == "midnight":
        return "strikes at midnight"
    if m == "indefinite":
        return "until cured"
    return "for " + (f"{e['minutes']} minute(s)" if e.get("minutes") else f"{e.get('hours')} hour(s)" if e.get("hours") else "a while")


def settle(data: dict, entry: dict, total: int, rng: random.Random) -> dict:
    """One saving throw of the course (a dawn's, or midnight's) came back: apply it."""
    ch = _changes(entry["who"])
    ch["name"] = entry["names"]["en"]
    entry["due"] = max(0, int(entry.get("due", 0)) - 1)
    entry["pending"] = None
    ok = total >= int(entry["dc"])
    model = entry.get("model")
    head = f"{entry['who']} — {entry['names']['en']}: {total} vs DC {entry['dc']}, "
    if model == "exhaustion":
        entry["level"] = int(entry.get("level", 1)) + (-1 if ok else 1)
        _log(entry, f"{'made' if ok else 'failed'} {total}")
        if entry["level"] < 1:
            out = end(data, entry, "the save brought it below one level")
            out["lines"] = [head + "made: it is over"]
            return out
        _set_conditions(data, entry, [f"Exhaustion {min(6, entry['level'])}"], ch)
        ch["event"] = "better" if ok else "worse"
        ch["lines"].append(head + ("made" if ok else "failed") + f": exhaustion {entry['level']}"
                           + (" — DEAD (exhaustion 6)" if entry["level"] >= 6 else ""))
    elif model == "dc_drop":
        if ok:
            drop = rng.randint(1, 6)
            entry["dc"] = int(entry["dc"]) - drop
            _log(entry, f"made {total}, DC -{drop}")
            if entry["dc"] <= 0:
                out = end(data, entry, "the DC reached 0")
                out["lines"] = [head + f"made: the DC drops by {drop} to 0 — it is over"]
                return out
            ch["event"] = "better"
            ch["lines"].append(head + f"made: the DC drops by {drop} to {entry['dc']}")
        else:
            entry["failures"] = int(entry.get("failures", 0)) + 1
            _log(entry, f"failed {total}")
            ch["event"] = "worse"
            ch["lines"].append(head + f"failed ({entry['failures']} of 3)")
            if entry["failures"] == 3:
                more = madness(data, entry["who"], "indefinite", None, rng)
                ch["add"] += more["add"]
                ch["lines"] += ["three failures: " + more["lines"][0]]
    elif model == "successes":
        if ok:
            entry["successes"] = int(entry.get("successes", 0)) + 1
            _log(entry, f"made {total}")
            if entry["successes"] >= int(entry.get("need") or 7):
                out = end(data, entry, f"{entry['successes']} successes")
                out["lines"] = [head + f"made: {entry['successes']} successes — it is over"]
                return out
            ch["event"] = "better"
            ch["lines"].append(head + f"made ({entry['successes']} of {entry.get('need') or 7})")
        else:
            dmg = roll(POISONS.get(entry["key"], {}).get("damage", "1d6"), rng)
            ch["damage"] = dmg
            _log(entry, f"failed {total}, {dmg} damage")
            ch["event"] = "worse"
            ch["lines"].append(head + f"failed: {dmg} poison damage (not healed until it is over)")
    elif model == "midnight":
        p = POISONS[entry["key"]]
        dmg = roll(p["damage"], rng)
        ch["damage"] = dmg if not ok else dmg // 2
        out = end(data, entry, "midnight came")
        out["damage"] = ch["damage"]
        out["event"] = "hit"
        out["lines"] = [head + ("made" if ok else "failed") + f": {ch['damage']} poison damage"]
        return out
    else:
        raise AfflictionError(f"{entry['names']['en']} has no saving throw to make")
    return ch


def dawn(data: dict, rng: random.Random, hour=None) -> list:
    """A dawn (at `hour` on the campaign clock, when there is one): saves fall due, sight rot worsens."""
    out = []
    for e in list(data["list"]):
        if e.get("status") != "active":
            continue
        if hour is not None and e.get("not_before_h") is not None and hour < e["not_before_h"]:
            continue
        if e.get("model") in ("exhaustion", "dc_drop", "successes"):
            e["due"] = int(e.get("due", 0)) + 1
        elif e.get("model") == "worsen":
            ch = _changes(e["who"])
            ch["name"] = e["names"]["en"]
            if e.get("treated"):
                e["treated"] = False
                _log(e, "treated: no worse")
                ch["lines"].append(f"{e['who']} — {e['names']['en']}: the ointment held it, still -{e['level']}")
            elif int(e.get("level", 1)) < int(e.get("max", 5)):
                e["level"] = int(e.get("level", 1)) + 1
                _log(e, f"worse: -{e['level']}")
                ch["event"] = "worse"
                if e["level"] >= int(e.get("max", 5)):
                    final = DISEASES.get(e["key"], {}).get("final")
                    if final:
                        _set_conditions(data, e, [final], ch)
                ch["lines"].append(f"{e['who']} — {e['names']['en']}: worse, {describe_state(e)}")
            if ch["lines"]:
                out.append(ch)
    return out


def treat(data: dict, entry: dict) -> dict:
    """A dose of the remedy before the night (sight rot's eyebright): no worse at dawn; enough doses cure it."""
    if entry.get("model") != "worsen":
        raise AfflictionError(f"{entry['names']['en']} has no ointment to treat it — `cure` when it is healed")
    need = DISEASES.get(entry["key"], {}).get("cure_doses", 3)
    entry["doses"] = int(entry.get("doses", 0)) + 1
    entry["treated"] = True
    if entry["doses"] >= need:
        return end(data, entry, f"{entry['doses']} doses of the remedy")
    ch = _changes(entry["who"])
    ch["name"] = entry["names"]["en"]
    ch["lines"].append(f"{entry['who']} — {entry['names']['en']}: a dose ({entry['doses']} of {need}); it will not worsen at dawn")
    return ch


def tick(data: dict, before_h: int, after_h: int, rng: random.Random) -> list:
    """The clock moved from before_h to after_h: onsets, midnights, dawns, ends. Changes to apply."""
    out = []
    for h in range(before_h + 1, after_h + 1):          # hour by hour: a dawn before the symptoms asks nothing
        for e in list(data["list"]):
            if e.get("status") == "incubating" and e.get("model") != "midnight" and e.get("onset_h") is not None \
                    and e["onset_h"] <= h:
                out.append(activate(data, e))
        if h % 24 == MIDNIGHT_HOUR:
            for e in data["list"]:
                if e.get("model") == "midnight" and e.get("status") == "incubating":
                    e["status"], e["due"] = "active", 1
        if h % 24 == DAWN_HOUR:
            out += dawn(data, rng, h)
        for e in list(data["list"]):
            if e.get("until_h") is not None and e["until_h"] <= h and e.get("model") == "timed":
                out.append(end(data, e, "its time is over"))
    return out


# ─── Views ────────────────────────────────────────────────────────────────────

def view_entry(e: dict, now_h=None) -> dict:
    left = None if e.get("until_h") is None or now_h is None else max(0, e["until_h"] - now_h)
    return {"id": e["id"], "who": e["who"], "kind": e["kind"], "key": e.get("key"), "names": e["names"],
            "model": e.get("model"), "status": e.get("status"), "level": e.get("level"), "dc": e.get("dc"),
            "failures": e.get("failures"), "successes": e.get("successes"), "need": e.get("need"),
            "doses": e.get("doses"), "max": e.get("max"), "hours_left": left, "minutes": e.get("minutes"),
            "save_due": bool(e.get("due")), "conditions": list(e.get("conditions") or []), "effect": e.get("effect") or {},
            "save": e.get("save", "CON")}


def public_view(data: dict, me: str = "", dm: bool = False, now_h=None) -> list:
    """A screen sees its own character's afflictions once they show; the DM sees all, incubating too."""
    out = []
    for e in data["list"]:
        if dm or (me and _norm(e["who"]) == _norm(me) and e.get("status") == "active"):
            out.append(view_entry(e, now_h))
    return out


def label(entry: dict, lang: str) -> str:
    ab = ABILITY_NAME.get(lang, ABILITY_NAME["en"]).get(entry.get("save", "CON"), entry.get("save", "CON"))
    name = entry["names"].get(lang) or entry["names"]["en"]
    return (f"TS {ab}: {name}" if lang == "it" else f"{ab} save: {name}")[:60]


# ─── The sheets ───────────────────────────────────────────────────────────────

def save_bonus(text: str, ability: str) -> int:
    """The saving throw bonus from the sheet's Saving Throws table, else the ability modifier."""
    for heading, paren in ((r"##\s+(?:Saving Throws|Tiri salvezza)", False), (r"##\s+(?:Ability Scores|Caratteristiche)", True)):
        m = re.search(heading + r"[^\n]*\n(.*?)(?=\n##\s|\Z)", text, re.S | re.I)
        if not m:
            continue
        rows = [r for r in m.group(1).splitlines() if r.strip().startswith("|")]
        if len(rows) < 3:
            continue
        cols = [c.strip().upper().strip("*") for c in rows[0].strip().strip("|").split("|")]
        cells = [c.strip() for c in rows[2].strip().strip("|").split("|")]
        for name in (ability, ABILITY_IT.get(ability, ability)):
            if name in cols and cols.index(name) < len(cells):
                cell = cells[cols.index(name)]
                found = re.search(r"\(([+\-−]?\d+)\)", cell) if paren else re.search(r"([+\-−]?\d+)", cell)
                if found:
                    return int(found.group(1).replace("−", "-"))
    import group_check
    return group_check._ability_mod(text, ability)


def sheet_of(name: str, camp: str) -> tuple:
    """(the name as the sheet writes it, the sheet's text)."""
    import inventory
    try:
        path = inventory.find_sheet(name, camp)
        return inventory.display_name(path), inventory._read(path)
    except inventory.InventoryError:
        raise AfflictionError(f"no sheet for {name}")


# ─── Time (calendar.py) ───────────────────────────────────────────────────────

def now_hours(camp: str):
    """Hours since the start of the campaign's calendar, or None when it keeps none."""
    try:
        import timeline
        lib, cal = timeline.calendar_of(camp)
        if not lib:
            return None
        return lib._abs_day(cal) * 24 + int(cal.get("hour", 8))
    except Exception:
        return None


def clock_moved(campaign: str, before_h: int, after_h: int) -> list:
    """calendar.py moved the clock: run the course, put it on the display. Lines for the DM; nothing without the file."""
    try:
        path = data_path(campaign)
    except Exception:
        return []
    if not os.path.isfile(path) or after_h <= before_h:
        return []
    data = load(path)
    changes = tick(data, before_h, after_h, random.Random())
    save(path, data)
    lines = []
    active = campaign == _active_campaign()
    for ch in changes:
        lines += ch["lines"]
        if active:
            if not _apply(ch):
                lines.append(f"  (display off: put it on {ch['who']}'s sheet yourself)")
    due = [e for e in data["list"] if e.get("due")]
    if due:
        asked = _post("/afflictions/ask", {}) if active else None
        who = ", ".join(f"{e['who']} ({e['names']['en']}, DC {e['dc']})" for e in due)
        lines.append(f"saves due: {who} — " + ("sent to the phones; the display applies them as they come back"
                                               if asked else "the display is off: afflictions.py ask when it is on, "
                                               "or afflictions.py resolve NAME AFFLICTION --total N"))
    return lines


# ─── Telling the display and the DM ───────────────────────────────────────────

def _post(path: str, body: dict):
    """POST to the display; the parsed answer, or None when it is not running."""
    try:
        import send
        import urllib.request
        headers = {"Content-Type": "application/json"}
        token = send._read_token()
        if token:
            headers["X-DND-Token"] = token
        req = urllib.request.Request(send.BASE_URL + path, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=5, context=send._SSL_CTX) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except Exception:
        return None


def _apply(ch: dict) -> bool:
    """Put the conditions and the damage on the display, and tell the screens. False when it is off."""
    body = {"character": ch["who"], "add": ch["add"], "remove": ch["remove"], "damage": ch["damage"],
            "event": ch["event"], "name": ch["name"]}
    return _post("/afflictions/apply", body) is not None


def _notes_file() -> str:
    from runtime_paths import rt
    return rt("affliction_notes.json")


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


def note_line(ch: dict) -> str:
    return "[[AFFLICTION: " + " ".join(ch["lines"]) + (
        " — applied on the display; write it on the sheet" if ch["add"] or ch["remove"] or ch["damage"] else "") + "]]"


def _ask(name: str, mod: int, label_text: str, dc: int, timeout: int):
    """A save on a phone; its total, or None when nobody rolled. Raises when the display is off."""
    import group_check
    status, body = group_check._request("/dice-request", {"characters": [name], "spec": "1d20", "modifier": mod,
                                                          "advantage": "normal", "label": label_text, "dc": dc})
    if status != 200 or not isinstance(body, dict):
        raise AfflictionError("the display is not running — roll it yourself and pass --total" if status == 0
                              else f"the display said HTTP {status}")
    rid = body["request_id"]
    deadline = time.time() + max(5, timeout)
    while time.time() < deadline:
        status, st = group_check._request(f"/dice-request/{rid}")
        if status == 200 and isinstance(st, dict) and st.get("complete"):
            res = next((r for r in st.get("results") or [] if str(r.get("character", "")).lower() == name.lower()), None)
            return int(res["total"]) if res else None
        time.sleep(0.5)
    return None


def _story(line: str) -> None:
    try:
        subprocess.run([sys.executable, os.path.join(_HERE, "send.py"), "--dice"], input=line, text=True,
                       encoding="utf-8", capture_output=True, timeout=15, env=dict(os.environ, PYTHONUTF8="1"))
    except Exception:
        pass


# ─── Command line ─────────────────────────────────────────────────────────────

def _finish(ch: dict, active: bool) -> None:
    for line in ch["lines"]:
        print(line)
    if ch["add"] or ch["remove"] or ch["damage"]:
        shown = active and _apply(ch)
        added = [c if isinstance(c, str) else c["name"] for c in ch["add"]]
        # the other exhaustion levels are cleared by the way: say only a level that really went
        gone = [c for c in ch["remove"] if c not in EXHAUSTION or not any(a in EXHAUSTION for a in added)]
        what = ", ".join([f"+{c}" for c in added] + [f"-{c}" for c in gone]
                         + ([f"{ch['damage']} damage"] if ch["damage"] else []))
        print(("  on the display: " if shown else "  NOT on the display (it is off): ") + what
              + " — write it on the sheet")
    elif active and ch["event"] in ("new", "cured", "better", "worse"):
        _apply(ch)


def main(argv: list | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    ap = argparse.ArgumentParser(description="Diseases, poisons and madness that run their course.")
    ap.add_argument("--campaign", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("expose", help="the save against a disease or a poison, then its effect")
    p.add_argument("who")
    p.add_argument("what")
    p.add_argument("--total", type=int, help="the save is already rolled")
    p.add_argument("--timeout", type=int, default=180)
    p = sub.add_parser("add", help="the save already failed: infected or poisoned")
    p.add_argument("who")
    p.add_argument("what")
    p = sub.add_parser("custom", help="a disease of your own")
    p.add_argument("who")
    p.add_argument("name")
    p.add_argument("--dc", type=int, required=True)
    p.add_argument("--onset", default="", help="2d, 6h (default: at once)")
    p.add_argument("--model", default="exhaustion", help="exhaustion | dc_drop | successes | worsen")
    p.add_argument("--need", type=int, default=3, help="successes needed (model successes)")
    p.add_argument("--save", default="CON")
    p.add_argument("--effect", default="")
    p = sub.add_parser("madness", help="short, long or indefinite madness")
    p.add_argument("who")
    p.add_argument("kind")
    p.add_argument("--d100", type=int)
    p.add_argument("--save", default="", help="WIS or CHA: a save to resist it first")
    p.add_argument("--dc", type=int)
    p.add_argument("--total", type=int)
    p.add_argument("--timeout", type=int, default=180)
    for cmd in ("treat", "cure"):
        p = sub.add_parser(cmd)
        p.add_argument("who")
        p.add_argument("what", nargs="?", default="")
        if cmd == "cure":
            p.add_argument("--all", action="store_true")
    p = sub.add_parser("resolve", help="a save of the course rolled without the phone")
    p.add_argument("who")
    p.add_argument("what", nargs="?", default="")
    p.add_argument("--total", type=int, required=True)
    p = sub.add_parser("show")
    p.add_argument("--who", default="")
    for cmd in ("ask", "dawn", "list"):
        sub.add_parser(cmd)
    args = ap.parse_args(argv)

    if args.cmd == "list":
        print("Diseases: " + "; ".join(f"{d['names']['en']} / {d['names']['it']} (DC {d['dc']})" for d in DISEASES.values()))
        print("Poisons: " + "; ".join(f"{d['names']['en']} / {d['names']['it']} ({d['type']}, DC {d['dc']})" for d in POISONS.values()))
        print("Madness: short (1d10 minutes), long (1d10 × 10 hours), indefinite (until cured)")
        return 0
    try:
        camp = _campaign(args.campaign)
        path = data_path(camp)
        data = load(path)
        active = camp == _active_campaign()
        rng = random.Random()
        now_h = now_hours(camp)
        import paths
        lang = "it" if paths.campaign_language(camp) == "it" else "en"
        if args.cmd == "show":
            rows = [e for e in data["list"] if not args.who or _norm(e["who"]) == _norm(args.who)]
            for e in rows:
                print(f"{e['who']}: {e['names']['en']} ({e['kind']}) — {describe_state(e)}"
                      + (f"; {e['hours'] if e.get('until_h') is None or now_h is None else e['until_h'] - now_h} h left"
                         if e.get("until_h") is not None or (e.get("hours") and e.get("model") == "timed") else "")
                      + (" — SAVE DUE" if e.get("due") else "") + (f" [{', '.join(e['conditions'])}]" if e.get("conditions") else ""))
            if not rows:
                print("no afflictions")
            return 0
        if args.cmd == "ask":
            due = [e for e in data["list"] if e.get("due")]
            if not due:
                print("no save is due")
                return 0
            if _post("/afflictions/ask", {"again": True}) is None:
                raise AfflictionError("the display is not running — afflictions.py resolve NAME AFFLICTION --total N")
            print("sent to the phones: " + ", ".join(f"{e['who']} ({e['names']['en']})" for e in due))
            return 0
        ch = None
        if args.cmd in ("expose", "add"):
            who, _text = sheet_of(args.who, camp)
            kind, key = find(args.what)
            total = None
            if args.cmd == "expose":
                dc = (DISEASES if kind == "disease" else POISONS)[key]["dc"]
                if POISONS.get(key, {}).get("model") == "midnight":
                    total = None
                elif args.total is not None:
                    total = args.total
                else:
                    total = _ask(who, save_bonus(_text, "CON"), label({"names": (DISEASES if kind == "disease" else POISONS)[key]["names"],
                                                                       "save": "CON"}, lang), dc, args.timeout)
                    if total is None:
                        raise AfflictionError(f"{who} did not roll in time — nothing changed; ask again or pass --total")
                if kind == "disease" and total is not None and total >= dc:
                    print(f"{who} makes the DC {dc} Constitution save ({total}): not infected")
                    return 0
            if kind == "disease":
                _entry, ch = infect(data, who, key, now_h, rng)
            else:
                ch = poison(data, who, key, total, now_h, rng)
        elif args.cmd == "custom":
            who, _text = sheet_of(args.who, camp)
            _entry, ch = custom(data, who, args.name, now_h, args.dc, args.onset, args.model, args.effect, args.save, args.need)
        elif args.cmd == "madness":
            who, _text = sheet_of(args.who, camp)
            if args.save:
                ab = args.save.upper()
                ab = next((k for k, v in ABILITY_IT.items() if v == ab), ab)
                if ab not in ABILITIES or not args.dc:
                    raise AfflictionError("--save WIS|CHA needs --dc")
                total = args.total if args.total is not None else _ask(
                    who, save_bonus(_text, ab), label({"names": MADNESS_NAMES[MADNESS_KINDS.get(args.kind.lower(), "short")],
                                                       "save": ab}, lang), args.dc, args.timeout)
                if total is None:
                    raise AfflictionError(f"{who} did not roll in time — nothing changed")
                if total >= args.dc:
                    print(f"{who} makes the DC {args.dc} save ({total}): the mind holds")
                    return 0
            ch = madness(data, who, args.kind, now_h, rng, args.d100)
        elif args.cmd == "treat":
            ch = treat(data, find_entry(data, args.who, args.what))
        elif args.cmd == "cure":
            rows = entries_of(data, args.who) if args.all else [find_entry(data, args.who, args.what)]
            if not rows:
                raise AfflictionError(f"{args.who} has no affliction")
            for e in rows:
                if e["kind"] == "madness" and e["key"] == "indefinite":
                    print("  (an indefinite madness needs greater restoration or stronger magic)")
                _finish(end(data, e, "cured"), active)
        elif args.cmd == "resolve":
            e = find_entry(data, args.who, args.what)
            if not e.get("due") and e.get("model") not in ("midnight",):
                print("  (no save was due — counted anyway)")
                e["due"] = 1
            ch = settle(data, e, args.total, rng)
        elif args.cmd == "dawn":
            for c in dawn(data, rng):
                _finish(c, active)
            due = [e for e in data["list"] if e.get("due")]
            for e in data["list"]:
                if e.get("status") == "incubating" and e.get("model") != "midnight" and e.get("onset_h") is None:
                    _finish(activate(data, e), active)
            save(path, data)
            if due:
                sent = active and _post("/afflictions/ask", {}) is not None
                print("saves due: " + ", ".join(f"{e['who']} ({e['names']['en']}, DC {e['dc']})" for e in due)
                      + (" — sent to the phones" if sent else " — the display is off: resolve --total"))
            return 0
        save(path, data)
        if ch is not None:
            _finish(ch, active)
    except AfflictionError as e:
        print(f"afflictions: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
