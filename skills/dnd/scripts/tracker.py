#!/usr/bin/env python3
"""
tracker.py — session-state tracker for conditions, concentration, effects, and death saves

Stores state per-campaign. Outputs formatted status to terminal and pushes
condition updates to the display companion sidebar (via push_stats.py).

Usage:
    CAMPAIGN=my-campaign

    # Conditions (5e SRD: blinded, charmed, deafened, exhausted, frightened,
    #   grappled, incapacitated, invisible, paralyzed, petrified, poisoned,
    #   prone, restrained, stunned, unconscious)
    python3 tracker.py -c $CAMPAIGN condition add <entity> <condition> [duration]   # 3r / 10m / 1h: ends by itself
    python3 tracker.py -c $CAMPAIGN condition remove <entity> <condition>
    python3 tracker.py -c $CAMPAIGN condition clear <entity>

    # Concentration (one spell at a time per caster; auto-clears previous)
    python3 tracker.py -c $CAMPAIGN concentrate <entity> "<spell name>"
    python3 tracker.py -c $CAMPAIGN concentrate <entity> break

    # Timed effects — duration formats: 10r (rounds), 60m (minutes), 8h (hours), indef
    python3 tracker.py -c $CAMPAIGN effect start <entity> "<spell>" <duration> [conc]
    python3 tracker.py -c $CAMPAIGN effect end   <entity> "<spell>"
    python3 tracker.py -c $CAMPAIGN effect tick  <entity>   # call on that entity's turn start

    # Hit points of monsters and NPCs (PCs live on their sheets and the display) — shown on the DM's screen
    python3 tracker.py -c $CAMPAIGN hp <entity> <current> [--max N] [--ac N]

    # (at 0 HP, from hp or hit, the creature leaves the display's turn order: it takes no more turns)
    # Damage to a monster or an NPC: takes it off the HP above (when known) and, when it is concentrating,
    # rolls the CON save — DC 10 or half the damage — and breaks it on a failure (at 0 HP it just ends)
    python3 tracker.py -c $CAMPAIGN hit <entity> <damage> [--con +3] [--adv|--dis] [--down]

    # Legendary creatures: actions per round (back at the start of its own turn), Legendary Resistance
    # per day, lair actions on initiative count 20 — counted here, announced when the turn moves
    # (push_stats.py --turn-current) and shown on the DM's screen. --as reads the numbers from the SRD.
    python3 tracker.py -c $CAMPAIGN legendary <entity> set [--actions 3] [--resist 3] [--lair] [--as "Adult Red Dragon"]
    python3 tracker.py -c $CAMPAIGN legendary <entity> use [--cost 2]     # a legendary action, at the end of another's turn
    python3 tracker.py -c $CAMPAIGN legendary <entity> resist             # Legendary Resistance: a failed save becomes a success
    python3 tracker.py -c $CAMPAIGN legendary <entity> lair "<action>"    # the lair action it used (not the same one twice running)
    python3 tracker.py -c $CAMPAIGN legendary <entity> reset              # a new day: Legendary Resistance back
    python3 tracker.py -c $CAMPAIGN legendary <entity> off

    # Morale (DMG 2014 p. 273, optional: only with `morale: on` in state.md's Session Flags). A creature
    # may flee the first time it drops to half its HP; a group when its leader falls or half of it is
    # down. hit / hp say when a check is due; roll it here (Wisdom save DC 10).
    python3 tracker.py -c $CAMPAIGN morale <entity> set --group Banditi [--leader] [--as "Bandit Captain"] [--wis +1] [--cha +2] [--fearless]
    python3 tracker.py -c $CAMPAIGN morale <entity|group> roll [--dis | --fail]   # overwhelming odds: --dis, or --fail
    python3 tracker.py -c $CAMPAIGN morale <entity> off

    # Death saves (PC only — 3 successes = stable, 3 failures = dead)
    python3 tracker.py -c $CAMPAIGN saves <entity> success
    python3 tracker.py -c $CAMPAIGN saves <entity> failure
    python3 tracker.py -c $CAMPAIGN saves <entity> stable   # manually mark stable
    python3 tracker.py -c $CAMPAIGN saves <entity> reset    # regain consciousness

    # Status display
    python3 tracker.py -c $CAMPAIGN status [entity]   # all entities or one

    # Clear (end of encounter — wipes conditions/concentrations/effects; preserves saves). It also gives
    # back half the ammunition shot in the fight (ammo.py recover; `ammo.py recover --none` before it if lost)
    python3 tracker.py -c $CAMPAIGN clear [--all]     # --all also clears saves
"""

import json
import os
import sys
import subprocess
import argparse
import time
from datetime import datetime, timezone
from paths import find_campaign as _find_campaign, display_dir as _display_dir

PUSH_STATS = str(_display_dir() / "push_stats.py")
SEND_PY    = str(_display_dir() / "send.py")

# 5e condition colours for display pills
CONDITION_COLOURS = {
    "unconscious":    "danger",
    "paralyzed":      "danger",
    "petrified":      "danger",
    "stunned":        "danger",
    "incapacitated":  "warn",
    "frightened":     "warn",
    "poisoned":       "warn",
    "charmed":        "warn",
    "exhausted":      "warn",
    "grappled":       "info",
    "restrained":     "info",
    "prone":          "info",
    "blinded":        "info",
    "deafened":       "info",
    "invisible":      "buff",
}


def _state_path(campaign: str) -> str:
    d = str(_find_campaign(campaign))
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "tracker.json")


def _load(campaign: str) -> dict:
    path = _state_path(campaign)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(campaign: str, state: dict) -> None:
    with open(_state_path(campaign), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def _entity(state: dict, name: str) -> dict:
    """Get or create an entity entry."""
    key = name.lower()
    if key not in state:
        state[key] = {
            "name": name,
            "conditions": [],
            "concentration": None,
            "effects": [],
            "death_saves": {"successes": 0, "failures": 0, "stable": False},
        }
    # Backfill effects key for older entries
    state[key].setdefault("effects", [])
    state[key].setdefault("condition_timers", {})
    return state[key]


def _parse_duration(dur_str: str) -> "dict | None":
    """Parse a duration string into an effect dict fragment.
    Formats: 10r (rounds), 60m (minutes), 8h (hours), indef (indefinite).
    Returns None on bad input.
    """
    d = dur_str.strip().lower()
    if d.endswith("r"):
        try:
            return {"duration_type": "rounds", "duration_remaining": int(d[:-1])}
        except ValueError:
            return None
    elif d.endswith("m"):
        try:
            secs = int(d[:-1]) * 60
            return {"duration_type": "minutes", "duration_seconds": secs, "started_at": time.time()}
        except ValueError:
            return None
    elif d.endswith("h"):
        try:
            secs = int(d[:-1]) * 3600
            return {"duration_type": "hours", "duration_seconds": secs, "started_at": time.time()}
        except ValueError:
            return None
    elif d == "indef":
        return {"duration_type": "indefinite"}
    return None


def _fmt_effect(eff: dict) -> str:
    """Return a short human-readable remaining-duration string."""
    dt = eff.get("duration_type", "indefinite")
    if dt == "rounds":
        r = eff.get("duration_remaining", 0)
        return f"{r} rnd"
    elif dt in ("minutes", "hours"):
        elapsed = time.time() - eff.get("started_at", time.time())
        remaining = max(0, eff.get("duration_seconds", 0) - elapsed)
        if remaining <= 0:
            return "expired"
        m, s = divmod(int(remaining), 60)
        if m >= 60:
            h, m = divmod(m, 60)
            return f"{h}h {m}m"
        return f"{m}:{s:02d}"
    return "∞"


def _push_conditions(entity_name: str, conditions: list[str]) -> None:
    """Push condition list to display sidebar via push_stats.py."""
    cond_str = ",".join(conditions)
    try:
        subprocess.run(
            [sys.executable, PUSH_STATS, "--player", entity_name,
             "--conditions", cond_str],
            capture_output=True, timeout=3,
        )
    except Exception:
        pass  # Display not running — fail silently


def _push_timed_condition(entity_name: str, condition: str, duration: str) -> None:
    """Add one condition with its duration on the display (the others keep their own)."""
    try:
        subprocess.run(
            [sys.executable, PUSH_STATS, "--player", entity_name,
             "--conditions-add", f"{condition}:{duration}"],
            capture_output=True, timeout=3,
        )
    except Exception:
        pass  # Display not running — fail silently


def _push_death_save(entity_name: str, action: str) -> None:
    """Mirror a death-save step on the display's card. Silent when it is not running."""
    try:
        subprocess.run(
            [sys.executable, PUSH_STATS, "--player", entity_name, "--death-save", action],
            capture_output=True, timeout=3,
        )
    except Exception:
        pass


def _push_turn_remove(entity_name: str) -> None:
    """A creature at 0 HP takes no more turns: out of the display's turn order. Silent when it is not running."""
    try:
        subprocess.run([sys.executable, PUSH_STATS, "--turn-remove", entity_name],
                       capture_output=True, timeout=3)
    except Exception:
        pass


def _send_announce(msg: str) -> None:
    """Send a one-line announcement to the display companion."""
    try:
        # run(), not Popen(): Popen takes neither capture_output nor timeout,
        # and the TypeError it raised was swallowed below, so nothing was sent.
        subprocess.run([sys.executable, SEND_PY, "--dice"],
                       input=msg.encode("utf-8"), capture_output=True, timeout=3)
    except Exception:
        pass


# ─── Commands ────────────────────────────────────────────────────────────────

def cmd_effect(campaign: str, action: str, entity_name: str,
               spell: str = "", duration: str = "", is_conc: bool = False) -> None:
    state = _load(campaign)
    ent   = _entity(state, entity_name)

    if action == "start":
        if not spell or not duration:
            print("  error: effect start requires <entity> <spell> <duration>")
            return
        dur = _parse_duration(duration)
        if dur is None:
            print(f"  error: bad duration '{duration}' — use 10r / 60m / 8h / indef")
            return

        effect = {"name": spell, "concentration": is_conc, **dur}

        # Replace any existing effect with same name
        ent["effects"] = [e for e in ent["effects"] if e["name"].lower() != spell.lower()]
        ent["effects"].append(effect)

        # Mirror concentration into the concentrate field
        if is_conc:
            old = ent.get("concentration")
            ent["concentration"] = spell
            if old and old.lower() != spell.lower():
                print(f"  {entity_name}: dropped concentration on '{old}'")
            _send_announce(f"{entity_name} — concentrating on {spell}")

        rem = _fmt_effect(effect)
        conc_tag = " [conc]" if is_conc else ""
        print(f"  + {entity_name}: {spell}{conc_tag} · {rem}")
        _save(campaign, state)

    elif action == "end":
        if not spell:
            print("  error: effect end requires <entity> <spell>")
            return
        before = len(ent["effects"])
        removed = [e for e in ent["effects"] if e["name"].lower() == spell.lower()]
        ent["effects"] = [e for e in ent["effects"] if e["name"].lower() != spell.lower()]
        if not removed:
            print(f"  ({entity_name} has no active effect '{spell}')")
            _save(campaign, state)
            return
        was_conc = any(e.get("concentration") for e in removed)
        if was_conc and (ent.get("concentration") or "").lower() == spell.lower():
            ent["concentration"] = None
            _send_announce(f"{entity_name} — concentration on {spell} ends")
            print(f"  - {entity_name}: {spell} ends (concentration broken)")
        else:
            _send_announce(f"{entity_name} — {spell} ends")
            print(f"  - {entity_name}: {spell} ends")
        _save(campaign, state)

    elif action == "tick":
        # Advance one round for this entity — decrement rounds, flag expired
        effects = ent.get("effects", [])
        kept, expired = [], []
        time_expired = []

        for e in effects:
            dt = e.get("duration_type")
            if dt == "rounds":
                e = dict(e)
                e["duration_remaining"] = max(0, e.get("duration_remaining", 1) - 1)
                if e["duration_remaining"] <= 0:
                    expired.append(e)
                else:
                    kept.append(e)
            elif dt in ("minutes", "hours"):
                elapsed = time.time() - e.get("started_at", time.time())
                remaining = e.get("duration_seconds", 0) - elapsed
                if remaining <= 0:
                    time_expired.append(e)
                else:
                    kept.append(e)
            else:
                kept.append(e)

        all_expired = expired + time_expired
        ent["effects"] = kept

        # Conditions with a duration run on the same turns
        timers = ent.get("condition_timers", {})
        ended = []
        for cond in list(timers):
            t = timers[cond]
            if t.get("duration_type") == "rounds":
                t["duration_remaining"] = max(0, t.get("duration_remaining", 1) - 1)
                if t["duration_remaining"] <= 0:
                    ended.append(cond)
            elif time.time() - t.get("started_at", time.time()) >= t.get("duration_seconds", 0):
                ended.append(cond)
        for cond in ended:
            timers.pop(cond, None)
            ent["conditions"] = [c for c in ent["conditions"] if c != cond]
            print(f"  ! {entity_name}: {cond.capitalize()} ENDS (duration over)")
        if ended:
            _push_conditions(entity_name, ent["conditions"])
        left = [f"{c.capitalize()} · {_fmt_effect(t)}" for c, t in timers.items()]
        if left:
            print(f"  {entity_name} — conditions: {', '.join(left)}")

        if not all_expired and not kept:
            # Nothing to report
            _save(campaign, state)
            return

        # Print tick summary
        if kept:
            lines = [f"⧗ {e['name']} · {_fmt_effect(e)}" for e in kept]
            print(f"  {entity_name} — effects: {', '.join(lines)}")

        for e in all_expired:
            was_conc = e.get("concentration", False)
            spell_name = e["name"]
            if was_conc and (ent.get("concentration") or "").lower() == spell_name.lower():
                ent["concentration"] = None
                _send_announce(f"{entity_name} — {spell_name} expired (concentration ends)")
                print(f"  ! {entity_name}: {spell_name} EXPIRED — concentration ends")
            else:
                _send_announce(f"{entity_name} — {spell_name} expired")
                print(f"  ! {entity_name}: {spell_name} EXPIRED")

        _save(campaign, state)

    else:
        print(f"  error: unknown effect action '{action}' — use start / end / tick")


def cmd_condition(campaign: str, entity_name: str, action: str, condition: str = "",
                  duration: str = "") -> None:
    state = _load(campaign)
    ent   = _entity(state, entity_name)
    conds = ent["conditions"]
    timers = ent["condition_timers"]

    if action == "add":
        cond = condition.lower()
        timer = None
        if duration:
            timer = _parse_duration(duration)
            if timer is None or timer.get("duration_type") == "indefinite":
                print(f"  error: bad duration '{duration}' — use 3r / 10m / 1h, or leave it out")
                return
        if cond not in conds or timer:
            if cond not in conds:
                conds.append(cond)
            if timer:
                timers[cond] = timer
                _push_timed_condition(entity_name, cond, duration.strip().lower())
            else:
                _push_conditions(entity_name, conds)
            _send_announce(f"{entity_name} → {cond.capitalize()}")
            print(f"  + {entity_name}: {cond}" + (f" · {_fmt_effect(timer)}" if timer else ""))
        else:
            print(f"  (already has {cond})")

    elif action == "remove":
        cond = condition.lower()
        if cond in conds:
            conds.remove(cond)
            timers.pop(cond, None)
            _push_conditions(entity_name, conds)
            _send_announce(f"{entity_name} — {cond.capitalize()} ends")
            print(f"  - {entity_name}: {cond} removed")
        else:
            print(f"  ({entity_name} does not have {cond})")

    elif action == "clear":
        if conds:
            _send_announce(f"{entity_name} — all conditions cleared")
        conds.clear()
        timers.clear()
        _push_conditions(entity_name, [])
        print(f"  {entity_name}: conditions cleared")

    ent["conditions"] = conds
    _save(campaign, state)


def cmd_concentrate(campaign: str, entity_name: str, spell_or_break: str) -> None:
    state = _load(campaign)
    ent   = _entity(state, entity_name)

    if spell_or_break.lower() == "break":
        old = ent.get("concentration")
        ent["concentration"] = None
        if old:
            _send_announce(f"{entity_name} — concentration on {old} broken")
            print(f"  {entity_name}: concentration on '{old}' broken")
        else:
            print(f"  {entity_name}: was not concentrating")
    else:
        spell = spell_or_break
        old   = ent.get("concentration")
        ent["concentration"] = spell
        if old and old != spell:
            _send_announce(f"{entity_name} — {old} ends, concentrating on {spell}")
            print(f"  {entity_name}: dropped '{old}', now concentrating on '{spell}'")
        else:
            _send_announce(f"{entity_name} — concentrating on {spell}")
            print(f"  {entity_name}: concentrating on '{spell}'")

    _save(campaign, state)


def concentration_dc(damage: int) -> int:
    """PHB 2014 p. 203: DC 10 or half the damage taken, whichever is higher."""
    return max(10, int(damage) // 2)


def cmd_hp(campaign: str, entity_name: str, current: int, maximum=None, ac=None) -> dict:
    """Set a monster's or an NPC's hit points (and AC): the DM's screen shows them, `hit` takes damage off."""
    state = _load(campaign)
    ent = _entity(state, entity_name)
    ent["hp"] = max(0, int(current))
    if maximum is not None:
        ent["max_hp"] = max(1, int(maximum))
    else:
        ent.setdefault("max_hp", max(1, ent["hp"]))
    if ac is not None:
        ent["ac"] = int(ac)
    print(f"  {entity_name}: {ent['hp']}/{ent['max_hp']} HP" + (f", AC {ent['ac']}" if ent.get("ac") is not None else ""))
    for line in morale_after_damage(campaign, state, ent):
        print("  " + line)
    _save(campaign, state)
    if ent["hp"] == 0:
        _push_turn_remove(entity_name)
        print(f"  ☠ {entity_name}: out of the turn order")
    return ent


def cmd_hit(campaign: str, entity_name: str, damage: int, con: int = 0, adv: str = "normal",
            down: bool = False, rng=None) -> dict:
    """Damage to a monster or an NPC: off its HP when known, and a concentration save when it concentrates."""
    import random
    rng = rng or random.SystemRandom()
    state = _load(campaign)
    ent = _entity(state, entity_name)
    if isinstance(ent.get("hp"), int) and damage > 0:
        ent["hp"] = max(0, ent["hp"] - damage)
        down = down or ent["hp"] == 0
        print(f"  {entity_name}: {ent['hp']}/{ent.get('max_hp', '?')} HP" + ("  — DOWN" if ent["hp"] == 0 else ""))
        for line in morale_after_damage(campaign, state, ent):
            print("  " + line)
        _save(campaign, state)
        if ent["hp"] == 0:
            _push_turn_remove(entity_name)
            print(f"  ☠ {entity_name}: out of the turn order")
    spell = ent.get("concentration")
    if not spell:
        print(f"  {entity_name}: not concentrating — nothing to roll")
        return {"concentrating": False}
    if damage < 1:
        print(f"  {entity_name}: no damage, no save")
        return {"concentrating": True, "kept": True}
    if down:
        kept, line = False, f"  {entity_name}: down to 0 HP — concentration on '{spell}' ends"
        result = {"concentrating": True, "kept": False, "down": True}
    else:
        dc = concentration_dc(damage)
        rolls = [rng.randint(1, 20)] + ([rng.randint(1, 20)] if adv in ("advantage", "disadvantage") else [])
        d20 = max(rolls) if adv == "advantage" else min(rolls) if adv == "disadvantage" else rolls[0]
        total = d20 + con
        kept = total >= dc
        shown = f"[{', '.join(map(str, rolls))}] → {d20}" if len(rolls) > 1 else str(d20)
        line = (f"  {entity_name}: CON save {shown}{con:+d} = {total} vs DC {dc} — "
                + (f"keeps concentrating on '{spell}'" if kept else f"FAILS: concentration on '{spell}' broken"))
        result = {"concentrating": True, "kept": kept, "total": total, "dc": dc, "rolls": rolls}
    print(line)
    if not kept:
        ent["concentration"] = None
        ent["effects"] = [e for e in ent.get("effects", [])
                          if not (e.get("concentration") and e.get("name", "").lower() == spell.lower())]
        _send_announce(f"{entity_name} — concentration on {spell} broken")
        _save(campaign, state)
    return result


# ─── Legendary creatures ──────────────────────────────────────────────────────

def srd_legendary(name: str, exact: bool = False) -> dict:
    """What the SRD says of a creature: {'actions': N, 'resist': N, 'options': [...]} (only what it says).

    `exact`: only a creature with exactly that name (a tracker entity called "Drago" is not "Adult Red Dragon")."""
    import re
    try:
        import encounter
        rec = encounter.find_monster(name, encounter.load_monsters())
    except Exception:
        return {}
    if exact and name.strip().lower() not in {str(rec.get("name", "")).lower(), *(str(a).replace("-", " ").lower()
                                                                                    for a in rec.get("_alt_names") or [])}:
        return {}
    text = str(rec.get("description") or "")
    out = {}
    options = []
    for m in re.finditer(r"^Legendary — ([^:\n]+?)(?: \(Costs (\d) Actions\))?:", text, re.M):
        options.append({"name": m.group(1).strip(), "cost": int(m.group(2) or 1)})
    if options:
        # The SRD's legendary creatures all take 3 legendary actions; the dataset keeps the options, not the number.
        m = re.search(r"can take (\d+) legendary actions", text, re.I)
        out["actions"] = int(m.group(1)) if m else 3
        out["options"] = options
    if re.search(r"^Legendary Resistance", text, re.M):
        m = re.search(r"Legendary Resistance \((\d+)/Day\)", text, re.I)
        out["resist"] = int(m.group(1)) if m else 3
    return out


def _fmt_legendary(leg: dict) -> str:
    bits = []
    if leg.get("actions"):
        bits.append(f"legendary actions {leg.get('left', 0)}/{leg['actions']}")
    if leg.get("resist"):
        bits.append(f"Legendary Resistance {leg.get('resist_left', 0)}/{leg['resist']}")
    if leg.get("lair"):
        bits.append("lair actions on 20" + (f" (last: {leg['lair_last']})" if leg.get("lair_last") else ""))
    return ", ".join(bits) or "nothing legendary"


def cmd_legendary(campaign: str, entity_name: str, action: str, actions=None, resist=None, lair=None,
                  cost: int = 1, srd: str = "", value: str = "") -> dict:
    """Legendary actions, Legendary Resistance and lair actions of one creature. Returns its record."""
    state = _load(campaign)
    ent = _entity(state, entity_name)
    leg = ent.get("legendary") or {}
    if action == "set":
        found = srd_legendary(srd) if srd else srd_legendary(entity_name, exact=True)
        n = actions if actions is not None else leg.get("actions", found.get("actions", 0))
        r = resist if resist is not None else leg.get("resist", found.get("resist", 0))
        leg = {"actions": max(0, int(n)), "left": max(0, int(n)), "resist": max(0, int(r)), "resist_left": max(0, int(r)),
               "lair": bool(lair) if lair is not None else bool(leg.get("lair")), "lair_last": leg.get("lair_last", ""),
               "options": found.get("options") or leg.get("options") or []}
        if not (leg["actions"] or leg["resist"] or leg["lair"]):
            raise SystemExit(f"  {entity_name}: nothing legendary to track — give --actions / --resist / --lair"
                             + ("" if found or not srd else f" (the SRD has no legendary numbers for {srd!r})"))
        msg = f"  {entity_name}: {_fmt_legendary(leg)}"
    elif not leg:
        raise SystemExit(f"  {entity_name}: not set up — tracker.py legendary {entity_name!r} set …")
    elif action == "use":
        named = next((o for o in leg.get("options") or [] if value and o["name"].lower() == value.lower()), None)
        cost = max(1, int(named["cost"] if named else cost))
        if leg.get("left", 0) < cost:
            raise SystemExit(f"  {entity_name}: only {leg.get('left', 0)} legendary action(s) left this round — it cannot pay {cost}")
        leg["left"] -= cost
        msg = f"  {entity_name}: legendary action ({cost}) — {leg['left']}/{leg['actions']} left until its turn"
    elif action == "resist":
        if leg.get("resist_left", 0) < 1:
            raise SystemExit(f"  {entity_name}: no Legendary Resistance left today — the failed save stands")
        leg["resist_left"] -= 1
        msg = f"  {entity_name}: Legendary Resistance — the save succeeds instead; {leg['resist_left']}/{leg['resist']} left today"
    elif action == "lair":
        if value and leg.get("lair_last", "").lower() == value.lower():
            raise SystemExit(f"  {entity_name}: it used '{value}' last round — a lair action cannot be used twice in a row")
        leg["lair_last"] = value
        msg = f"  {entity_name}: lair action — {value}"
    elif action == "reset":
        leg["resist_left"], leg["left"] = leg.get("resist", 0), leg.get("actions", 0)
        msg = f"  {entity_name}: {_fmt_legendary(leg)}"
    elif action == "off":
        ent.pop("legendary", None)
        _save(campaign, state)
        print(f"  {entity_name}: no longer tracked as legendary")
        return {}
    else:
        raise SystemExit(f"  unknown action {action!r}")
    ent["legendary"] = leg
    _save(campaign, state)
    print(msg)
    return leg


def _lair_now(inits: dict, prev_cur: str, new_cur: str, wrapped: bool) -> bool:
    """Does initiative count 20 (losing ties) fall between these two turns?"""
    p = inits.get(prev_cur.lower()) if prev_cur else None
    n = inits.get(new_cur.lower()) if new_cur else None
    if n is None or (p is None and not wrapped):
        return False
    if not wrapped:
        return p >= 20 > n
    return (p is not None and p >= 20) or n < 20


def turn_news(campaign: str, prev: dict, new: dict) -> list:
    """The turn moved (push_stats.py --turn-current / --turn-round): what the legendary creatures get. Lines."""
    if not isinstance(new, dict):
        return []
    prev = prev if isinstance(prev, dict) else {}
    order = [str(n) for n in (new.get("order") or prev.get("order") or [])]
    prev_cur, new_cur = str(prev.get("current") or ""), str(new.get("current") or prev.get("current") or "")
    prev_round, new_round = prev.get("round") or 1, new.get("round") or prev.get("round") or 1
    if not new_cur or (prev_cur.lower() == new_cur.lower() and prev_round == new_round):
        return []
    try:
        state = _load(campaign)
    except Exception:
        return []
    legends = [e for e in state.values() if isinstance(e, dict) and e.get("legendary")
               and (not order or e["name"].lower() in {n.lower() for n in order})]
    if not legends:
        return []
    lines, changed = [], False
    inits = {str(k).lower(): v for k, v in (new.get("init") or prev.get("init") or {}).items() if isinstance(v, (int, float))}
    idx = {n.lower(): i for i, n in enumerate(order)}
    wrapped = new_round != prev_round or (prev_cur.lower() in idx and new_cur.lower() in idx
                                          and idx[new_cur.lower()] <= idx[prev_cur.lower()])
    for ent in legends:
        leg = ent["legendary"]
        if leg.get("lair") and inits and _lair_now(inits, prev_cur, new_cur, wrapped):
            lines.append(f"🏰 Initiative count 20 — {ent['name']}'s LAIR ACTION before {new_cur}'s turn"
                         + (f" (last round: {leg['lair_last']} — not that one again)" if leg.get("lair_last") else ""))
        if leg.get("actions") and new_cur.lower() == ent["name"].lower():
            if leg.get("left", 0) != leg["actions"]:
                changed = True
            leg["left"] = leg["actions"]
            lines.append(f"⚔ {ent['name']}'s turn: legendary actions back to {leg['actions']}/{leg['actions']}")
        elif leg.get("actions") and prev_cur and prev_cur.lower() != ent["name"].lower() and leg.get("left", 0) > 0:
            can = [o["name"] + (f" ({o['cost']})" if o.get("cost", 1) > 1 else "") for o in leg.get("options") or []
                   if o.get("cost", 1) <= leg["left"]]
            lines.append(f"⚔ {prev_cur}'s turn is over: {ent['name']} may take a legendary action "
                         f"({leg['left']}/{leg['actions']} left" + (f": {', '.join(can)}" if can else "")
                         + f") — tracker.py legendary \"{ent['name']}\" use")
    if changed:
        _save(campaign, state)
    return lines


# ─── Morale (DMG 2014 p. 273, optional rule) ───────────────────────────────────

MORALE_DC = 10


def morale_on(campaign: str) -> bool:
    """The campaign adopted the morale rule: `morale: on` in state.md's Session Flags."""
    import re
    try:
        text = (_find_campaign(campaign) / "state.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(re.search(r"^[ \t]*(?:[-*][ \t]*)?morale:[ \t]*(?:on|true|yes|sì|si)\b", text, re.M | re.I))


def _srd_mods(name: str, exact: bool) -> dict:
    try:
        import encounter
        rec = encounter.find_monster(name, encounter.load_monsters())
    except Exception:
        return {}
    if exact and name.strip().lower() != str(rec.get("name", "")).lower():
        return {}
    return {"wis": (int(rec.get("wis", 10)) - 10) // 2, "cha": (int(rec.get("cha", 10)) - 10) // 2}


def _alive(ent: dict) -> bool:
    return not (isinstance(ent.get("hp"), int) and ent["hp"] <= 0) and \
        not any(c in ("unconscious", "incapacitated", "paralyzed", "petrified", "stunned") for c in ent.get("conditions", []))


def _group(state: dict, name: str) -> list:
    return [e for e in state.values() if isinstance(e, dict) and (e.get("morale") or {}).get("group", "").lower() == name.lower()]


def morale_after_damage(campaign: str, state: dict, ent: dict) -> list:
    """After damage: is a morale check due for the creature or its group? Lines; marks `due` (call with the state, then save)."""
    mor = ent.get("morale")
    if not mor or mor.get("fearless") or not morale_on(campaign) or not isinstance(ent.get("hp"), int):
        return []
    lines = []
    mx = ent.get("max_hp") or 0
    if 0 < ent["hp"] <= mx / 2 and not mor.get("half_done"):
        mor["half_done"], mor["due"] = True, "half"
        lines.append(f"🏳 Morale: {ent['name']} is down to half its HP for the first time — it may flee: "
                     f"tracker.py morale \"{ent['name']}\" roll")
    group = _group(state, mor.get("group", "")) if mor.get("group") else []
    if ent["hp"] <= 0 and mor.get("leader") and group:
        for e in group:
            if _alive(e) and not e["morale"].get("fearless"):
                e["morale"]["due"] = "leader"
        lines.append(f"🏳 Morale: the leader {ent['name']} is down — the group {mor['group']} may break: "
                     f"tracker.py morale \"{mor['group']}\" roll")
    if group and len(group) >= 2 and not any(e["morale"].get("half_group") for e in group):
        down = [e for e in group if not _alive(e)]
        if len(down) * 2 >= len(group):
            for e in group:
                e["morale"]["half_group"] = True
                if _alive(e) and not e["morale"].get("fearless"):
                    e["morale"]["due"] = "half_group"
            lines.append(f"🏳 Morale: half of {mor['group']} is down ({len(down)} of {len(group)}) — if the party has "
                         f"lost no one, the group may break: tracker.py morale \"{mor['group']}\" roll")
    return lines


def cmd_morale(campaign: str, target: str, action: str, group: str = "", leader: bool = False, srd: str = "",
               wis=None, cha=None, fearless: bool = False, mode: str = "normal", rng=None) -> dict:
    import random
    rng = rng or random.SystemRandom()
    state = _load(campaign)
    if action == "set":
        ent = _entity(state, target)
        mods = _srd_mods(srd, False) if srd else _srd_mods(target, True)
        ent["morale"] = {"group": " ".join((group or "").split())[:50], "leader": bool(leader),
                         "wis": int(wis) if wis is not None else mods.get("wis", 0),
                         "cha": int(cha) if cha is not None else mods.get("cha", 0),
                         "fearless": bool(fearless), "half_done": False, "half_group": False, "due": ""}
        _save(campaign, state)
        m = ent["morale"]
        print(f"  {ent['name']}: morale" + (f" in {m['group']}" if m["group"] else "") + (" (leader)" if m["leader"] else "")
              + f", WIS {m['wis']:+d}" + (" — fearless, never flees" if m["fearless"] else "")
              + ("" if morale_on(campaign) else "  [the campaign has not adopted the rule: add `morale: on` to the Session Flags for the prompts]"))
        return ent["morale"]
    if action == "off":
        ent = _entity(state, target)
        ent.pop("morale", None)
        _save(campaign, state)
        print(f"  {target}: morale no longer tracked")
        return {}
    # roll: one creature, or a group (its leader rolls; if the leader cannot, the one with the best Charisma)
    members = _group(state, target)
    if members:
        standing = [e for e in members if _alive(e) and not e["morale"].get("fearless")]
        if not standing:
            raise SystemExit(f"  nobody in {target} is left to run")
        roller = next((e for e in standing if e["morale"].get("leader")), None) or max(standing, key=lambda e: e["morale"].get("cha", 0))
        who, affected = f"{target} ({roller['name']} rolls)", standing
    else:
        key = target.lower()
        if key not in state or not state[key].get("morale"):
            raise SystemExit(f"  {target}: no creature or group with morale — morale <entity> set first")
        roller = state[key]
        who, affected = roller["name"], [roller]
    if mode == "fail":
        total, shown, held = None, "automatic failure (overwhelming odds)", False
    else:
        rolls = [rng.randint(1, 20)] + ([rng.randint(1, 20)] if mode == "disadvantage" else [])
        d20 = min(rolls)
        total = d20 + roller["morale"].get("wis", 0)
        shown = (f"[{', '.join(map(str, rolls))}] → {d20}" if len(rolls) > 1 else str(d20)) + f"{roller['morale'].get('wis', 0):+d} = {total}"
        held = total >= MORALE_DC
    for e in affected:
        e["morale"]["due"] = ""
        if not held:
            e["morale"]["broken"] = True
    _save(campaign, state)
    print(f"  🏳 Morale of {who}: Wisdom save {shown} vs DC {MORALE_DC} — "
          + ("they HOLD and fight on" if held else
             "they BREAK: they flee by the quickest way; cornered, they surrender. One who surrendered and is "
             "attacked anyway will not surrender again. Fleeing may raise the alarm or carry off the treasure."))
    return {"held": held, "total": total, "affected": [e["name"] for e in affected]}


def cmd_saves(campaign: str, entity_name: str, action: str) -> None:
    state = _load(campaign)
    ent   = _entity(state, entity_name)
    saves = ent["death_saves"]

    if action == "success":
        saves["successes"] = min(3, saves["successes"] + 1)
        if saves["successes"] >= 3:
            saves["stable"] = True
            msg = f"{entity_name} is STABLE (3 successes)"
            _send_announce(f"☑ {msg}")
            print(f"  ☑ {msg}")
        else:
            print(f"  {entity_name}: {saves['successes']} success(es), {saves['failures']} failure(s)")

    elif action == "failure":
        saves["failures"] = min(3, saves["failures"] + 1)
        if saves["failures"] >= 3:
            msg = f"{entity_name} — 3 FAILURES: character is DEAD"
            _send_announce(f"✗ {msg}")
            print(f"  ✗ {msg}")
        else:
            print(f"  {entity_name}: {saves['successes']} success(es), {saves['failures']} failure(s)")

    elif action == "stable":
        saves["stable"] = True
        _send_announce(f"{entity_name} stabilised")
        print(f"  {entity_name}: marked stable")

    elif action == "reset":
        saves.update({"successes": 0, "failures": 0, "stable": False})
        print(f"  {entity_name}: death saves reset (regained consciousness)")

    ent["death_saves"] = saves
    _save(campaign, state)
    _push_death_save(entity_name, action)


def cmd_status(campaign: str, filter_name: str = "") -> None:
    state = _load(campaign)
    if not state:
        print("  (no tracked entities)")
        return

    entities = list(state.values())
    if filter_name:
        entities = [e for e in entities if filter_name.lower() in e["name"].lower()]

    for ent in entities:
        name   = ent["name"]
        conds  = ent.get("conditions", [])
        conc   = ent.get("concentration")
        saves  = ent.get("death_saves", {})

        effects = ent.get("effects", [])
        parts = []
        if conds:
            timers = ent.get("condition_timers", {})
            parts.append("Conditions: " + ", ".join(
                c.capitalize() + (f" · {_fmt_effect(timers[c])}" if c in timers else "") for c in conds))
        if conc:
            parts.append(f"Concentrating: {conc}")
        if effects:
            eff_strs = []
            for e in effects:
                rem = _fmt_effect(e)
                tag = " [conc]" if e.get("concentration") else ""
                eff_strs.append(f"{e['name']}{tag} · {rem}")
            parts.append("Effects: " + ", ".join(eff_strs))
        s = saves.get("successes", 0)
        f = saves.get("failures", 0)
        if s or f:
            stable = " (stable)" if saves.get("stable") else ""
            parts.append(f"Death saves: {s}✓ {f}✗{stable}")
        if ent.get("legendary"):
            parts.append("Legendary: " + _fmt_legendary(ent["legendary"]))
        if ent.get("morale"):
            m = ent["morale"]
            parts.append("Morale: " + (m.get("group") or "alone") + (" (leader)" if m.get("leader") else "")
                         + (" — fearless" if m.get("fearless") else "") + (" — BROKEN" if m.get("broken") else "")
                         + (f" — CHECK DUE ({m['due']})" if m.get("due") else ""))

        if parts:
            print(f"  {name}:")
            for p in parts:
                print(f"    {p}")
        else:
            print(f"  {name}: clean")


def cmd_clear(campaign: str, clear_all: bool = False) -> None:
    state = _load(campaign)
    for ent in state.values():
        had_conditions = bool(ent.get("conditions"))
        ent["conditions"] = []
        ent["condition_timers"] = {}
        ent["concentration"] = None
        ent["effects"] = []
        for k in ("hp", "max_hp", "ac"):       # the fight is over: the foes' numbers go with it
            ent.pop(k, None)
        if ent.get("legendary"):               # actions back; Legendary Resistance waits for a new day
            ent["legendary"]["left"] = ent["legendary"].get("actions", 0)
            ent["legendary"]["lair_last"] = ""
        # Push only what had something to clear: a long campaign keeps dozens
        # of old foes here, and one push_stats call each took minutes.
        if had_conditions:
            _push_conditions(ent["name"], [])
        if clear_all:
            ent["death_saves"] = {"successes": 0, "failures": 0, "stable": False}
    _save(campaign, state)
    scope = "all state" if clear_all else "conditions and concentration"
    print(f"  Cleared {scope} for {len(state)} entities.")
    _recover_ammo(campaign)


def _recover_ammo(campaign: str) -> None:
    """The fight is over: half the arrows and bolts shot can be found again (ammo.py, PHB p. 146)."""
    try:
        import ammo
        for line in ammo.recover(campaign):
            print(f"  🏹 {line}")
    except Exception:
        pass   # a courtesy: the encounter is cleared anyway


# ─── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="Session condition/death-save tracker.")
    p.add_argument("-c", "--campaign", required=True, metavar="NAME",
                   help="Campaign name (matches ~/.claude/dnd/campaigns/<name>/)")
    sub = p.add_subparsers(dest="cmd")

    # effect
    eff = sub.add_parser("effect", help="Track timed effects (rounds/minutes/hours/indefinite)")
    eff.add_argument("action", choices=["start", "end", "tick"])
    eff.add_argument("entity")
    eff.add_argument("spell", nargs="?", default="",
                     help="Spell or ability name")
    eff.add_argument("duration", nargs="?", default="",
                     help="Duration: 10r / 60m / 8h / indef")
    eff.add_argument("conc", nargs="?", default="",
                     help="Pass 'conc' to mark as concentration")

    # condition
    cond = sub.add_parser("condition", help="Add/remove/clear conditions")
    cond.add_argument("action", choices=["add", "remove", "clear"])
    cond.add_argument("entity")
    cond.add_argument("condition", nargs="?", default="")
    cond.add_argument("duration", nargs="?", default="",
                      help="add: 3r / 10m / 1h — the condition ends by itself")

    # concentrate
    conc = sub.add_parser("concentrate", help="Track spell concentration")
    conc.add_argument("entity")
    conc.add_argument("spell", help="Spell name, or 'break' to end concentration")

    # hp
    hpp = sub.add_parser("hp", help="Hit points (and AC) of a monster or an NPC")
    hpp.add_argument("entity")
    hpp.add_argument("current", type=int)
    hpp.add_argument("--max", type=int, default=None)
    hpp.add_argument("--ac", type=int, default=None)

    # hit
    hit = sub.add_parser("hit", help="Damage to a concentrating creature: roll the CON save")
    hit.add_argument("entity")
    hit.add_argument("damage", type=int)
    hit.add_argument("--con", type=int, default=0, help="its Constitution save bonus")
    hit.add_argument("--adv", action="store_true", help="advantage (War Caster)")
    hit.add_argument("--dis", action="store_true", help="disadvantage")
    hit.add_argument("--down", action="store_true", help="it dropped to 0 HP: concentration just ends")

    # legendary
    leg = sub.add_parser("legendary", help="Legendary actions, Legendary Resistance and lair actions")
    leg.add_argument("entity")
    leg.add_argument("action", choices=["set", "use", "resist", "lair", "reset", "off"])
    leg.add_argument("value", nargs="?", default="", help="lair: the lair action it used")
    leg.add_argument("--actions", type=int, default=None)
    leg.add_argument("--resist", type=int, default=None)
    leg.add_argument("--lair", action="store_true", default=None)
    leg.add_argument("--cost", type=int, default=1)
    leg.add_argument("--as", dest="srd", default="", help="the SRD creature to read the numbers from")

    # morale
    mor = sub.add_parser("morale", help="Morale (DMG optional rule): who may flee")
    mor.add_argument("target", help="a creature, or a group for roll")
    mor.add_argument("action", choices=["set", "roll", "off"])
    mor.add_argument("--group", default="")
    mor.add_argument("--leader", action="store_true")
    mor.add_argument("--as", dest="srd", default="")
    mor.add_argument("--wis", type=int, default=None)
    mor.add_argument("--cha", type=int, default=None)
    mor.add_argument("--fearless", action="store_true", help="never flees (mindless, undead under orders, a zealot)")
    mor.add_argument("--dis", action="store_true", help="overwhelming odds: disadvantage")
    mor.add_argument("--fail", action="store_true", help="hopeless odds: the save fails")

    # saves
    sav = sub.add_parser("saves", help="Track death saving throws")
    sav.add_argument("entity")
    sav.add_argument("action", choices=["success", "failure", "stable", "reset"])

    # status
    stat = sub.add_parser("status", help="Show current tracking state")
    stat.add_argument("entity", nargs="?", default="")

    # clear
    clr = sub.add_parser("clear", help="Clear encounter state")
    clr.add_argument("--all", action="store_true",
                     help="Also clear death saves (not just conditions/concentration)")

    import paths
    args = p.parse_args(paths.campaign_flag_first(sys.argv[1:]))

    if args.cmd == "effect":
        cmd_effect(args.campaign, args.action, args.entity,
                   spell=args.spell, duration=args.duration,
                   is_conc=(args.conc.lower() == "conc"))
    elif args.cmd == "condition":
        cmd_condition(args.campaign, args.entity, args.action, args.condition, args.duration)
    elif args.cmd == "concentrate":
        cmd_concentrate(args.campaign, args.entity, args.spell)
    elif args.cmd == "hp":
        cmd_hp(args.campaign, args.entity, args.current, args.max, args.ac)
    elif args.cmd == "hit":
        cmd_hit(args.campaign, args.entity, args.damage, args.con,
                "advantage" if args.adv and not args.dis else "disadvantage" if args.dis and not args.adv else "normal",
                args.down)
    elif args.cmd == "legendary":
        cmd_legendary(args.campaign, args.entity, args.action, args.actions, args.resist, args.lair,
                      args.cost, args.srd, args.value)
    elif args.cmd == "morale":
        cmd_morale(args.campaign, args.target, args.action, args.group, args.leader, args.srd, args.wis, args.cha,
                   args.fearless, "fail" if args.fail else "disadvantage" if args.dis else "normal")
    elif args.cmd == "saves":
        cmd_saves(args.campaign, args.entity, args.action)
    elif args.cmd == "status":
        cmd_status(args.campaign, args.entity)
    elif args.cmd == "clear":
        cmd_clear(args.campaign, getattr(args, "all", False))
    else:
        p.print_help()


if __name__ == "__main__":
    main()
