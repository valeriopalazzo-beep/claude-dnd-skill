#!/usr/bin/env python3
"""ambience.py — ambient sounds under the music: rain, wind, the fire, the river…

Two quiet layers play under the background music on every screen that has
music on. The place layer follows the scene (push_stats.py --scene: the inn's
chatter, crickets at night, drips in a cave, the river by the sea…); the
weather layer follows the campaign calendar (rain, storm, wind), and is not
heard under ground. The DM can set either one by hand:

    ambience.py rain          # the weather layer: rain | storm | wind
    ambience.py fire          # the place layer: fire | river | night | cave | crowd
    ambience.py none weather  # silence one layer (place / weather / both)
    ambience.py auto          # both layers follow the scene and the calendar again
    ambience.py show

The sounds are built with the music (build_music.py) into <data-root>/music.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import music  # noqa: E402

LAYERS = ("place", "weather")


def request_body(word: str, layer: str = "") -> dict:
    """What `ambience.py <word> [layer]` asks the display for; ValueError when it means nothing."""
    word = (word or "").strip().lower()
    if word in music.SOUNDS:
        return {music.SOUNDS[word]["layer"]: word}
    if word in ("none", "off", "auto"):
        value = "none" if word in ("none", "off") else "auto"
        layer = (layer or "both").strip().lower()
        if layer not in LAYERS + ("both",):
            raise ValueError(f"layer is place, weather or both (got {layer!r})")
        return {k: value for k in (LAYERS if layer == "both" else (layer,))}
    raise ValueError(f"unknown sound {word!r} — " + ", ".join(sorted(music.SOUNDS)) + ", none, auto")


def _request(path: str, body: dict | None = None):
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
    ap = argparse.ArgumentParser(description="Ambient sounds under the music.")
    ap.add_argument("sound", help="rain | storm | wind | fire | river | night | cave | crowd | none | auto | show")
    ap.add_argument("layer", nargs="?", default="", help="with none / auto: place, weather or both (default)")
    args = ap.parse_args(argv)
    if args.sound == "show":
        status, body = _request("/ambience")
    else:
        try:
            req = request_body(args.sound, args.layer)
        except ValueError as e:
            print(f"ambience: {e}. Nothing was changed.", file=sys.stderr)
            return 1
        status, body = _request("/ambience", req)
    if status != 200 or not isinstance(body, dict):
        print("ambience: " + ("the display is not running" if status == 0 else f"HTTP {status}") + ".", file=sys.stderr)
        return 1
    amb = body.get("ambience") or {}
    print(f"place: {amb.get('place', 'auto')} · weather: {amb.get('weather', 'auto')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
