#!/usr/bin/env python3
"""compose.py — a rough depth sketch that pins WHERE each subject of an image goes.

Small image models lose the second subject of a scene: "a dwarf cleaving an
ash hound" comes back as a dwarf alone, or two dwarves. image_gen.py can send
this sketch to a depth ControlNet on the local backend (Forge / A1111): the
silhouettes fix the composition, the prompt still decides what they look like.

Spec — comma-separated figures, each `shape:position[:size]`:

    humanoid:left, quadruped:right
    humanoid-short:0.35:large, beast:0.7:large
    humanoid:center:large, object:right:small

Shapes:    humanoid, humanoid-short (dwarf, halfling), humanoid-tall, quadruped
           (dog, wolf, horse), beast (bear, big cat), flyer (bird, bat, dragon in
           flight), serpent, object (a chest, an altar, a boulder)

Only SOLID figures. Explosions, clouds, fire and portals are not surfaces: a
depth sketch of them came back as metal spheres (SDXL + control-lora-depth,
2026-09-30). Describe effects in the prompt and leave them out of the spec.
Positions: far-left, left, center, right, far-right, or a number 0..1
Sizes:     small, medium, large (default medium); larger also reads nearer

Depth convention (MiDaS style): white is near, black is far.

stdlib only.
"""
from __future__ import annotations

import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

SHAPES = ("humanoid", "humanoid-short", "humanoid-tall", "quadruped", "beast", "flyer",
          "serpent", "object")
EFFECTS = ("blast", "explosion", "cloud", "fire", "burst", "smoke", "portal", "gate", "light")
SHAPE_ALIASES = {
    "human": "humanoid", "person": "humanoid", "dwarf": "humanoid-short", "halfling": "humanoid-short",
    "gnome": "humanoid-short", "giant": "humanoid-tall", "ogre": "humanoid-tall", "troll": "humanoid-tall",
    "dog": "quadruped", "wolf": "quadruped", "hound": "quadruped", "horse": "quadruped",
    "bear": "beast", "cat": "beast", "bird": "flyer", "bat": "flyer", "dragon": "flyer",
    "snake": "serpent", "worm": "serpent", "chest": "object", "altar": "object", "rock": "object",
}
POSITIONS = {"far-left": 0.14, "left": 0.3, "center": 0.5, "centre": 0.5, "right": 0.7, "far-right": 0.86}
SIZES = {"small": 0.42, "medium": 0.62, "large": 0.86}   # share of the image height


class ComposeError(ValueError):
    pass


def parse(spec: str) -> list:
    """[(shape, x 0..1, size 0..1), ...] — raises ComposeError on anything unclear."""
    figures = []
    for part in (p.strip() for p in spec.split(",")):
        if not part:
            continue
        bits = [b.strip().lower() for b in part.split(":")]
        shape = SHAPE_ALIASES.get(bits[0], bits[0])
        if shape in EFFECTS:
            raise ComposeError(f"{bits[0]!r} is an effect, not a solid figure: describe it in the "
                               f"prompt and leave it out of --compose")
        if shape not in SHAPES:
            raise ComposeError(f"unknown shape {bits[0]!r} (one of: {', '.join(SHAPES)})")
        pos = bits[1] if len(bits) > 1 and bits[1] else "center"
        try:
            x = POSITIONS[pos] if pos in POSITIONS else float(pos)
        except ValueError:
            raise ComposeError(f"unknown position {pos!r}") from None
        if not 0 <= x <= 1:
            raise ComposeError(f"position {x} is outside 0..1")
        size = bits[2] if len(bits) > 2 and bits[2] else "medium"
        if size not in SIZES:
            raise ComposeError(f"unknown size {size!r} (small, medium, large)")
        figures.append((shape, x, SIZES[size]))
    if not figures:
        raise ComposeError("empty composition")
    if len(figures) > 4:
        raise ComposeError("at most 4 figures — more turns the sketch into noise")
    return figures


# ── Raster ───────────────────────────────────────────────────────────────────

class Canvas:
    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        # Far background: a floor that comes closer towards the bottom edge.
        self.px = [[int(8 + 60 * (y / h) ** 2)] * w for y in range(h)]

    def put(self, x: int, y: int, v: float) -> None:
        if 0 <= x < self.w and 0 <= y < self.h and v > self.px[y][x]:
            self.px[y][x] = int(min(255, v))   # nearer surface wins

    def ellipse(self, cx, cy, rx, ry, v, rim=0.0) -> None:
        """Filled ellipse shaded like a rounded body; rim > 0 leaves a ring of that relative width."""
        if rx < 1 or ry < 1:
            return
        for y in range(int(cy - ry), int(cy + ry) + 1):
            for x in range(int(cx - rx), int(cx + rx) + 1):
                d = ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2
                if d <= 1 and (rim == 0 or d >= (1 - rim) ** 2):
                    self.put(x, y, v * (0.82 + 0.18 * math.sqrt(1 - d)))

    def limb(self, x0, y0, x1, y1, r, v) -> None:
        """A capsule from (x0,y0) to (x1,y1) of radius r."""
        steps = max(1, int(math.hypot(x1 - x0, y1 - y0) / max(1.0, r / 2)))
        for i in range(steps + 1):
            t = i / steps
            self.ellipse(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, r, r, v)

    def png(self) -> bytes:
        import map_art
        rows = [bytes(v for p in row for v in (p, p, p)) for row in self.px]
        return map_art.png_bytes(self.w, self.h, rows)


def _draw(c: Canvas, shape: str, fx: float, size: float, facing: int) -> None:
    W, H = c.w, c.h
    ground = H * 0.94
    h = H * size
    cx = W * fx
    v = 150 + 100 * size          # bigger reads nearer, so brighter
    if shape.startswith("humanoid"):
        width = {"humanoid-short": 0.34, "humanoid-tall": 0.2}.get(shape, 0.24) * h
        top = ground - h
        head = h * (0.09 if shape == "humanoid-short" else 0.08)
        c.ellipse(cx, top + head * 1.3, head * 0.85, head, v)                # head
        c.ellipse(cx, top + h * 0.42, width / 2, h * 0.22, v)                # torso
        hip = top + h * 0.6
        c.limb(cx - width * 0.22, hip, cx - width * 0.3, ground, width * 0.14, v)   # legs
        c.limb(cx + width * 0.22, hip, cx + width * 0.3, ground, width * 0.14, v)
        sh = top + h * 0.27
        # One arm raised towards the facing side (a swing), the other forward.
        c.limb(cx + facing * width * 0.45, sh, cx + facing * width * 0.9, top - h * 0.02, width * 0.11, v)
        c.limb(cx - facing * width * 0.45, sh, cx - facing * width * 0.2, top + h * 0.55, width * 0.11, v)
    elif shape in ("quadruped", "beast"):
        bh = h * (0.36 if shape == "quadruped" else 0.48)                    # body height
        bl = h * (0.9 if shape == "quadruped" else 1.0)                      # body length
        by = ground - h * 0.45
        c.ellipse(cx, by, bl / 2, bh / 2, v)                                 # body
        nx, ny = cx + facing * bl * 0.4, by - bh * 0.15                      # shoulder
        hx, hy = cx + facing * bl * 0.58, by - bh * 0.62
        c.limb(nx, ny, hx, hy, bh * 0.22, v)                                 # neck
        c.ellipse(hx, hy, bh * 0.34, bh * 0.3, v)                            # head
        c.ellipse(hx + facing * bh * 0.36, hy + bh * 0.08, bh * 0.22, bh * 0.14, v)  # snout
        for lx in (-0.32, -0.18, 0.2, 0.34):
            c.limb(cx + lx * bl, by, cx + lx * bl + facing * bl * 0.04, ground, bh * 0.13, v)
        c.limb(cx - facing * bl * 0.5, by - bh * 0.1, cx - facing * bl * 0.72, by - bh * 0.5, bh * 0.07, v)
    elif shape == "flyer":
        cy = H * 0.4
        c.ellipse(cx, cy, h * 0.18, h * 0.12, v)
        c.ellipse(cx + facing * h * 0.2, cy - h * 0.08, h * 0.08, h * 0.07, v)
        for side in (-1, 1):
            c.limb(cx, cy, cx + side * h * 0.55, cy - h * 0.25, h * 0.05, v)
            c.limb(cx + side * h * 0.55, cy - h * 0.25, cx + side * h * 0.6, cy + h * 0.05, h * 0.04, v)
    elif shape == "serpent":
        pts = 24
        for i in range(pts):
            t = i / (pts - 1)
            x = cx + (t - 0.5) * h * 1.1
            y = ground - h * 0.2 - math.sin(t * math.pi * 2.2) * h * 0.12 - t * h * 0.35
            c.ellipse(x, y, h * 0.07, h * 0.07, v)
        c.ellipse(cx + h * 0.58 * facing, ground - h * 0.62, h * 0.11, h * 0.09, v)
    else:  # object
        c.ellipse(cx, ground - h * 0.2, h * 0.3, h * 0.2, v)


def depth_png(spec: str, width: int, height: int) -> bytes:
    """Depth sketch for `spec` at width x height (rendered at half size: ControlNet rescales)."""
    figures = parse(spec)
    c = Canvas(max(64, width // 2), max(64, height // 2))
    # Far figures first; each one faces the middle of the picture.
    for shape, x, size in sorted(figures, key=lambda f: f[2]):
        facing = 1 if x < 0.5 else -1
        _draw(c, shape, x, size, facing)
    return c.png()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Write the depth sketch for a composition spec.")
    ap.add_argument("spec")
    ap.add_argument("--out", default="compose.png")
    ap.add_argument("--size", default="1024x704")
    a = ap.parse_args()
    w, h = (int(n) for n in a.size.lower().split("x"))
    with open(a.out, "wb") as f:
        f.write(depth_png(a.spec, w, h))
    print(a.out)
