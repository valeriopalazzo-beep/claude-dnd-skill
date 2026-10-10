#!/usr/bin/env python3
"""whisper.py — secret notes between the DM and one player.

A note is shown only on the phone (or browser) of the player it is for: it is
not narrated, not read aloud, and never reaches the shared screen or the other
players. Use it for what one character alone perceives or knows — the thing
only the one who passed the Perception check sees, a vision, a charm's command,
a thief's private aside.

    # DM → one player. The others see only "the DM passes a note to Aldric":
    python3 whisper.py --to "Aldric" "Dietro l'arazzo senti qualcuno respirare."

    # …or see nothing at all:
    python3 whisper.py --to "Aldric" --no-teaser "Il mercante ti fa l'occhiolino."

    # Notes the players wrote to the DM and the DM has not read yet (prints, then clears):
    python3 whisper.py --inbox

    # Every note of the active campaign, or one character's thread:
    python3 whisper.py --list
    python3 whisper.py --list --character "Aldric"

Players write from their phone ("Secret notes"). Their notes reach the DM
through check_input.py and autorun_wait.py, which print them like this:

    [[SECRET NOTE from Aldric — …]] Voglio sfilare la chiave a Mira senza che se ne accorga.

Answer a secret note with `--to`, never in the shared narration.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from runtime_paths import rt          # writable runtime dir (update-safe)

INBOX_FILE = rt("whisper_inbox.json")   # players' notes the DM has not read yet
TOKEN_FILE = rt(".token")
TIMEOUT = 3.0


# ─── Inbox: written by the display server, drained by the DM's scripts ────────

def inbox_add(entry: dict) -> None:
    """Queue a player's note for the DM. Called by the display server."""
    try:
        with open(INBOX_FILE, encoding="utf-8") as f:
            notes = json.load(f)
        if not isinstance(notes, list):
            notes = []
    except (OSError, ValueError):
        notes = []
    notes.append({"from": entry.get("from", ""), "text": entry.get("text", "")})
    tmp = INBOX_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(notes, f, ensure_ascii=False)
        os.replace(tmp, INBOX_FILE)
    except OSError:
        pass


def inbox_pending() -> bool:
    return os.path.exists(INBOX_FILE)


def format_note(sender: str, text: str) -> str:
    """One line for the DM's context: who wrote, and how to treat it."""
    return (f'[[SECRET NOTE from {sender} — for the DM only, not a turn action. '
            f'Do not narrate it or mention it to the table: answer with '
            f'whisper.py --to "{sender}".]] {text}')


def drain_inbox() -> list[str]:
    """Unread player notes as lines for the DM; clears the inbox."""
    try:
        with open(INBOX_FILE, encoding="utf-8") as f:
            notes = json.load(f)
        os.remove(INBOX_FILE)
    except (OSError, ValueError):
        return []
    return [format_note(str(n.get("from", "?")), " ".join(str(n.get("text", "")).split()))
            for n in notes if isinstance(n, dict) and n.get("text")]


# ─── Talking to the display server ────────────────────────────────────────────

def _base_url() -> str:
    scheme_file = os.path.join(_HERE, ".scheme")
    scheme = "http"
    if os.path.exists(scheme_file):
        scheme = open(scheme_file, encoding="utf-8").read().strip() or "http"
    return f"{scheme}://localhost:5001"


def _request(path: str, body: dict | None = None):
    """(status, parsed JSON or None). status 0 = the display is not running."""
    url = _base_url() + path
    headers = {"Content-Type": "application/json"}
    try:
        token = open(TOKEN_FILE, encoding="utf-8").read().strip()
    except OSError:
        token = ""
    if token:
        headers["X-DND-Token"] = token
    ctx = None
    if url.startswith("https"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except Exception:
        return 0, None
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, None


def send(to: str, text: str, teaser: bool = True) -> int:
    status, body = _request("/whisper", {"to": to, "text": text, "teaser": teaser})
    if status == 200 and isinstance(body, dict):
        print(f"whisper → {body.get('to', to)}: delivered to their phone only"
              + ("" if teaser else " (no hint to the others)"))
        return 0
    if status == 404 and isinstance(body, dict):
        party = ", ".join(body.get("party") or []) or "nobody yet — push the party with push_stats.py"
        print(f"whisper: no character called {to!r} in the party ({party}). Nothing sent.", file=sys.stderr)
        return 1
    if status == 0:
        print("whisper: the display is not running — nothing sent.", file=sys.stderr)
        return 1
    print(f"whisper: the display refused the note (HTTP {status}). Nothing sent.", file=sys.stderr)
    return 1


def show_list(character: str) -> int:
    path = "/whispers?" + (urllib.parse.urlencode({"character": character}) if character else "all=1")
    status, notes = _request(path)
    if status != 200 or not isinstance(notes, list):
        print("whisper: the display is not running." if status == 0
              else f"whisper: could not read the notes (HTTP {status}).", file=sys.stderr)
        return 1
    if not notes:
        print("No secret notes.")
    for n in notes:
        who = f"DM → {n.get('to')}" if n.get("from") == "dm" else f"{n.get('from')} → DM"
        print(f"{who}: {n.get('text', '')}")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    ap = argparse.ArgumentParser(description="Secret notes between the DM and one player.")
    ap.add_argument("--to", metavar="NAME", help="character the note is for")
    ap.add_argument("text", nargs="*", help="the note (with --to)")
    ap.add_argument("--no-teaser", action="store_true",
                    help="the other screens show nothing (default: 'the DM passes a note to NAME')")
    ap.add_argument("--inbox", action="store_true",
                    help="print the players' unread notes to the DM, then clear them")
    ap.add_argument("--list", action="store_true", help="print the notes of the active campaign")
    ap.add_argument("--character", metavar="NAME", default="", help="with --list: one character's thread")
    args = ap.parse_args()

    if args.inbox:
        for line in drain_inbox():
            print(line)
        return 0
    if args.list:
        return show_list(args.character.strip())
    text = " ".join(args.text).strip()
    if not args.to or not text:
        ap.error('give a recipient and a note: whisper.py --to "Name" "text"')
    return send(args.to.strip(), text, teaser=not args.no_teaser)


if __name__ == "__main__":
    sys.exit(main())
