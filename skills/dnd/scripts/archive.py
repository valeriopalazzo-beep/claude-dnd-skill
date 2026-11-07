#!/usr/bin/env python3
"""The cold half of state.md: `state-archive.md`, read by the piece (ADR 0003, 0004).

state.md is read whole at every load and resume, so what is history — faction
moves of past sessions, old recent events, the continuity archive, closed arc
chapters, flags that no longer hold — moves to `<campaign>/state-archive.md`,
with the same `## ` headings and one `### ` entry per faction, session or
chapter. Nothing is forgotten: the DM asks for the piece an event needs before
it happens, and reads the whole archive in the prep at the end of the night.

Reading:
    archive.py index  -c NAME                     sections and entries, with sizes
    archive.py show   -c NAME "Faction Moves" ["Figli dell'Inverno"]
                                                  one section, or one entry of it
    archive.py grep   -c NAME Solmund "Corvo Basso"
                                                  every archived line naming any term
    archive.py all    -c NAME                     the whole archive (the prep)
Writing (at /dm:dnd save):
    archive.py rotate -c NAME [--keep-sessions 2] [--dry-run]
        moves what carries a session number older than the last N sessions:
        Faction Moves bullets (filed by faction), Recent Events bullets (filed
        by session), and every `### ` entry of the Continuity Archive
    archive.py move   -c NAME "Live State Flags" (--bullet TEXT | --entry HEADING) [--dry-run]
        moves the bullets containing TEXT, or one `### ` entry, as they are
    archive.py add    -c NAME "Campaign Arc" [--entry HEADING] < text
        files text cut from state.md by hand (an arc chapter, old steering notes)
A campaign without state-archive.md works as before: reads print nothing.
"""
import argparse
import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ARCHIVE = "state-archive.md"
HEADER = ("# Archive — the cold half of state.md\n"
          "*What no longer happens every turn but must not be forgotten. Read it by the piece with "
          "`scripts/archive.py` (show / grep) before an event that touches it, and whole in the prep. "
          "Newest first in every section.*\n")
SESSION = re.compile(r"^-\s+(?:\*\*)?(Sessione|Session)\s+(\d+)", re.I)
FACTION = re.compile(r"^-\s+(?:\*\*)?(?:Sessione|Session)\s+\d+[^—*]*?—\s*([^:*]+?)\s*(?::\*\*|\*\*:?|:)", re.I)
POINTER = "*Older entries: `state-archive.md` (`archive.py show -c <campaign> \"{}\"`).*"


# ── a small Markdown model: sections (## ) holding entries (### ) ─────────────

def split(text, level):
    """[(heading or None, lines)] cut at headings of `level` ('## ' or '### '), outside code fences."""
    out = [[None, []]]
    fence = False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            fence = not fence
            out[-1][1].append(line)
            continue
        if not fence and line.startswith(level):
            out.append([line.rstrip("\r\n"), [line]])
        else:
            out[-1][1].append(line)
    return [(h, "".join(ls)) for h, ls in out]


def join(parts):
    return "".join(body for _, body in parts)


def _name(heading, level):
    return heading[len(level):].strip() if heading else ""


def _find(parts, name, level):
    key = name.strip().lower()
    for i, (h, _) in enumerate(parts):
        if h and _name(h, level).lower() == key:
            return i
    for i, (h, _) in enumerate(parts):
        if h and _name(h, level).lower().startswith(key):
            return i
    return None


def bullets(body):
    """Split a body into [(kind, text)]: 'bullet' items (with their indented lines) and 'text'."""
    out = []
    fence = False
    for line in body.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            fence = not fence
        if not fence and line.startswith("- "):
            out.append(["bullet", line])
        elif not fence and out and out[-1][0] == "bullet" and line[:1] in (" ", "\t") and line.strip():
            out[-1][1] += line
        else:
            out.append(["text", line])
    return out


# ── the archive file ─────────────────────────────────────────────────────────

def _blank_section(name):
    return (f"## {name}\n", f"## {name}\n\n")


def _ensure_section(parts, name):
    i = _find(parts, name, "## ")
    if i is None:
        h, body = _blank_section(name)
        if not parts[-1][1].endswith("\n\n"):
            parts[-1] = (parts[-1][0], parts[-1][1].rstrip("\n") + "\n\n")
        parts.append((h.rstrip("\n"), body))
        i = len(parts) - 1
    return i


def file_into(archive_text, section, items, entry=None):
    """Put `items` (text blocks, newest first) at the top of `section` (and of its `### entry`)."""
    if not archive_text.strip():
        archive_text = HEADER + "\n"
    parts = split(archive_text, "## ")
    i = _ensure_section(parts, section)
    h, body = parts[i]
    block = "".join(x if x.endswith("\n") else x + "\n" for x in items)
    if entry is None:
        head, _, rest = body.partition("\n")
        intro, entries = _intro_and_entries(rest)
        body = head + "\n" + _insert_top(intro, block) + entries
    else:
        subs = split(body, "### ")
        j = _find(subs, entry, "### ")
        if j is None:
            new = (f"### {entry}", f"### {entry}\n{block}\n")
            # a new entry goes first, after the section's own intro
            subs.insert(1, new)
        else:
            sh, sbody = subs[j]
            first, _, rest = sbody.partition("\n")
            subs[j] = (sh, first + "\n" + block + rest)
        body = join(subs)
    if not body.endswith("\n\n"):
        body = body.rstrip("\n") + "\n\n"
    parts[i] = (h, body)
    return join(parts)


def _intro_and_entries(rest):
    subs = split(rest, "### ")
    return subs[0][1], "".join(b for _, b in subs[1:])


def _insert_top(intro, block):
    """Bullets go after an italic note line (if any) and before older bullets."""
    lines = intro.splitlines(keepends=True)
    k = 0
    while k < len(lines) and (not lines[k].strip() or lines[k].lstrip().startswith("*")) and not lines[k].startswith("- "):
        k += 1
    return "".join(lines[:k]) + block + "".join(lines[k:])


# ── moves ────────────────────────────────────────────────────────────────────

def _session_of(bullet):
    m = SESSION.match(bullet)
    return (m.group(1), int(m.group(2))) if m else None


def _faction_of(bullet):
    m = FACTION.match(bullet)
    if not m:
        return "Other"
    name = re.sub(r"\s*\([^)]*\)\s*$", "", m.group(1)).strip()
    return name[:1].upper() + name[1:] if name else "Other"


def _rotate_bullets(state_parts, archive_text, section, keep, by):
    i = _find(state_parts, section, "## ")
    if i is None:
        return archive_text, 0
    h, body = state_parts[i]
    items = bullets(body)
    numbers = [s[1] for k, t in items if k == "bullet" for s in [_session_of(t)] if s]
    if not numbers:
        return archive_text, 0
    cutoff = max(numbers) - keep + 1
    stay, groups, order = [], {}, []
    for kind, text in items:
        s = _session_of(text) if kind == "bullet" else None
        if s and s[1] < cutoff:
            key = by(text, s)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(text)
        else:
            stay.append(text)
    moved = sum(len(v) for v in groups.values())
    if not moved:
        return archive_text, 0
    if by is _by_session:   # newest session first in the archive
        order.sort(key=lambda k: -int(re.search(r"\d+", k).group()))
    for key in reversed(order):
        archive_text = file_into(archive_text, section, groups[key], entry=key)
    new_body = "".join(stay)
    state_parts[i] = (h, _with_pointer(new_body, _name(h, "## ")))
    return archive_text, moved


def _with_pointer(body, name):
    """The section keeps one line saying where its older entries went, after its own italic notes."""
    line = POINTER.format(name)
    if line in body:
        return body
    head, _, rest = body.partition("\n")
    lines = rest.splitlines(keepends=True)
    k = 0
    while k < len(lines) and lines[k].lstrip().startswith("*") and not lines[k].startswith("- "):
        k += 1
    return head + "\n" + "".join(lines[:k]) + line + "\n" + "".join(lines[k:])


def _by_faction(text, s):
    return _faction_of(text)


def _by_session(text, s):
    return f"{s[0][:1].upper()}{s[0][1:].lower()} {s[1]}"


def rotate(state_text, archive_text, keep=2):
    """Move old faction moves, old recent events and the continuity archive. Returns (state, archive, report)."""
    parts = split(state_text, "## ")
    report = []
    archive_text, n = _rotate_bullets(parts, archive_text, "Faction Moves", keep, _by_faction)
    if n:
        report.append(f"Faction Moves: {n} moved")
    archive_text, n = _rotate_bullets(parts, archive_text, "Recent Events", keep, _by_session)
    if n:
        report.append(f"Recent Events: {n} moved")
    i = _find(parts, "Continuity Archive", "## ")
    if i is not None:
        h, body = parts[i]
        subs = split(body, "### ")
        entries = subs[1:]
        if entries:
            for eh, eb in reversed(entries):
                archive_text = _file_entry(archive_text, "Continuity Archive", eb)
            parts[i] = (h, _with_pointer(subs[0][1], _name(h, "## ")).rstrip("\n") + "\n\n")
            report.append(f"Continuity Archive: {len(entries)} entries moved")
    return join(parts), archive_text, report


def _file_entry(archive_text, section, entry_body):
    """A whole `### ` entry, as it is, at the top of `section`."""
    if not archive_text.strip():
        archive_text = HEADER + "\n"
    parts = split(archive_text, "## ")
    i = _ensure_section(parts, section)
    h, body = parts[i]
    head, _, rest = body.partition("\n")
    intro, entries = _intro_and_entries(rest)
    eb = entry_body.rstrip("\n") + "\n\n"
    parts[i] = (h, head + "\n" + intro + eb + entries)
    return join(parts)


def move(state_text, archive_text, section, bullet=None, entry=None):
    parts = split(state_text, "## ")
    i = _find(parts, section, "## ")
    if i is None:
        raise SystemExit(f"archive: state.md has no section '{section}'")
    h, body = parts[i]
    name = _name(h, "## ")
    if entry:
        subs = split(body, "### ")
        j = _find(subs, entry, "### ")
        if j is None:
            raise SystemExit(f"archive: '{name}' has no entry '{entry}'")
        archive_text = _file_entry(archive_text, name, subs[j][1])
        del subs[j]
        parts[i] = (h, join(subs))
        n = 1
    else:
        stay, out = [], []
        for kind, text in bullets(body):
            (out if kind == "bullet" and bullet.lower() in text.lower() else stay).append(text)
        if not out:
            raise SystemExit(f"archive: no bullet in '{name}' contains '{bullet}'")
        archive_text = file_into(archive_text, name, out)
        parts[i] = (h, "".join(stay))
        n = len(out)
    return join(parts), archive_text, [f"{name}: {n} moved"]


# ── reads ────────────────────────────────────────────────────────────────────

def index(archive_text):
    lines = []
    for h, body in split(archive_text, "## ")[1:]:
        lines.append(f"{_name(h, '## ')}  ({len(body.encode()) // 1024 + 1} KB)")
        for eh, eb in split(body, "### ")[1:]:
            lines.append(f"  - {_name(eh, '### ')}  ({len(eb.encode())} B)")
    return "\n".join(lines)


def show(archive_text, section, entry=None):
    parts = split(archive_text, "## ")
    i = _find(parts, section, "## ")
    if i is None:
        return None
    body = parts[i][1]
    if entry is None:
        return body
    subs = split(body, "### ")
    j = _find(subs, entry, "### ")
    if j is None:
        hits = [b for h, b in subs[1:] if entry.lower() in (h or "").lower()]
        return "".join(hits) or None
    return subs[j][1]


def grep(archive_text, terms):
    terms = [t.lower() for t in terms if t.strip()]
    out = []
    for h, body in split(archive_text, "## ")[1:]:
        for eh, eb in split(body, "### "):
            where = _name(h, "## ") + (" › " + _name(eh, "### ") if eh else "")
            for line in eb.splitlines():
                if line.startswith("#"):
                    continue
                if any(t in line.lower() for t in terms):
                    out.append(f"[{where}] {line.strip()}")
    return "\n".join(out)


# ── command line ─────────────────────────────────────────────────────────────

def _paths(campaign):
    from paths import find_campaign
    d = pathlib.Path(find_campaign(campaign))
    return d / "state.md", d / ARCHIVE


def _read(p):
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def _write(p, text):
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, p)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--campaign", "-c", required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index")
    sub.add_parser("all")
    p = sub.add_parser("show")
    p.add_argument("section")
    p.add_argument("entry", nargs="?")
    p = sub.add_parser("grep")
    p.add_argument("terms", nargs="+")
    p = sub.add_parser("rotate")
    p.add_argument("--keep-sessions", type=int, default=2)
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("move")
    p.add_argument("section")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--bullet")
    g.add_argument("--entry")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("add")
    p.add_argument("section")
    p.add_argument("--entry")
    a = ap.parse_args(argv)

    state_p, arch_p = _paths(a.campaign)
    archive_text = _read(arch_p)

    if a.cmd in ("index", "all", "show", "grep"):
        if not archive_text.strip():
            print(f"archive: {a.campaign} has no {ARCHIVE} yet")
            return 0
        if a.cmd == "index":
            print(index(archive_text))
        elif a.cmd == "all":
            sys.stdout.write(archive_text)
        elif a.cmd == "show":
            out = show(archive_text, a.section, a.entry)
            if out is None:
                print(f"archive: nothing under '{a.section}'" + (f" › '{a.entry}'" if a.entry else ""))
            else:
                sys.stdout.write(out)
        else:
            print(grep(archive_text, a.terms) or "archive: no line names " + ", ".join(a.terms))
        return 0

    if a.cmd == "add":
        text = sys.stdin.read()
        if not text.strip():
            raise SystemExit("archive: nothing on stdin")
        new_archive = file_into(archive_text, a.section, [text.rstrip("\n") + "\n"], entry=a.entry)
        _write(arch_p, new_archive)
        print(f"archive: filed under {a.section}" + (f" › {a.entry}" if a.entry else ""))
        return 0

    state_text = _read(state_p)
    if not state_text:
        raise SystemExit(f"archive: no {state_p}")
    if a.cmd == "rotate":
        new_state, new_archive, report = rotate(state_text, archive_text, a.keep_sessions)
    else:
        new_state, new_archive, report = move(state_text, archive_text, a.section, a.bullet, a.entry)
    if not report:
        print("archive: nothing to move")
        return 0
    before, after = len(state_text.encode()), len(new_state.encode())
    summary = "; ".join(report) + f" — state.md {before // 1024} KB -> {after // 1024} KB"
    if a.dry_run:
        print("dry run: " + summary)
        return 0
    _write(arch_p, new_archive)      # the archive first: a crash in between duplicates, never loses
    _write(state_p, new_state)
    print("archive: " + summary)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
