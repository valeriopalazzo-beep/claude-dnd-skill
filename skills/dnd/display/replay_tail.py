#!/usr/bin/env python3
"""replay_tail.py — put the last session's final exchanges back on the display.

Used by `/dnd load` after `--clear` and the full stats push: it reads the
campaign-side session_tail.json and sends every entry the way it was first
shown, so nobody has to map entry shapes to send.py flags by hand (an image
entry is a dict, a handout carries an id, a map clear is not worth replaying).

    python3 replay_tail.py <campaign-name>

Replayed entries are marked "replay": the display does not give Inspiration
again, play sound effects or change the scene for them.
Run it after `push_stats.py --set-campaign`: images are served from the
active campaign's media folder.
"""

from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
for _p in (_HERE, _SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import paths  # noqa: E402
import send   # noqa: E402  (shares the display URL, token and retrying POST)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def _entries(camp: str) -> list:
    path = paths.campaign_dir(camp) / "session_tail.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        print(f"replay_tail.py: {path} is not valid JSON — nothing replayed", file=sys.stderr)
        return []
    if not isinstance(data, list):
        return []
    # Entries stamped with another campaign belong elsewhere (same rule as the display).
    return [e for e in data if isinstance(e, dict) and e.get("_camp", camp) == camp]


def _send(path: str, body: dict, token: str) -> bool:
    return send._post(f"{send.BASE_URL}/{path}", json.dumps(body).encode("utf-8"), token)


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1].startswith("-"):
        print(__doc__.strip().splitlines()[0])
        print("usage: replay_tail.py <campaign-name>")
        return 2
    camp = sys.argv[1]
    entries = _entries(camp)
    if not entries:
        print(f"replay_tail.py: no session tail for {camp} — display starts blank")
        return 0

    token = send._read_token()
    sent = skipped = failed = 0
    for e in entries:
        e = {k: v for k, v in e.items() if k != "_camp"}
        audience = {k: e[k] for k in ("only", "only_label") if k in e}
        if e.get("map_clear"):
            skipped += 1
            continue
        if isinstance(e.get("image"), dict):
            ok = _send("image", {**e["image"], **audience}, token)
        elif isinstance(e.get("handout"), dict) and e["handout"].get("id"):
            ok = _send("handout", {"id": e["handout"]["id"]}, token)
        elif e.get("text"):
            ok = _send("chunk", {**e, "replay": True}, token)
        else:
            skipped += 1
            continue
        if ok:
            sent += 1
        else:
            failed += 1
            if send._SEND_LOG and send._SEND_LOG[-1].get("reason") == "display offline":
                print("replay_tail.py: display offline — stopped", file=sys.stderr)
                return 1
    print(f"replay_tail.py: {sent} entries replayed, {skipped} skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
