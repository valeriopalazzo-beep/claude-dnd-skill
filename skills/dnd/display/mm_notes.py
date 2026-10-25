#!/usr/bin/env python3
"""mm_notes.py — the DM's own Monster Manual notes, where they exist on this PC.

<data root>/riferimenti/mm holds stat blocks for the creatures the SRD lacks:
INDICE.md (a table: Italian name | English name | ... | [file.md](file.md))
and one page per creature. The notes are personal and never part of this
repository: this module only reads them, and everything returns None when
they are not there. Used by map_render.py (senses) and bestiary.py (a page
for a creature the SRD does not have).
"""
from __future__ import annotations

import os
import re
import sys
import unicodedata

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)


def notes_dir():
    import paths
    d = paths._root() / "riferimenti" / "mm"
    return d if (d / "INDICE.md").is_file() else None


def key(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[-_'’]", " ", text).casefold().split())


def page(name: str) -> dict | None:
    """{"it", "en", "slug", "text"} for the creature named exactly (Italian or English), or None."""
    d = notes_dir()
    if d is None:
        return None
    want = key(name)
    for line in (d / "INDICE.md").read_text(encoding="utf-8", errors="replace").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        link = re.search(r"\(([^)]+\.md)\)", line)
        if len(cells) < 2 or not link or want not in (key(cells[0]), key(cells[1])):
            continue
        try:
            text = (d / link.group(1)).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        return {"it": cells[0], "en": cells[1], "slug": link.group(1)[:-3], "text": text}
    return None


def stats(text: str) -> dict:
    """The numbers bestiary.py can reveal (ac hp speed cr type), as the notes write them."""
    def grab(pattern):
        m = re.search(pattern, text, re.M)
        return m.group(1).strip() if m else ""
    ac, hp = grab(r"\*\*CA\*\*\s*(\d+)"), grab(r"\*\*PF\*\*\s*(\d+)")
    return {"ac": int(ac) if ac else "", "hp": int(hp) if hp else "",
            "speed": grab(r"\*\*Velocità\*\*\s*([^·\n]+)"),
            "cr": grab(r"·\s*GS\s*([\d/]+)"),
            "type": grab(r"^\*Manuale dei Mostri[^*]*\*\s*·\s*([^,·\n]+)")}


def senses(text: str):
    """The senses line of a page, or None when the page lists none."""
    m = re.search(r"\*\*Sensi\*\*\s*(.*?)(?:·|\*\*Linguaggi|$)", text, re.M)
    return m.group(1).strip() if m else None
