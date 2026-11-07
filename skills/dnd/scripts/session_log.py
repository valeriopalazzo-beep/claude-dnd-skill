#!/usr/bin/env python3
"""One entry of session-log.md instead of the whole file.

A `/dm:dnd resume` needs only tonight's entry, the one a checkpoint wrote with
`*(in corso — …)*` in its heading; the rest of the log is history it already
holds in state.md. Reading the whole file at every resume put thousands of
tokens back into every call of the conversation (ADR 0007).

Usage:
    session_log.py current  --campaign NAME   tonight's entry: the one marked
                                              "(in corso" / "(in progress"; when
                                              none is, the newest entry
    session_log.py entry N  --campaign NAME   the entry of session N
    session_log.py headings --campaign NAME   the `## ` headings, one per line
    --file PATH reads another file in place of <campaign>/session-log.md.
Exit code 0, or 2 when the file or the entry is missing.
"""
import argparse
import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RUNNING = re.compile(r"\((?:in corso|in progress)\b", re.I)
NUMBER = re.compile(r"^##\s+(?:Session|Sessione)\s+(\d+)", re.I)


def entries(text: str):
    """[(heading, body_with_heading)] for every `## ` entry, in file order."""
    out, cur = [], None
    fence = False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            fence = not fence
        if not fence and line.startswith("## "):
            cur = [line.rstrip("\r\n"), [line]]
            out.append(cur)
        elif cur is not None:
            cur[1].append(line)
    return [(h, "".join(b).rstrip() + "\n") for h, b in out]


def _number(heading: str):
    m = NUMBER.match(heading)
    return int(m.group(1)) if m else None


def current(text: str):
    es = entries(text)
    if not es:
        return None
    running = [e for e in es if RUNNING.search(e[0])]
    if running:
        return running[-1][1]
    numbered = [e for e in es if _number(e[0]) is not None]
    if numbered:
        return max(numbered, key=lambda e: _number(e[0]))[1]
    return es[-1][1]


def entry(text: str, n: int):
    for h, body in entries(text):
        if _number(h) == n:
            return body
    return None


def _log_path(a) -> pathlib.Path:
    if a.file:
        return pathlib.Path(a.file)
    from paths import find_campaign
    return pathlib.Path(find_campaign(a.campaign)) / "session-log.md"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["current", "entry", "headings"])
    ap.add_argument("n", nargs="?", type=int, help="session number (entry)")
    ap.add_argument("--campaign")
    ap.add_argument("--file")
    a = ap.parse_args(argv)
    if not a.file and not a.campaign:
        ap.error("--campaign or --file is required")
    p = _log_path(a)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        print(f"session_log: no {p}", file=sys.stderr)
        return 2
    if a.action == "headings":
        for h, _ in entries(text):
            print(h)
        return 0
    if a.action == "entry":
        if a.n is None:
            ap.error("entry needs a session number")
        out = entry(text, a.n)
    else:
        out = current(text)
    if out is None:
        print("session_log: no such entry", file=sys.stderr)
        return 2
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
