"""media.py — shared plumbing for campaign images (portraits, monsters, maps).

Generated files live with the campaign, not in the code dir, so they survive
`/plugin update` and travel with the campaign folder:

    <DND_CAMPAIGN_ROOT>/campaigns/<campaign>/media/<file>

The display serves them at /media/<file> for the ACTIVE campaign only, and
image_gen.py / map_render.py / send.py --image all announce a file through
post_image() → POST /image.

stdlib only.
"""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from runtime_paths import rt  # noqa: E402

sys.path.insert(0, os.path.join(_HERE, os.pardir, "scripts"))
from paths import find_campaign  # noqa: E402

CAMP_FILE = rt(".campaign")

# The server re-validates every name against this same pattern before it
# touches the filesystem — keep the two in sync (dnd-display-app.py).
MEDIA_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.(?:png|jpe?g|webp|svg)$")
KINDS = ("portrait", "monster", "scene", "action", "item", "map")


def safe_campaign(name: str) -> str:
    """Same allowlist the display uses for /character — no path pivots."""
    return re.sub(r"[^A-Za-z0-9_-]", "", name or "")[:50]


def active_campaign() -> str:
    try:
        return safe_campaign(open(CAMP_FILE, encoding="utf-8").read().strip())
    except OSError:
        return ""


def media_dir(campaign: Optional[str] = None, create: bool = True) -> Path:
    """Media folder of `campaign` (default: the display's active campaign)."""
    camp = safe_campaign(campaign) if campaign else active_campaign()
    if not camp:
        raise RuntimeError(
            "no active campaign — pass --campaign or run "
            "push_stats.py --set-campaign <name> first")
    d = find_campaign(camp) / "media"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def slug(text: str, limit: int = 40) -> str:
    """ASCII filename slug: 'Mirëlla la Rossa' → 'mirella-la-rossa'."""
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    t = re.sub(r"[^A-Za-z0-9]+", "-", t).strip("-").lower()
    return t[:limit].strip("-") or "img"


# ── index.json: what was generated, from which prompt ────────────────────────
# Lets the DM reuse the exact description (and seed) next time, which is what
# keeps a recurring NPC looking like the same person.

def _index_path(d: Path) -> Path:
    return d / "index.json"


def load_index(d: Path) -> list:
    try:
        data = json.loads(_index_path(d).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def add_to_index(d: Path, entry: dict) -> None:
    items = [e for e in load_index(d) if e.get("file") != entry.get("file")]
    items.append(entry)
    tmp = _index_path(d).with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, _index_path(d))


# ── Display announcement ─────────────────────────────────────────────────────

def post_image(file: str, caption: str = "", kind: str = "scene",
               subject: str = "") -> bool:
    """Tell the display to show media/<file>. False if the display is offline."""
    import send  # imported lazily: send.py resolves the display URL at import
    body = {"file": file, "caption": caption, "kind": kind, "subject": subject}
    return send._post(f"{send.BASE_URL}/image",
                      json.dumps(body).encode("utf-8"), send._read_token())
