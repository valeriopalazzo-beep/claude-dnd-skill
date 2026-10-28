#!/usr/bin/env python3
"""handout.py — hand the party a document they can read and keep.

A letter, a notice, a journal page, a scroll, a scrap of paper — or a picture
from the campaign's media folder. It appears in the story as a sheet of paper
and stays in the players' Documents list (saved in <campaign>/handouts.json),
so they can read it again whenever they like.

    # The text comes from stdin (or --body for a short one):
    python3 handout.py --title "Lettera trovata sul corriere" --kind letter --from "V." << 'DNDEND'
    Ilde,
    il carico parte alla terza luna. Nessuno deve sapere della cripta.
    Brucia questa lettera.
    DNDEND

    python3 handout.py --title "Avviso affisso alla taverna" --kind notice \
        --body "RICERCATO — vivo. 200 monete d'oro. Rivolgersi al capitano Brask."

    # A picture already in <campaign>/media/ (image_gen.py, map_render.py --no-send):
    python3 handout.py --title "Mappa del contrabbandiere" --image mappa-contrabbandiere.png

    # Written in another language: only the characters whose sheet lists it can
    # read it; the others see the script and who at the table can read it.
    python3 handout.py --title "Iscrizione sull'arco" --kind scroll --language Elfico --body "…"
    python3 handout.py --reader a1b2c3d4 "Hernio"   # now Hernio can read it too (comprehend languages…)

    python3 handout.py --list            # id, kind and title of every handout
    python3 handout.py --show a1b2c3d4   # put an old one back on every screen
    python3 handout.py --remove a1b2c3d4 # take it out of the Documents list

Kinds (how the paper looks):
    letter   handwritten letter, signature at the bottom right (--from)
    notice   public notice or wanted poster, centred, framed
    journal  page of a diary or a ledger, ruled
    scroll   old or arcane text on darker parchment
    note     hurried scrap of paper

Write the document itself, in the voice of who wrote it — not a description of
it. Narrate how it is found with send.py as usual; the handout is the object.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from runtime_paths import rt          # writable runtime dir (update-safe)

KINDS = ("letter", "notice", "journal", "scroll", "note")
TOKEN_FILE = rt(".token")
TIMEOUT = 4.0


def _base_url() -> str:
    scheme_file = os.path.join(_HERE, ".scheme")
    scheme = "http"
    if os.path.exists(scheme_file):
        scheme = open(scheme_file, encoding="utf-8").read().strip() or "http"
    return f"{scheme}://127.0.0.1:5001"


def _request(path: str, body: dict | None = None, method: str | None = None):
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
                                 method=method or ("POST" if body is not None else "GET"))
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


def _fail(status: int, body, what: str) -> int:
    if status == 0:
        print(f"handout: the display is not running — {what}.", file=sys.stderr)
    else:
        why = body.get("error") if isinstance(body, dict) else ""
        print(f"handout: {what} (HTTP {status}{': ' + why if why else ''}).", file=sys.stderr)
    return 1


def main() -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    ap = argparse.ArgumentParser(description="Hand the party a document they can read and keep.")
    ap.add_argument("--title", help="what the players call it in their Documents list")
    ap.add_argument("--kind", choices=KINDS, default="letter", help="how the paper looks (default: letter)")
    ap.add_argument("--from", dest="sender", default="", help="signature at the bottom")
    ap.add_argument("--body", help="the text; without it the text is read from stdin")
    ap.add_argument("--image", default="", help="a file in <campaign>/media/ shown on the sheet")
    ap.add_argument("--language", default="", help="written in this language: only who knows it reads it")
    ap.add_argument("--reader", nargs=2, metavar=("ID", "NAME"), help="let one more character read a handout")
    ap.add_argument("--list", action="store_true", help="list the handouts of the active campaign")
    ap.add_argument("--show", metavar="ID", help="show an existing handout again")
    ap.add_argument("--remove", metavar="ID", help="take a handout out of the Documents list")
    args = ap.parse_args()

    if args.list:
        status, items = _request("/handouts")
        if status != 200 or not isinstance(items, list):
            return _fail(status, items, "could not read the handouts")
        if not items:
            print("No handouts yet.")
        for h in items:
            print(f"{h.get('id')}  {h.get('kind', ''):8} {h.get('title', '')}"
                  + (f"  [{h['language']}: {', '.join(h.get('readers') or []) or 'nobody'}]" if h.get("language") else ""))
        return 0

    if args.remove:
        status, body = _request(f"/handout/{args.remove}", method="DELETE")
        if status != 204:
            return _fail(status, body, f"handout {args.remove} was not removed")
        print(f"handout {args.remove}: removed from the Documents list")
        return 0

    if args.reader:
        hid, name = args.reader
        status, body = _request("/handout", {"id": hid, "reader": name})
        if status != 200:
            return _fail(status, body, f"{name} cannot read handout {hid} yet")
        print(f"handout {hid}: \"{body.get('title', '')}\" — readers now: {', '.join(body.get('readers') or [])}")
        return 0

    if args.show:
        status, body = _request("/handout", {"id": args.show})
        if status != 200:
            return _fail(status, body, f"handout {args.show} was not shown")
        print(f"handout {args.show}: \"{body.get('title', '')}\" shown again")
        return 0

    if not args.title:
        ap.error("--title is required (or use --list / --show / --remove)")
    body_text = args.body
    if body_text is None and not sys.stdin.isatty():
        body_text = sys.stdin.read()
    body_text = (body_text or "").strip()
    if not body_text and not args.image:
        ap.error("give the text with --body or on stdin, or a picture with --image")

    status, body = _request("/handout", {
        "title": args.title, "kind": args.kind, "from": args.sender,
        "body": body_text, "image": args.image, "language": args.language,
    })
    if status != 200 or not isinstance(body, dict):
        return _fail(status, body, "nothing was handed out")
    print(f"handout {body.get('id')}: \"{body.get('title')}\" is in the story and in the players' Documents")
    if body.get("language"):
        readers = body.get("readers") or []
        print(f"  in {body['language']}: " + (f"{', '.join(readers)} can read it; the others see only the script"
                                              if readers else "nobody at the table can read it — they see only the script"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
