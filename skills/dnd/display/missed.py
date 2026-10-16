#!/usr/bin/env python3
"""missed.py — "While you were away…": a personal recap for a player who missed a session.

A player who could not come finds, the next time they open the display with
their character, a full-screen card written for them: what the others did,
what their character knows of it, what they got (gold, items, XP), and where
they will join the party. The narrator's voice can read it. Once they tap
*Got it* the card stops opening by itself, and the menu keeps it for them to
read again. Nobody else sees it.

    missed.py absent                                   # the PCs named in state.md → absent:
    missed.py add "Lyra" --session 4 << 'DNDEND'
    Mentre eri a Vallombra a curarti la ferita, Hernio e Lio…
    DNDEND
    missed.py add "Lyra" --title "Mentre eri a Vallombra…" --text "…"
    missed.py list [--unread]                          # every recap, and who has read it
    missed.py show "Lyra"                              # what Lyra will find
    missed.py remove <id>

Blank lines separate paragraphs; 3000 characters at most, 120-250 words read
best. The recaps live in <campaign>/missed.json, so they wait for the player
across sessions even with the display off; with the display on, the player's
screen is told at once. A character is named as on the sheets (any part of
the name that means one character only). Anything that does not match exits
non-zero and changes nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

MAX_CHARS = 3000
MAX_KEPT = 30          # recaps per character; the oldest read ones go first
_ABSENT_RE = re.compile(r"^[ \t]*(?:[-*][ \t]*)?absent:[ \t]*(.*)$", re.MULTILINE | re.IGNORECASE)


class MissedError(Exception):
    """A request that cannot be applied; the recaps are left as they were."""


# ─── Data ─────────────────────────────────────────────────────────────────────

def _campaign(campaign: str = "") -> str:
    import media
    camp = media.safe_campaign(campaign) if campaign else media.active_campaign()
    if not camp:
        raise MissedError("no active campaign — pass --campaign or load one first")
    return camp


def _folder(campaign: str):
    import media
    try:
        return media.find_campaign(_campaign(campaign))
    except Exception:
        raise MissedError(f"no campaign called {campaign!r}")


def data_path(campaign: str = "") -> str:
    return str(_folder(campaign) / "missed.json")


def load(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        return []
    except ValueError as e:
        raise MissedError(f"missed.json is not valid JSON ({e})")
    rows = data.get("recaps") if isinstance(data, dict) else data
    return [r for r in rows or [] if isinstance(r, dict) and r.get("character") and r.get("text") and r.get("id")]


def save(path: str, recaps: list) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"recaps": recaps}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _key(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def party(campaign: str = "") -> list:
    """The PCs' names, as the first line of each sheet in characters/ gives them."""
    folder = _folder(campaign) / "characters"
    names = []
    for sheet in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        try:
            first = sheet.read_text(encoding="utf-8", errors="replace").splitlines()[0]
        except (OSError, IndexError):
            first = ""
        names.append(first[2:].strip() if first.startswith("# ") else sheet.stem)
    return [n for n in names if n]


def resolve(names: list, name: str) -> str:
    """The character `name` means: the exact name first, else a part only one has."""
    want = _key(name)
    if not want:
        raise MissedError("for whom?")
    hits = [n for n in names if _key(n) == want] or [n for n in names if want in _key(n)]
    if not hits:
        raise MissedError(f"no character called {name!r} in this campaign (PCs: {', '.join(names) or 'none'})")
    if len(hits) > 1:
        raise MissedError(f"{name!r} matches several characters: " + ", ".join(hits))
    return hits[0]


def absent(campaign: str = "") -> list:
    """The PCs state.md names in `absent:` (as the sheets spell them when they match one)."""
    try:
        text = (_folder(campaign) / "state.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    m = _ABSENT_RE.search(text)
    if not m or m.group(1).strip().lower() in ("", "none", "nessuno", "-"):
        return []
    names = party(campaign)
    out = []
    for raw in m.group(1).split(","):
        raw = raw.strip().strip("*` ")
        if not raw:
            continue
        try:
            out.append(resolve(names, raw))
        except MissedError:
            out.append(raw)
    return out


# ─── Operations ───────────────────────────────────────────────────────────────

def clean_text(text: str) -> str:
    """Keep paragraphs; drop control characters and the markup the page would not show."""
    text = re.sub(r"[\x00-\x09\x0b-\x1f\x7f`<>]", "", str(text or "").replace("\r\n", "\n").replace("\t", " "))
    paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n", text)]
    return "\n\n".join(p for p in paras if p)


def add(recaps: list, character: str, text: str, title: str = "", session: str = "", now: float | None = None) -> dict:
    text = clean_text(text)
    if not text:
        raise MissedError("no text (pipe it in, or --text)")
    if len(text) > MAX_CHARS:
        raise MissedError(f"{len(text)} characters, {MAX_CHARS} at most — shorten it")
    entry = {"id": secrets.token_hex(4), "character": character, "title": " ".join(str(title or "").split())[:120],
             "session": " ".join(str(session or "").split())[:20], "text": text,
             "at": time.time() if now is None else now, "read_at": None}
    recaps.append(entry)
    mine = [r for r in recaps if _key(r["character"]) == _key(character)]
    while len(mine) > MAX_KEPT:
        old = next((r for r in mine if r.get("read_at")), mine[0])
        recaps.remove(old)
        mine.remove(old)
    return entry


def for_character(recaps: list, character: str) -> list:
    """One character's recaps, oldest first."""
    return [r for r in recaps if _key(r["character"]) == _key(character)]


def mark_read(recaps: list, character: str, rid: str, now: float | None = None) -> dict | None:
    """Mark one of `character`'s recaps read; None when it is not theirs."""
    for r in recaps:
        if r["id"] == rid and _key(r["character"]) == _key(character):
            if not r.get("read_at"):
                r["read_at"] = time.time() if now is None else now
            return r
    return None


def remove(recaps: list, rid: str) -> dict:
    for r in recaps:
        if r["id"] == rid:
            recaps.remove(r)
            return r
    raise MissedError(f"no recap with id {rid!r} (missed.py list shows them)")


def describe(r: dict) -> str:
    when = time.strftime("%Y-%m-%d", time.localtime(r.get("at") or 0))
    read = ("read " + time.strftime("%Y-%m-%d %H:%M", time.localtime(r["read_at"]))) if r.get("read_at") else "NOT READ YET"
    head = f"[{r['id']}] {r['character']}" + (f" — session {r['session']}" if r.get("session") else "") + f" ({when}, {read})"
    return head + (f"\n  {r['title']}" if r.get("title") else "")


def _announce(character: str) -> None:
    """Tell the display, so the player's screen opens the card now (silent when the display is off)."""
    try:
        import send
        send._post(f"{send.BASE_URL}/missed-changed", json.dumps({"character": character}).encode("utf-8"),
                   send._read_token())
    except Exception:
        pass


def main(argv: list | None = None) -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    ap = argparse.ArgumentParser(description='"While you were away…": a recap for a player who missed a session.')
    ap.add_argument("--campaign", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("absent", help="the PCs state.md names in absent:")
    p = sub.add_parser("add", help="write a recap for one character (text from stdin, or --text)")
    p.add_argument("character")
    p.add_argument("--text", default=None)
    p.add_argument("--title", default="", help='the card\'s title (default: "While you were away…" in the screen\'s language)')
    p.add_argument("--session", default="", help="the session they missed (a number, as in session-log.md)")
    p = sub.add_parser("list", help="every recap, and who has read it")
    p.add_argument("--unread", action="store_true")
    sub.add_parser("show", help="what one character will find").add_argument("character")
    sub.add_parser("remove", help="take a recap away").add_argument("id")
    args = ap.parse_args(argv)

    try:
        camp = _campaign(args.campaign)
        if args.cmd == "absent":
            names = absent(camp)
            print(", ".join(names) if names else "nobody is absent (state.md → absent: is empty)")
            return 0
        path = data_path(camp)
        recaps = load(path)
        if args.cmd == "list":
            rows = [r for r in recaps if not (args.unread and r.get("read_at"))]
            print("\n".join(describe(r) for r in rows) or ("no unread recap" if args.unread else "no recap yet"))
            return 0
        if args.cmd == "show":
            who = resolve(party(camp), args.character)
            rows = for_character(recaps, who)
            print("\n\n".join(describe(r) + "\n\n" + r["text"] for r in rows) or f"no recap for {who}")
            return 0
        if args.cmd == "remove":
            gone = remove(recaps, args.id)
            save(path, recaps)
            _announce(gone["character"])
            print(f"- removed: {describe(gone)}")
            return 0
        who = resolve(party(camp), args.character)
        text = args.text if args.text is not None else ("" if sys.stdin.isatty() else sys.stdin.read())
        entry = add(recaps, who, text, args.title, args.session)
        save(path, recaps)
        _announce(who)
        print(f"+ {describe(entry)} — {len(entry['text'].split())} words; "
              f"{who}'s screen opens it the next time they open the display")
        return 0
    except MissedError as e:
        print(f"missed: {e}. Nothing was changed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
