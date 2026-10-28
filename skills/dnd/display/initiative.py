#!/usr/bin/env python3
"""initiative.py — let the players roll their own initiative, on their phones.

`scripts/combat.py init '<JSON>' --phones` is the way in; this module is the
part that talks to the display. It opens a roll on the display, waits until
every PC is in (or the time is up — whoever is missing is rolled for), and
returns the order the display settled.

    initiative.py status     # the roll that is open, or the last one
    initiative.py cancel     # close a roll nobody is going to finish

The dice are rolled by the display server, not by the browser: a phone only
asks for its roll. The monsters' rolls stay hidden until the order is shown.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from runtime_paths import rt          # writable runtime dir (update-safe)

TOKEN_FILE = rt(".token")
TIMEOUT = 4.0
DEFAULT_SECONDS = 60


def _base_url() -> str:
    override = os.environ.get("DND_DISPLAY_URL", "").strip().rstrip("/")
    if override:
        return override
    scheme_file = os.path.join(_HERE, ".scheme")
    scheme = "http"
    if os.path.exists(scheme_file):
        scheme = open(scheme_file, encoding="utf-8").read().strip() or "http"
    return f"{scheme}://127.0.0.1:5001"


def _request(path: str, body: dict | None = None, method: str | None = None):
    """(status, parsed body) — status 0 when the display cannot be reached."""
    url = _base_url() + path
    headers = {"Content-Type": "application/json"}
    try:
        token = open(TOKEN_FILE, encoding="utf-8").read().strip()
    except OSError:
        token = ""
    if token:
        headers["X-DND-Token"] = token
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method or ("POST" if data else "GET"))
    ctx = None
    if url.startswith("https"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except ValueError:
            return e.code, {}
    except (OSError, ValueError):
        return 0, {}


def collect(combatants: list, seconds: int = DEFAULT_SECONDS, poll: float = 0.5, say=None) -> list | None:
    """Open a roll for these combatants and wait for the order.

    Returns [{"name", "type", "modifier", "roll", "total", "auto"}, …] in turn
    order, or None when there is no display to ask (not running, or too old
    to know about initiative) — the caller then rolls for everyone itself.
    """
    say = say or (lambda msg: print(msg, file=sys.stderr, flush=True))
    status, state = _request("/initiative", {"combatants": combatants, "timeout": seconds})
    if status != 200 or not state.get("id"):
        return None
    roll_id = state["id"]
    waiting_before = None
    deadline = time.time() + seconds + 15           # the display settles at its own deadline; this is the backstop
    while not state.get("done"):
        waiting = [e["name"] for e in state.get("entries", []) if e.get("type") == "pc" and not e.get("rolled")]
        if waiting != waiting_before:
            say("initiative: waiting for " + ", ".join(waiting) if waiting else "initiative: all in")
            waiting_before = waiting
        if time.time() > deadline:
            return None
        time.sleep(poll)
        status, state = _request("/initiative")
        if status != 200 or state.get("id") != roll_id:
            return None                              # cancelled, replaced, or the display went away
    by_name = {e["name"]: e for e in state.get("entries", [])}
    return [by_name[name] for name in state.get("order") or [] if name in by_name]


def main(argv: list | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else ""
    if cmd == "status":
        status, state = _request("/initiative")
        if status != 200:
            print("initiative: the display is not answering", file=sys.stderr)
            return 1
        if not state:
            print("no initiative roll")
            return 0
        print("settled" if state.get("done") else "open")
        for e in state.get("entries", []):
            print(f"  {e['name']}: " + (f"{e['total']} (d20 {e['roll']} {e['modifier']:+d})" if e.get("rolled") else "…"))
        return 0
    if cmd == "cancel":
        status, _ = _request("/initiative", method="DELETE")
        if status not in (200, 204):
            print("initiative: the display is not answering", file=sys.stderr)
            return 1
        print("initiative roll closed")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
