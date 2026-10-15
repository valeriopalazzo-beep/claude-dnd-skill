#!/usr/bin/env python3
"""recap.py — "previously on…": the story so far, on every screen.

At the start of a session the DM writes a short recap of where things stand;
this puts it on every screen as a full-screen card titled "Previously on…"
(in the display's language), and the narrator's voice reads it on the devices
that narrate. A player who joins later still finds it when they open the
display, and anyone can open it again from the menu while it is there.

    python3 recap.py << 'DNDEND'
    La compagnia ha lasciato la Rocca all'alba…
    DNDEND
    python3 recap.py --title "Nelle puntate precedenti di Corona di Cenere…" < recap.txt
    python3 recap.py show      # what is on the screens now
    python3 recap.py clear     # take it away

Blank lines separate paragraphs. Keep it short: 120-220 words read well.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

MAX_CHARS = 4000


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


def main(argv: list | None = None) -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    ap = argparse.ArgumentParser(description='"Previously on…" on every screen.')
    ap.add_argument("cmd", nargs="?", choices=("show", "clear"), help="show / clear; without it the recap is read from stdin")
    ap.add_argument("--title", default="", help='the card\'s title (default: "Previously on…" in the display\'s language)')
    ap.add_argument("--text", default=None, help="the recap; without it, stdin")
    args = ap.parse_args(argv)

    if args.cmd == "show":
        status, body = _request("/recap")
    elif args.cmd == "clear":
        status, body = _request("/recap", {"clear": True})
    else:
        text = args.text if args.text is not None else ("" if sys.stdin.isatty() else sys.stdin.read())
        text = text.strip()
        if not text:
            print("recap: no text (pipe it in, or --text). Nothing was shown.", file=sys.stderr)
            return 1
        if len(text) > MAX_CHARS:
            print(f"recap: {len(text)} characters, {MAX_CHARS} at most — shorten it. Nothing was shown.", file=sys.stderr)
            return 1
        status, body = _request("/recap", {"text": text, "title": args.title.strip()})
    if status != 200 or not isinstance(body, dict):
        why = body.get("error") if isinstance(body, dict) else ""
        print("recap: " + ("the display is not running" if status == 0 else f"HTTP {status}{': ' + why if why else ''}")
              + ".", file=sys.stderr)
        return 1
    recap = body.get("recap")
    if not recap:
        print("No recap on the screens.")
    elif args.cmd == "show":
        print((recap.get("title") or "Previously on…") + "\n\n" + recap["text"])
    else:
        print(f"→ on every screen ({len(recap['text'].split())} words); the narrator reads it where narration is on")
    return 0


if __name__ == "__main__":
    sys.exit(main())
