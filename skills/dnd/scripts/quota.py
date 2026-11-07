#!/usr/bin/env python3
"""How much of the 5-hour usage limit is gone, and whether to warn the table.

Claude Code hands the status line the real figure for subscribers:
`rate_limits.five_hour.used_percentage` and `resets_at`. The status line saves
that object to `<runtime>/quota.json` (see `record` below); this script reads it
back. At every natural break the DM runs it next to `context_size.py`
(SKILL.md -> *Long sessions*):

    75% or more  -> warn: about an hour left, aim to close the scene
    90% or more  -> save now: close the scene and run `/dm:dnd save`

No data means no warning, never a guess: no subscription, an old status line,
a window that has already reset, or a file older than five hours.

Usage:
    quota.py                 one line, e.g. `quota: 78% · reset 01:20 — warn: ...`
    quota.py --json          the same as JSON ({} when unknown)
    quota.py record          read the status line's JSON on stdin and save
                             its `rate_limits` (for a status line written in shell:
                             `input=$(cat); echo "$input" | python3 quota.py record`)
Exit code 0 always.
"""
import argparse
import datetime as _dt
import json
import os
import pathlib
import sys
import time

WARN_AT = 75
SAVE_AT = 90
FILE_NAME = "quota.json"
MAX_AGE = 5 * 3600          # a file older than one window says nothing


def quota_path() -> pathlib.Path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from paths import runtime_dir
        return runtime_dir() / FILE_NAME
    except Exception:
        raw = os.environ.get("DND_RUNTIME_DIR", "").strip()
        if raw:
            return pathlib.Path(raw).expanduser() / FILE_NAME
        root = os.environ.get("DND_CAMPAIGN_ROOT", "").strip() or "~/.claude/dnd"
        return pathlib.Path(root).expanduser() / ".runtime" / FILE_NAME


def _epoch(value):
    """resets_at as epoch seconds: a number (seconds or ms) or an ISO string."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
    else:
        s = str(value).strip()
        try:
            v = float(s)
        except ValueError:
            try:
                d = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
            except ValueError:
                return None
            if d.tzinfo is None:
                d = d.replace(tzinfo=_dt.timezone.utc)
            return int(d.timestamp())
    if v > 1e12:
        v /= 1000.0
    return int(v) if v > 0 else None


def _pick(d, *names):
    for n in names:
        if isinstance(d, dict) and d.get(n) is not None:
            return d.get(n)
    return None


def parse(raw: dict, now=None):
    """The five-hour window from a saved file, or None when there is nothing usable.

    Tolerant of the field names (snake or camel case) because they belong to
    Claude Code and may change. Returns {"pct", "resets_at", "level"}.
    """
    if not isinstance(raw, dict):
        return None
    now = time.time() if now is None else now
    limits = _pick(raw, "rate_limits", "rateLimits")
    if limits is None and _pick(raw, "five_hour", "fiveHour") is not None:
        limits = raw
    window = _pick(limits, "five_hour", "fiveHour")
    if not isinstance(window, dict):
        return None
    pct = _pick(window, "used_percentage", "usedPercentage")
    fraction = pct is None             # `utilization` may be a 0..1 fraction
    if fraction:
        pct = _pick(window, "utilization")
    try:
        pct = float(pct)
    except (TypeError, ValueError):
        return None
    if pct != pct or pct < 0:          # NaN or nonsense
        return None
    if fraction and pct <= 1:
        pct *= 100
    resets_at = _epoch(_pick(window, "resets_at", "resetsAt", "reset_at"))
    saved_at = _epoch(raw.get("saved_at"))
    if resets_at is not None and resets_at <= now:
        return None                    # the window has reset since: the figure is gone
    if resets_at is None and (saved_at is None or now - saved_at > MAX_AGE):
        return None
    pct = min(pct, 100.0)
    level = "save" if pct >= SAVE_AT else "warn" if pct >= WARN_AT else "fine"
    return {"pct": round(pct, 1), "resets_at": resets_at, "level": level}


def read(path=None, now=None):
    p = pathlib.Path(path) if path else quota_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return parse(raw, now)


def record(payload: dict, path=None, now=None) -> bool:
    """Save the status line's `rate_limits` with a timestamp. False when absent."""
    limits = _pick(payload, "rate_limits", "rateLimits") if isinstance(payload, dict) else None
    if not isinstance(limits, dict):
        return False
    p = pathlib.Path(path) if path else quota_path()
    data = {"rate_limits": limits, "saved_at": int(time.time() if now is None else now)}
    tmp = p.with_name(p.name + ".tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        return False
    return True


def line(q) -> str:
    if not q:
        return "quota: unknown — no data, no warning"
    pct = int(q["pct"])
    reset = ""
    if q.get("resets_at"):
        reset = " · reset " + _dt.datetime.fromtimestamp(q["resets_at"]).strftime("%H:%M")
    verdict = {
        "fine": "fine",
        "warn": f"warn ({WARN_AT}%): about an hour left — tell the table, aim to close the scene",
        "save": f"save now ({SAVE_AT}%): close the scene and run /dm:dnd save",
    }[q["level"]]
    return f"quota: {pct}%{reset} — {verdict}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", nargs="?", default="check", choices=["check", "record"])
    ap.add_argument("--json", action="store_true", help="print JSON instead of a line")
    ap.add_argument("--file", help="quota file (default <runtime>/quota.json)")
    a = ap.parse_args(argv)
    if a.action == "record":
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except ValueError:
            payload = {}
        record(payload, a.file)
        return 0
    q = read(a.file)
    print(json.dumps(q or {}) if a.json else line(q))
    return 0


if __name__ == "__main__":
    sys.exit(main())
