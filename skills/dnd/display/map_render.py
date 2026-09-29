#!/usr/bin/env python3
"""map_render.py — turn an ASCII battle map into an SVG and show it on the display.

The DM (Claude) draws the map as text; this script owns every pixel, so the
output is deterministic, free, instant, and safe to serve (all text escaped).

Input (stdin or --file). Header and legend are optional; `---` separates them:

    title: Cripta di Vessar
    scale: 1 quadretto = 1,5 m
    ---
    ###########
    #..A...g..#
    #..B..%%..+
    #~~~..&...#
    ###########
    ---
    A: Aldric | pc
    B: Mira | pc
    g: Goblin arciere | foe

Terrain (letters are never terrain — they are always tokens):
    #  wall            .  floor          (space) or _  void / off-map
    +  door            ~  water          ^  trap / hazard
    *  tree / bush     %  difficult terrain (rubble, undergrowth)
    ,  grass / open ground               =  furniture (table, altar, crate)
    &  pillar / statue <  stairs up      >  stairs down
    !  fire / brazier

Tokens: any letter. Legend line `X: Name | side` — side is one of
pc, ally, npc, foe, neutral. Without a legend entry, UPPERCASE = pc and
lowercase = foe, labelled with the letter itself.

Usage:
    python3 map_render.py << 'DNDEND'  ...map...  DNDEND      # render + show
    python3 map_render.py --file map.txt --no-send             # render only
    python3 map_render.py --symbols                            # print the key
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

for _stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

CELL = 40            # px per square
PAD = 28             # room for the coordinate ruler
MAX_W, MAX_H = 60, 60

TERRAIN = {
    "#": "wall", ".": "floor", " ": "void", "_": "void", "+": "door",
    "~": "water", "^": "trap", "*": "tree", "%": "rough", ",": "grass",
    "=": "furniture", "&": "pillar", "<": "stairs_up", ">": "stairs_down",
    "!": "fire",
}
SIDES = {
    "pc": "#2f6fb0", "ally": "#3f8f4f", "npc": "#b8862b",
    "foe": "#b03a2e", "neutral": "#6f6a62",
}
SIDE_ALIASES = {"enemy": "foe", "monster": "foe", "hostile": "foe",
                "player": "pc", "friend": "ally", "friendly": "ally"}

C = {  # palette — parchment, so the map reads the same in light and dark UI
    "bg": "#efe4c8", "floor": "#e4d4ac", "grid": "#b7a176",
    "wall": "#4b4036", "wall_edge": "#2f2821", "water": "#7fa9bf",
    "water_line": "#d9ecf3", "grass": "#c9d49a", "ink": "#3b2f22",
    "door": "#8a5a2b", "tree": "#4f7a3a", "tree_dark": "#355a26",
    "stone": "#8d8579", "trap": "#b03a2e", "fire": "#e0782a", "fire_core": "#f6c64a",
    "wood": "#9a6a3a",
}


@dataclass
class MapSpec:
    title: str = ""
    scale: str = ""
    rows: list = field(default_factory=list)
    legend: dict = field(default_factory=dict)   # letter → (name, side)
    warnings: list = field(default_factory=list)


def parse(text: str) -> MapSpec:
    spec = MapSpec()
    lines = text.replace("\r", "").replace("\t", "    ").split("\n")
    sections, cur = [], []
    for ln in lines:
        if ln.strip() == "---":
            sections.append(cur)
            cur = []
        else:
            cur.append(ln)
    sections.append(cur)

    def is_header(sec):
        body = [l for l in sec if l.strip()]
        return bool(body) and all(":" in l and l.split(":", 1)[0].strip().lower()
                                  in ("title", "scale", "titolo", "scala") for l in body)

    if len(sections) >= 2 and is_header(sections[0]):
        for l in sections[0]:
            if ":" in l:
                k, v = l.split(":", 1)
                k = k.strip().lower()
                if k in ("title", "titolo"):
                    spec.title = v.strip()
                elif k in ("scale", "scala"):
                    spec.scale = v.strip()
        sections = sections[1:]

    grid = sections[0]
    # Trim blank lines around the grid but keep interior spacing.
    while grid and not grid[0].strip():
        grid = grid[1:]
    while grid and not grid[-1].strip():
        grid = grid[:-1]
    if not grid:
        raise ValueError("empty map: no grid rows found")
    if len(grid) > MAX_H or max(len(r) for r in grid) > MAX_W:
        raise ValueError(f"map too large (max {MAX_W}x{MAX_H} squares)")
    # Drop indentation common to every row (heredocs are often indented).
    indent = min(len(r) - len(r.lstrip(" ")) for r in grid if r.strip())
    grid = [r[indent:].rstrip() for r in grid]
    width = max(len(r) for r in grid)
    spec.rows = [r.ljust(width) for r in grid]

    for sec in sections[1:]:
        for l in sec:
            if ":" not in l:
                continue
            k, v = l.split(":", 1)
            k = k.strip()
            if len(k) != 1 or not k.isalpha():
                continue
            name, _, side = v.partition("|")
            side = side.strip().lower()
            side = SIDE_ALIASES.get(side, side)
            if side not in SIDES:
                side = "pc" if k.isupper() else "foe"
            spec.legend[k] = (name.strip() or k, side)

    for y, row in enumerate(spec.rows):
        for x, ch in enumerate(row):
            if ch.isalpha() or ch in TERRAIN:
                continue
            spec.warnings.append(f"unknown symbol {ch!r} at col {x + 1}, row {y + 1} — drawn as floor")
    return spec


# ── SVG drawing ──────────────────────────────────────────────────────────────

def _cell_kind(spec: MapSpec, x: int, y: int) -> str:
    if y < 0 or y >= len(spec.rows) or x < 0 or x >= len(spec.rows[0]):
        return "void"
    ch = spec.rows[y][x]
    if ch.isalpha():
        return "floor"
    return TERRAIN.get(ch, "floor")


def _draw_cell(kind: str, spec: MapSpec, x: int, y: int) -> list:
    px, py, s = PAD + x * CELL, PAD + y * CELL, CELL
    cx, cy = px + s / 2, py + s / 2
    out = []
    if kind == "void":
        return out      # off-map: leave the parchment showing
    base = {"wall": C["wall"], "water": C["water"],
            "grass": C["grass"]}.get(kind, C["floor"])
    out.append(f'<rect x="{px}" y="{py}" width="{s}" height="{s}" fill="{base}"/>')

    if kind == "wall":
        # Hatching so walls read as solid even in greyscale.
        for i in range(-s, s, 8):
            out.append(f'<line x1="{px + i}" y1="{py + s}" x2="{px + i + s}" y2="{py}" '
                       f'stroke="{C["wall_edge"]}" stroke-width="1.2" clip-path="url(#c{x}_{y})"/>')
        out.insert(0, f'<clipPath id="c{x}_{y}"><rect x="{px}" y="{py}" width="{s}" height="{s}"/></clipPath>')
    elif kind == "water":
        for k in (0.35, 0.65):
            yy = py + s * k
            out.append(f'<path d="M{px + 6},{yy} q{s / 8},-4 {s / 4},0 t{s / 4},0 t{s / 4},0" '
                       f'fill="none" stroke="{C["water_line"]}" stroke-width="1.5"/>')
    elif kind == "door":
        # Orient the door along the wall it sits in.
        horiz = _cell_kind(spec, x - 1, y) == "wall" or _cell_kind(spec, x + 1, y) == "wall"
        if horiz:
            out.append(f'<rect x="{px}" y="{cy - 5}" width="{s}" height="10" fill="{C["door"]}" '
                       f'stroke="{C["ink"]}" stroke-width="1.5"/>')
        else:
            out.append(f'<rect x="{cx - 5}" y="{py}" width="10" height="{s}" fill="{C["door"]}" '
                       f'stroke="{C["ink"]}" stroke-width="1.5"/>')
    elif kind == "trap":
        out.append(f'<path d="M{cx},{py + 8} L{px + s - 8},{py + s - 9} L{px + 8},{py + s - 9} Z" '
                   f'fill="none" stroke="{C["trap"]}" stroke-width="2.5" stroke-linejoin="round"/>')
        out.append(f'<text x="{cx}" y="{py + s - 13}" font-size="13" font-weight="700" '
                   f'text-anchor="middle" fill="{C["trap"]}">!</text>')
    elif kind == "tree":
        out.append(f'<circle cx="{cx}" cy="{cy}" r="{s * 0.42}" fill="{C["tree"]}" '
                   f'stroke="{C["tree_dark"]}" stroke-width="2"/>')
        out.append(f'<circle cx="{cx - 4}" cy="{cy - 4}" r="{s * 0.16}" fill="{C["tree_dark"]}" opacity="0.5"/>')
    elif kind == "rough":
        for dx, dy, r in ((0.25, 0.3, 3.5), (0.62, 0.22, 2.5), (0.45, 0.6, 4),
                          (0.78, 0.7, 3), (0.2, 0.78, 2.5)):
            out.append(f'<circle cx="{px + s * dx}" cy="{py + s * dy}" r="{r}" fill="{C["stone"]}"/>')
    elif kind == "grass":
        for dx in (0.25, 0.55, 0.8):
            gx, gy = px + s * dx, py + s * (0.35 + dx / 3)
            out.append(f'<path d="M{gx - 3},{gy + 5} L{gx},{gy - 3} L{gx + 3},{gy + 5}" '
                       f'fill="none" stroke="{C["tree"]}" stroke-width="1.3"/>')
    elif kind == "furniture":
        out.append(f'<rect x="{px + 5}" y="{py + 8}" width="{s - 10}" height="{s - 16}" rx="3" '
                   f'fill="{C["wood"]}" stroke="{C["ink"]}" stroke-width="1.5"/>')
    elif kind == "pillar":
        out.append(f'<circle cx="{cx}" cy="{cy}" r="{s * 0.36}" fill="{C["stone"]}" '
                   f'stroke="{C["wall_edge"]}" stroke-width="2"/>')
    elif kind in ("stairs_up", "stairs_down"):
        for i in range(5):
            yy = py + 6 + i * (s - 12) / 4
            out.append(f'<line x1="{px + 6}" y1="{yy}" x2="{px + s - 6}" y2="{yy}" '
                       f'stroke="{C["ink"]}" stroke-width="1.5"/>')
        arrow = "▲" if kind == "stairs_up" else "▼"
        out.append(f'<text x="{cx}" y="{cy + 5}" font-size="14" text-anchor="middle" '
                   f'fill="{C["ink"]}">{arrow}</text>')
    elif kind == "fire":
        out.append(f'<path d="M{cx},{py + 6} C{cx + 14},{cy} {cx + 10},{py + s - 6} {cx},{py + s - 6} '
                   f'C{cx - 10},{py + s - 6} {cx - 14},{cy} {cx},{py + 6} Z" fill="{C["fire"]}"/>')
        out.append(f'<circle cx="{cx}" cy="{cy + 6}" r="5" fill="{C["fire_core"]}"/>')
    return out


def _draw_token(ch: str, spec: MapSpec, x: int, y: int) -> list:
    name, side = spec.legend.get(ch, (ch, "pc" if ch.isupper() else "foe"))
    cx, cy = PAD + x * CELL + CELL / 2, PAD + y * CELL + CELL / 2
    return [
        f'<g><title>{escape(name)}</title>',
        f'<circle cx="{cx}" cy="{cy + 1.5}" r="{CELL * 0.4}" fill="#000" opacity="0.25"/>',
        f'<circle cx="{cx}" cy="{cy}" r="{CELL * 0.4}" fill="{SIDES[side]}" stroke="#fff" stroke-width="2.5"/>',
        f'<text x="{cx}" y="{cy + 6}" font-size="17" font-weight="700" text-anchor="middle" '
        f'fill="#fff" font-family="Georgia, serif">{escape(ch)}</text></g>',
    ]


def render_svg(spec: MapSpec) -> str:
    w, h = len(spec.rows[0]), len(spec.rows)
    grid_w, grid_h = w * CELL, h * CELL
    title_h = 34 if spec.title else 0

    # Legend: tokens that actually appear on the map, in reading order.
    seen = []
    for row in spec.rows:
        for ch in row:
            if ch.isalpha() and ch not in seen:
                seen.append(ch)
    order = list(SIDES)   # pc, ally, npc, foe, neutral
    seen.sort(key=lambda ch: order.index(
        spec.legend.get(ch, (ch, "pc" if ch.isupper() else "foe"))[1]))
    cols = max(1, min(3, (grid_w + PAD) // 190))
    leg_rows = (len(seen) + cols - 1) // cols
    legend_h = (leg_rows * 24 + 16) if seen else 0
    scale_h = 20 if spec.scale else 0

    total_w = PAD + grid_w + 12
    total_h = title_h + PAD + grid_h + 12 + legend_h + scale_h

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="{total_h}" '
           f'viewBox="0 0 {total_w} {total_h}" font-family="Georgia, \'Times New Roman\', serif">',
           f'<rect width="100%" height="100%" fill="{C["bg"]}"/>']
    if spec.title:
        out.append(f'<text x="{total_w / 2}" y="24" font-size="19" text-anchor="middle" '
                   f'fill="{C["ink"]}" letter-spacing="1">{escape(spec.title)}</text>')

    out.append(f'<g transform="translate(0,{title_h})">')
    for y, row in enumerate(spec.rows):
        for x, ch in enumerate(row):
            out.extend(_draw_cell(_cell_kind(spec, x, y), spec, x, y))
    # Grid lines over playable squares only (not over void).
    for y, row in enumerate(spec.rows):
        for x, ch in enumerate(row):
            if _cell_kind(spec, x, y) != "void":
                out.append(f'<rect x="{PAD + x * CELL}" y="{PAD + y * CELL}" width="{CELL}" '
                           f'height="{CELL}" fill="none" stroke="{C["grid"]}" stroke-width="0.8"/>')
    for y, row in enumerate(spec.rows):
        for x, ch in enumerate(row):
            if ch.isalpha():
                out.extend(_draw_token(ch, spec, x, y))
    # Coordinate ruler: column numbers on top, row numbers on the left, so the
    # table can say "I move to 7,3".
    for x in range(w):
        out.append(f'<text x="{PAD + x * CELL + CELL / 2}" y="{PAD - 9}" font-size="11" '
                   f'text-anchor="middle" fill="{C["ink"]}" opacity="0.6">{x + 1}</text>')
    for y in range(h):
        out.append(f'<text x="{PAD - 7}" y="{PAD + y * CELL + CELL / 2 + 4}" font-size="11" '
                   f'text-anchor="end" fill="{C["ink"]}" opacity="0.6">{y + 1}</text>')
    out.append('</g>')

    ly = title_h + PAD + grid_h + 22
    col_w = (total_w - PAD) / cols
    for i, ch in enumerate(seen):
        name, side = spec.legend.get(ch, (ch, "pc" if ch.isupper() else "foe"))
        lx = PAD + (i % cols) * col_w
        yy = ly + (i // cols) * 24
        out.append(f'<circle cx="{lx + 9}" cy="{yy - 5}" r="9" fill="{SIDES[side]}" stroke="#fff" stroke-width="1.5"/>')
        out.append(f'<text x="{lx + 9}" y="{yy - 0.5}" font-size="11" font-weight="700" '
                   f'text-anchor="middle" fill="#fff">{escape(ch)}</text>')
        out.append(f'<text x="{lx + 24}" y="{yy}" font-size="13" fill="{C["ink"]}">{escape(name[:28])}</text>')
    if spec.scale:
        out.append(f'<text x="{total_w - 12}" y="{total_h - 8}" font-size="11" text-anchor="end" '
                   f'font-style="italic" fill="{C["ink"]}" opacity="0.7">{escape(spec.scale)}</text>')
    out.append('</svg>')
    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser(description="Render an ASCII battle map to SVG and show it on the display.")
    p.add_argument("--file", help="read the map from this file instead of stdin")
    p.add_argument("--campaign", help="campaign name (default: the display's active campaign)")
    p.add_argument("--out", help="write the SVG here instead of the campaign media folder (implies --no-send)")
    p.add_argument("--name", help="map id — re-sending the same id marks the older map on the display as superseded "
                                  "(default: derived from the title)")
    p.add_argument("--caption", help="caption under the map (default: the title)")
    p.add_argument("--no-send", action="store_true", help="render only; do not push to the display")
    p.add_argument("--symbols", action="store_true", help="print the terrain/token key and exit")
    args = p.parse_args()

    if args.symbols:
        key = __doc__.split("Terrain", 1)[1].split("Usage:", 1)[0]
        print("Terrain" + key.rstrip())
        return 0

    text = open(args.file, encoding="utf-8").read() if args.file else sys.stdin.read()
    try:
        spec = parse(text)
    except ValueError as e:
        print(f"map_render: {e}", file=sys.stderr)
        return 2
    for w in spec.warnings[:10]:
        print(f"map_render: {w}", file=sys.stderr)
    svg = render_svg(spec)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(svg)
        print(args.out)
        return 0

    import media
    try:
        d = media.media_dir(args.campaign)
    except RuntimeError as e:
        print(f"map_render: {e}", file=sys.stderr)
        return 2
    map_id = media.slug(args.name or spec.title or "map")
    digest = hashlib.sha1(svg.encode("utf-8")).hexdigest()[:8]
    fname = f"map-{map_id}-{digest}.svg"
    (d / fname).write_text(svg, encoding="utf-8")
    print(str(d / fname))

    if not args.no_send:
        caption = args.caption if args.caption is not None else spec.title
        if not media.post_image(fname, caption=caption, kind="map", subject=map_id):
            print("map_render: display offline — map saved but not shown", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
