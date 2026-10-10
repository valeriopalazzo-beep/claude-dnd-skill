#!/usr/bin/env python3
"""backup.py — a dated copy of a campaign, so a bad save is never the only copy.

A campaign is a folder of files that get rewritten every session (state.md,
the sheets, the session log, the tracker…). This script zips that folder into
<DATA root>/backups/<campaign>/ and keeps the newest ones:

    backup.py save    --campaign corona-di-cenere --label end   # at /dm:dnd end
    backup.py list    --campaign corona-di-cenere
    backup.py restore --campaign corona-di-cenere 1 --yes       # 1 = the newest
    backup.py restore --campaign corona-di-cenere corona-di-cenere-20261010-213000-end.zip --yes

What goes in: every file of the campaign except `media/` (pictures and maps are
written once and never edited, and they are most of the weight — add `--media`
for a complete copy) plus the global roster sheets under `_roster/`.

`restore` first saves what is there now (label `pre-restore`), then puts the
campaign back exactly as it was in the archive; `media/` is left alone unless
the archive holds it. It does nothing without `--yes`.

`--keep N` (default 20) is how many archives stay per campaign; `--keep 0`
keeps them all. `--dest DIR`, or the DND_BACKUP_DIR environment variable, puts
the archives somewhere else — a synced folder, another disk.
"""
from __future__ import annotations

import argparse
import datetime
import os
import pathlib
import re
import shutil
import sys
import zipfile

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import paths  # noqa: E402  (also forces UTF-8 output on Windows)

DEFAULT_KEEP = 20
ROSTER_PREFIX = "_roster/"
MEDIA_DIR = "media"
# Written while a file is being replaced, or by the display while it runs.
_SKIP_SUFFIXES = (".tmp", ".lock")
_STAMP_RE = re.compile(r"-(\d{8}-\d{6})(?:-([a-z0-9-]+))?\.zip$")


class BackupError(Exception):
    """A request that cannot be carried out; nothing was changed."""


def backups_root(dest: str = "") -> pathlib.Path:
    raw = (dest or os.environ.get("DND_BACKUP_DIR", "")).strip()
    if raw:
        return pathlib.Path(raw).expanduser()
    return paths.campaigns_dir().parent / "backups"


def backup_dir(campaign: str, dest: str = "") -> pathlib.Path:
    return backups_root(dest) / campaign


def _campaign_path(campaign: str) -> pathlib.Path:
    if not campaign or campaign != pathlib.Path(campaign).name or campaign.startswith("."):
        raise BackupError(f"not a campaign name: {campaign!r}")
    path = paths.find_campaign(campaign)
    if not path.is_dir():
        raise BackupError(f"no campaign folder at {path}")
    return path


def _label(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:24]


def _files(root: pathlib.Path, media: bool) -> list:
    out = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.endswith(_SKIP_SUFFIXES):
            continue
        rel = path.relative_to(root)
        if not media and rel.parts[0] == MEDIA_DIR:
            continue
        out.append((path, rel.as_posix()))
    return out


def archives(campaign: str, dest: str = "") -> list:
    """This campaign's archives, newest first."""
    folder = backup_dir(campaign, dest)
    found = [p for p in folder.glob(f"{campaign}-*.zip") if _STAMP_RE.search(p.name)]
    return sorted(found, key=lambda p: _STAMP_RE.search(p.name).group(1), reverse=True)


def save(campaign: str, label: str = "", media: bool = False, keep: int = DEFAULT_KEEP,
         dest: str = "", now: datetime.datetime | None = None) -> pathlib.Path:
    """Write one archive, check it reads back, drop the oldest beyond `keep`."""
    root = _campaign_path(campaign)
    files = _files(root, media)
    if not files:
        raise BackupError(f"{root} is empty")
    folder = backup_dir(campaign, dest)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.datetime.now()).strftime("%Y%m%d-%H%M%S")
    tag = _label(label)
    target = folder / f"{campaign}-{stamp}{'-' + tag if tag else ''}.zip"
    tmp = target.with_suffix(".zip.part")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for path, rel in files:
                z.write(path, rel)
            roster = paths.characters_dir()
            if roster.is_dir():
                for path in sorted(roster.glob("*.md")):
                    z.write(path, ROSTER_PREFIX + path.name)
        with zipfile.ZipFile(tmp) as z:
            bad = z.testzip()
            if bad:
                raise BackupError(f"the archive does not read back ({bad})")
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()
    if keep > 0:
        for old in archives(campaign, dest)[keep:]:
            old.unlink()
    return target


def pick(campaign: str, which: str, dest: str = "") -> pathlib.Path:
    """An archive by number (1 = newest) or by file name."""
    found = archives(campaign, dest)
    if not found:
        raise BackupError(f"no backups of {campaign!r} in {backup_dir(campaign, dest)}")
    if which.isdigit():
        n = int(which)
        if not 1 <= n <= len(found):
            raise BackupError(f"there are {len(found)} backups; {n} is not one of them")
        return found[n - 1]
    for p in found:
        if p.name == pathlib.Path(which).name:
            return p
    raise BackupError(f"no backup named {which!r}")


def restore(campaign: str, which: str, dest: str = "", roster: bool = False) -> tuple:
    """Put the campaign back as it was in the archive. Returns (archive, safety copy)."""
    archive = pick(campaign, which, dest)
    root = _campaign_path(campaign)
    with zipfile.ZipFile(archive) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        for n in names:
            if n.startswith(("/", "\\")) or ".." in pathlib.PurePosixPath(n).parts or ":" in n:
                raise BackupError(f"unsafe path in the archive: {n!r}")
        own = [n for n in names if not n.startswith(ROSTER_PREFIX)]
        if not own:
            raise BackupError(f"{archive.name} holds no campaign files")
        has_media = any(n.split("/")[0] == MEDIA_DIR for n in own)
        safety = save(campaign, "pre-restore", media=has_media, keep=0, dest=dest)
        for path, _rel in _files(root, has_media):
            path.unlink()
        for n in own:
            out = root.joinpath(*pathlib.PurePosixPath(n).parts)
            out.parent.mkdir(parents=True, exist_ok=True)
            with z.open(n) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
        if roster:
            folder = paths.characters_dir()
            folder.mkdir(parents=True, exist_ok=True)
            for n in names:
                if n.startswith(ROSTER_PREFIX) and n.endswith(".md"):
                    with z.open(n) as src, open(folder / pathlib.PurePosixPath(n).name, "wb") as dst:
                        shutil.copyfileobj(src, dst)
    for folder in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
        try:
            folder.rmdir()                     # only the ones the restore emptied
        except OSError:
            pass
    return archive, safety


def _describe(path: pathlib.Path) -> str:
    m = _STAMP_RE.search(path.name)
    when = datetime.datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").strftime("%Y-%m-%d %H:%M")
    size = path.stat().st_size
    human = f"{size / 1048576:.1f} MB" if size >= 1048576 else f"{max(1, size // 1024)} KB"
    return f"{when}  {human:>9}  {m.group(2) or '':<12} {path.name}"


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="Dated copies of a campaign folder.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd in ("save", "list", "restore"):
        p = sub.add_parser(cmd)
        p.add_argument("--campaign", "-c", required=True)
        p.add_argument("--dest", default="", help="where the archives live (default: <DATA root>/backups)")
        if cmd == "save":
            p.add_argument("--label", default="", help="a word for the file name: end, pre-level-up…")
            p.add_argument("--media", action="store_true", help="include media/ (pictures and maps)")
            p.add_argument("--keep", type=int, default=DEFAULT_KEEP, help="archives kept (0 = all)")
        if cmd == "restore":
            p.add_argument("which", help="number from `list` (1 = newest) or file name")
            p.add_argument("--roster", action="store_true", help="also put back the global roster sheets")
            p.add_argument("--yes", action="store_true", help="really replace the campaign's files")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "save":
            path = save(args.campaign, args.label, args.media, args.keep, args.dest)
            print(f"backup: {path}")
            print(f"  {len(archives(args.campaign, args.dest))} kept for {args.campaign}")
        elif args.cmd == "list":
            found = archives(args.campaign, args.dest)
            if not found:
                print(f"no backups of {args.campaign} in {backup_dir(args.campaign, args.dest)}")
            for i, p in enumerate(found, 1):
                print(f"{i:>3}. {_describe(p)}")
        elif args.cmd == "restore":
            archive = pick(args.campaign, args.which, args.dest)
            if not args.yes:
                print(f"would restore {archive.name} over {_campaign_path(args.campaign)}")
                print("  the files there now are saved first; run again with --yes to do it")
                return 2
            archive, safety = restore(args.campaign, args.which, args.dest, args.roster)
            print(f"restored {archive.name}")
            print(f"  what was there before: {safety.name}")
    except BackupError as e:
        print(f"backup: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
