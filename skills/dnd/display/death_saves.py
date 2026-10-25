#!/usr/bin/env python3
"""death_saves.py — death saving throws rolled on a phone, waiting for the DM.

The display server rolls a dying character's death save when the player taps
it (/death-save/roll) and writes a note here. autorun_wait.py wakes on it and
check_input.py prints it, like a vote result or a turn in a chase, so the DM
narrates the outcome and moves the turn on without watching the story feed.
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from runtime_paths import rt          # writable runtime dir (update-safe)

NOTES_FILE = rt("death_save_notes.json")   # rolls the DM has not been told about yet

_OUTCOME = {
    "success": "a success", "failure": "a failure", "fail2": "a natural 1: two failures",
    "stable": "the third success: stable at 0 HP", "dead": "the third failure: dead",
    "up": "a natural 20: back up with 1 HP",
}


def note_line(result: dict) -> str:
    who = result.get("character", "?")
    what = _OUTCOME.get(result.get("outcome", ""), result.get("outcome", ""))
    tally = f"{result.get('successes', 0)} success(es), {result.get('failures', 0)} failure(s)"
    tail = "" if result.get("outcome") in ("up", "stable", "dead") else f" — now {tally}"
    return (f"[[DEATH SAVE — {who} rolled {result.get('roll', '?')} on the phone: {what}{tail}. "
            "Already applied to the card; narrate it and move to the next turn.]]")


def notes_add(result: dict) -> None:
    try:
        with open(NOTES_FILE, encoding="utf-8") as f:
            notes = json.load(f)
        if not isinstance(notes, list):
            notes = []
    except (OSError, ValueError):
        notes = []
    notes.append(note_line(result))
    try:
        with open(NOTES_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(notes, f, ensure_ascii=False)
        os.replace(NOTES_FILE + ".tmp", NOTES_FILE)
    except OSError:
        pass


def notes_pending() -> bool:
    return os.path.exists(NOTES_FILE)


def drain_notes() -> list:
    try:
        with open(NOTES_FILE, encoding="utf-8") as f:
            notes = json.load(f)
        os.remove(NOTES_FILE)
    except (OSError, ValueError):
        return []
    return [n for n in notes if isinstance(n, str)]
