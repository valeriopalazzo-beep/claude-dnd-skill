#!/usr/bin/env python3
"""How long the conversation is now, and whether a checkpoint is due.

Every tool call reads the whole conversation again, so a session that has grown
to 600k tokens costs four times one at 150k for the same turn. At a natural
break the DM runs this and, when it says `due`, runs `/dm:dnd checkpoint`
(SKILL.md -> *Long sessions*).

The size is the last model call's input (fresh + cache read + cache write),
taken from Claude Code's own transcript of this session: the file named by
CLAUDE_CODE_SESSION_ID under ~/.claude/projects (or CLAUDE_CONFIG_DIR), or the
newest transcript when that variable is missing.

Usage:
    context_size.py [--limit 300000] [--transcript FILE]
Prints one line, e.g.  `context: 412k tokens — checkpoint due (limit 300k)`.
Exit code 0 always; 2 only when no transcript is found.
"""
import argparse
import json
import os
import pathlib
import sys

DEFAULT_LIMIT = 300_000
TAIL_BYTES = 4 * 1024 * 1024


def find_transcript():
    base = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or pathlib.Path.home() / ".claude") / "projects"
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    if sid:
        hits = sorted(base.glob(f"*/{sid}.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if hits:
            return hits[0]
    files = sorted(base.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def last_context(path):
    """Input tokens of the newest model call in the transcript, or None."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - TAIL_BYTES))
        lines = f.read().decode("utf-8", errors="replace").splitlines()
    for line in reversed(lines):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") != "assistant":
            continue
        u = (d.get("message") or {}).get("usage") or {}
        total = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                 + u.get("cache_creation_input_tokens", 0))
        if total:
            return total
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help="tokens above which a checkpoint is due (default 300000)")
    ap.add_argument("--transcript", help="transcript file (default: this session's)")
    a = ap.parse_args(argv)
    path = pathlib.Path(a.transcript) if a.transcript else find_transcript()
    if not path or not path.exists():
        print("context: unknown — no transcript found")
        return 2
    n = last_context(path)
    if n is None:
        print("context: unknown — no model call in the transcript yet")
        return 0
    verdict = "checkpoint due" if n > a.limit else "fine"
    print(f"context: {n // 1000}k tokens — {verdict} (limit {a.limit // 1000}k)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
