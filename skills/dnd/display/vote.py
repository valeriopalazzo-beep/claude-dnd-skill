#!/usr/bin/env python3
"""vote.py — the party decides together: a vote on the players' phones.

The DM asks a question with two or more answers; each voting player taps one
on their own screen (and may change it until the vote ends). The count shows
on every screen as it grows, or only at the end with --secret. The vote ends
when everybody has voted, or when the DM closes it; the result goes into the
story and reaches the DM with the next check_input.py / autorun_wait.py as

    [[VOTE: "Destra o sinistra?" — Destra 3, Sinistra 1 (Ajeje: Sinistra; Hernio: Destra; …). Destra wins.]]

    vote.py open "Destra o sinistra?" --option Destra --option Sinistra
    vote.py open "Chi entra per primo?" --option Lio --option Hernio --group Collina   # a split party: only that group
    vote.py open "Accettate l'offerta?" --option Sì --option No --only "Ajeje, Lyra" --secret
    vote.py show       # the count so far
    vote.py close      # end it now and print the result

Without --group / --only every character at the table votes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from runtime_paths import rt          # writable runtime dir (update-safe); also puts scripts/ on the path

NOTES_FILE = rt("vote_notes.json")      # results the DM has not been told about yet
MAX_OPTIONS = 8


class VoteError(Exception):
    """A vote that cannot be opened or cast."""


# ─── The count ────────────────────────────────────────────────────────────────

def tally(vote: dict) -> list:
    """Votes per option, in the options' order."""
    counts = [0] * len(vote.get("options") or [])
    for idx in (vote.get("votes") or {}).values():
        if isinstance(idx, int) and 0 <= idx < len(counts):
            counts[idx] += 1
    return counts


def winners(vote: dict) -> list:
    counts = tally(vote)
    top = max(counts) if counts else 0
    return [vote["options"][i] for i, c in enumerate(counts) if c == top and top > 0]


def result_line(vote: dict) -> str:
    """The line for the story: '🗳 Destra o sinistra? — Destra 3 · Sinistra 1'."""
    counts = tally(vote)
    parts = [f"{opt} {counts[i]}" for i, opt in enumerate(vote["options"])]
    return f"🗳 {vote['question']} — " + " · ".join(parts)


def format_note(vote: dict) -> str:
    counts = tally(vote)
    parts = ", ".join(f"{opt} {counts[i]}" for i, opt in enumerate(vote["options"]))
    who = "; ".join(f"{name}: {vote['options'][idx]}" for name, idx in (vote.get("votes") or {}).items()
                    if isinstance(idx, int) and 0 <= idx < len(vote["options"]))
    missing = [n for n in vote.get("voters") or [] if n not in (vote.get("votes") or {})]
    win = winners(vote)
    outcome = (f"{win[0]} wins." if len(win) == 1 else f"A tie between {', '.join(win)}: you decide, or ask again."
               if win else "Nobody voted.")
    tail = f" Did not vote: {', '.join(missing)}." if missing else " Everybody voted."
    return f'[[VOTE: "{vote["question"]}" — {parts}' + (f" ({who})" if who else "") + f". {outcome}{tail}]]"


# ─── Results for the DM: written by the display server, drained by the DM's scripts ──

def notes_add(vote: dict) -> None:
    try:
        with open(NOTES_FILE, encoding="utf-8") as f:
            notes = json.load(f)
        if not isinstance(notes, list):
            notes = []
    except (OSError, ValueError):
        notes = []
    notes.append(vote)
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
    return [format_note(v) for v in notes if isinstance(v, dict) and v.get("options")]


# ─── Command line ─────────────────────────────────────────────────────────────

def _request(path: str, body: dict | None = None):
    """(status, parsed JSON or None); status 0 when the display is not running."""
    import ssl
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


def _print(vote: dict | None) -> None:
    if not vote:
        print("No vote.")
        return
    counts = vote.get("counts") or tally(vote)
    state = "closed" if vote.get("closed") else "open"
    print(f'{vote["question"]} ({state}' + (f", {vote['group']}" if vote.get("group") else "") + ")")
    for i, opt in enumerate(vote["options"]):
        names = [n for n, idx in (vote.get("votes") or {}).items() if idx == i]
        print(f"  {opt}: {counts[i]}" + (f" — {', '.join(names)}" if names else ""))
    missing = [n for n in vote.get("voters") or [] if n not in (vote.get("votes") or {})]
    if missing and not vote.get("closed"):
        print(f"  still to vote: {', '.join(missing)}")


def main(argv: list | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    ap = argparse.ArgumentParser(description="A vote on the players' phones.")
    ap.add_argument("--campaign", default="", help="campaign the groups are read from (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("open", help="ask the table a question")
    p.add_argument("question")
    p.add_argument("--option", action="append", default=[], help="an answer (two or more)")
    p.add_argument("--group", default="", help="only this group of a split party votes")
    p.add_argument("--only", default="", help='only these characters vote, "A, B"')
    p.add_argument("--secret", action="store_true", help="show the count only when the vote is over")
    sub.add_parser("show", help="the count so far")
    sub.add_parser("close", help="end the vote now and print the result")
    args = ap.parse_args(argv)

    if args.cmd == "open":
        options = [o.strip() for o in args.option if o.strip()]
        if not args.question.strip() or not 2 <= len(options) <= MAX_OPTIONS:
            print(f"vote: a question and 2 to {MAX_OPTIONS} --option. Nothing was opened.", file=sys.stderr)
            return 1
        voters, label = [], ""
        if args.group or args.only:
            try:
                import split
                camp = split._campaign(args.campaign)
                voters, label = split.audience(camp, args.group, args.only)
            except Exception as e:
                print(f"vote: {e}. Nothing was opened.", file=sys.stderr)
                return 1
        status, body = _request("/vote", {"question": args.question.strip(), "options": options, "voters": voters,
                                          "group": label, "secret": args.secret})
        what = "the vote was not opened"
    elif args.cmd == "close":
        status, body = _request("/vote", {"close": True})
        what = "the vote was not closed"
    else:
        status, body = _request("/vote")
        what = "could not read the vote"
    if status != 200 or not isinstance(body, dict):
        why = body.get("error") if isinstance(body, dict) else ""
        print("vote: " + ("the display is not running" if status == 0 else f"HTTP {status}{': ' + why if why else ''}")
              + f" — {what}.", file=sys.stderr)
        return 1
    _print(body.get("vote"))
    if args.cmd == "open":
        print("→ on the voters' phones; the result reaches you as a [[VOTE: …]] line")
    return 0


if __name__ == "__main__":
    sys.exit(main())
