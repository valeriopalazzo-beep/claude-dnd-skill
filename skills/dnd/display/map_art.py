"""map_art.py — let the image backend paint a battle map's artwork.

map_render.py still owns every position: the grid, the coordinate ruler and
the tokens are drawn by the script on top of the art, so a token is always in
the square the DM wrote. Only the look of the terrain comes from the backend
configured for image_gen.py:

  paint   The layout (walls, water, doors… as flat colours) goes to the backend
          as an init image and gets repainted (img2img), so walls and doors land
          where the grid says. Needs a backend that takes an image: local
          (Forge / A1111) or gemini.
  tiles   One texture per terrain type (floor, wall, water, grass, rough),
          generated from text and tiled square by square. Works with every
          backend, anonymous Pollinations included.
  auto    paint on local / gemini, tiles otherwise (the default).
  off     the plain vector map.

Art is cached in <campaign>/media/ keyed on the TERRAIN only — tokens are
ignored — so re-sending a map after tokens moved costs nothing.

stdlib only.
"""
from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import json
import random
import struct
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import image_gen
import media
from image_gen import ImageError

MODES = ("auto", "paint", "tiles", "off")
PAINT_BACKENDS = ("local", "gemini")
TEXTURED = ("floor", "wall", "water", "grass", "rough")
RETRY_AFTER_S = 15 * 60    # a texture that failed is retried at most this often

# Flat colours of the init image for `paint`. Close to what the finished map
# should look like, so a moderate denoise keeps the layout.
LAYOUT_RGB = {
    "void": (22, 20, 18), "floor": (150, 132, 105), "wall": (46, 41, 37),
    "door": (112, 72, 36), "water": (52, 96, 132), "trap": (150, 132, 105),
    "tree": (42, 86, 36), "rough": (122, 102, 78), "grass": (96, 132, 62),
    "furniture": (106, 72, 42), "pillar": (96, 93, 89), "stairs_up": (126, 116, 100),
    "stairs_down": (82, 74, 64), "fire": (222, 122, 42),
}

# What each present terrain adds to the default description.
_DESC_WORDS = {
    "floor": "stone floor", "wall": "stone walls", "water": "water", "grass": "grass",
    "tree": "trees", "rough": "rubble", "furniture": "furniture", "pillar": "pillars",
    "fire": "braziers", "stairs_up": "stairs", "stairs_down": "stairs", "door": "wooden doors",
}
_TEXTURE_SUBJECT = {
    "floor": "the floor or ground",
    "wall": "the top of a solid wall, masonry or rock",
    "water": "the water surface",
    "grass": "grass and open ground",
    "rough": "rubble, loose rocks and undergrowth",
}
MAP_NEGATIVE = ("text, letters, numbers, grid, watermark, signature, logo, frame, "
                "people, characters, perspective, isometric, blurry, lowres")
_GEMINI_RATIOS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")


@dataclass
class Art:
    mode: str                                     # "paint" | "tiles"
    image: Optional[tuple] = None                 # paint: (mime, bytes)
    textures: dict = field(default_factory=dict)  # tiles: kind → (mime, bytes)
    missing: list = field(default_factory=list)   # tiles: kinds not generated yet


def resolve_mode(requested: Optional[str], backend: str) -> str:
    mode = (requested or "auto").strip().lower()
    if mode not in MODES:
        mode = "auto"
    if backend == "off" or mode == "off":
        return "off"
    if mode == "auto" or (mode == "paint" and backend not in PAINT_BACKENDS):
        return "paint" if backend in PAINT_BACKENDS else "tiles"
    return mode


def describe(kinds: list, art_desc: str) -> str:
    """The DM's `art:` line, or a plain description built from the terrain."""
    if art_desc.strip():
        return art_desc.strip().rstrip(".")
    present = []
    for row in kinds:
        for k in row:
            w = _DESC_WORDS.get(k)
            if w and w not in present:
                present.append(w)
    return ", ".join(present) or "stone floor"


def data_uri(mime: str, data: bytes) -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def _mime(data: bytes) -> str:
    return {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[image_gen._ext_for(data)]


# ── Layout image (stdlib PNG writer) ─────────────────────────────────────────

def png_bytes(width: int, height: int, rows_rgb: list) -> bytes:
    def chunk(tag: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))
    raw = b"".join(b"\x00" + r for r in rows_rgb)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def layout_png(kinds: list, cell: int = 32) -> bytes:
    rows = []
    for krow in kinds:
        line = b"".join(bytes(LAYOUT_RGB.get(k, LAYOUT_RGB["floor"])) * cell for k in krow)
        rows.extend([line] * cell)
    return png_bytes(len(kinds[0]) * cell, len(kinds) * cell, rows)


def paint_size(w_cells: int, h_cells: int, max_side: int) -> "tuple[int, int]":
    f = max_side / max(w_cells, h_cells)
    return (max(128, int(w_cells * f) // 64 * 64), max(128, int(h_cells * f) // 64 * 64))


def gemini_ratio(w_cells: int, h_cells: int) -> str:
    target = w_cells / h_cells
    return min(_GEMINI_RATIOS, key=lambda r: abs(int(r.split(":")[0]) / int(r.split(":")[1]) - target))


# ── Backends ─────────────────────────────────────────────────────────────────

def paint(kinds: list, desc: str, seed: int, cfg: dict) -> bytes:
    style = cfg["style"]
    w_cells, h_cells = len(kinds[0]), len(kinds)
    init = base64.b64encode(layout_png(kinds)).decode("ascii")
    if cfg["backend"] == "local":
        w, h = paint_size(w_cells, h_cells, int(cfg["local_max_side"]))
        prompt = (f"top-down battle map, orthographic view from directly above, "
                  f"tabletop roleplaying game map, {desc}, {style}")
        return image_gen.a1111("img2img", {
            "init_images": ["data:image/png;base64," + init], "resize_mode": 0,
            "denoising_strength": float(cfg["map_denoise"]),
            "prompt": prompt, "negative_prompt": MAP_NEGATIVE,
            "width": w, "height": h, "steps": int(cfg["local_steps"]), "seed": seed,
        }, cfg)
    if cfg["backend"] == "gemini":
        prompt = (f"Repaint this layout as a finished top-down battle map for a tabletop RPG, "
                  f"seen from directly above: {desc}. Keep the layout exactly — dark areas are "
                  f"walls, blue is water, green is grass or trees, brown strips are doors, light "
                  f"areas are floor. No grid lines, no text, no letters, no characters or tokens. "
                  f"Style: {style}.")
        return image_gen.gemini([{"inlineData": {"mimeType": "image/png", "data": init}},
                                 {"text": prompt}], gemini_ratio(w_cells, h_cells), cfg)
    raise ImageError(f"backend {cfg['backend']!r} cannot repaint a layout — use map_art \"tiles\"")


def texture(kind: str, desc: str, seed: int, cfg: dict) -> bytes:
    prompt = (f"flat seamless texture, orthographic view looking straight down at "
              f"{_TEXTURE_SUBJECT[kind]}, setting: {desc}, uniform pattern filling the whole "
              f"frame, no walls, no corners, no perspective, no horizon, even lighting, "
              f"no objects, no people, {cfg['style']}")
    return image_gen.generate(prompt, "texture", seed, cfg)


# ── Cache ────────────────────────────────────────────────────────────────────

def _key(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()


def _cached(d: Path, stem: str) -> Optional[Path]:
    hits = sorted(d.glob(stem + ".*"))
    return hits[0] if hits else None


def _store(d: Path, stem: str, data: bytes, entry: dict) -> Path:
    path = d / f"{stem}.{image_gen._ext_for(data)}"
    path.write_bytes(data)
    media.add_to_index(d, {"file": path.name, **entry,
                           "created": _dt.datetime.now().isoformat(timespec="seconds")})
    return path


def _read(path: Path) -> tuple:
    data = path.read_bytes()
    return (_mime(data), data)


def get_art(kinds: list, art_desc: str, d: Path, cfg: dict, mode: str,
            generate: bool = True, log: Callable[[str], None] = lambda _m: None) -> Optional[Art]:
    """Cached art for this terrain, generating what is missing when `generate`.

    With generate=False: None when nothing is cached yet; for tiles, whatever
    textures exist, with the rest listed in `missing`. With tiles, a texture
    that fails is left out (that terrain stays vector) and stays in `missing`;
    raises ImageError only when nothing at all could be made.
    """
    desc = describe(kinds, art_desc)
    style, backend = cfg["style"], cfg["backend"]

    if mode == "paint":
        stem = "mapart-" + _key("paint", kinds, desc, style, backend, cfg.get("map_denoise"))[:12]
        hit = _cached(d, stem)
        if hit:
            return Art("paint", image=_read(hit))
        if not generate:
            return None
        seed = random.randint(1, 2**31 - 1)
        data = paint(kinds, desc, seed, cfg)
        path = _store(d, stem, data, {"kind": "map-art", "subject": desc, "prompt": desc,
                                      "seed": seed, "backend": backend})
        return Art("paint", image=_read(path))

    needed = [k for k in TEXTURED if any(k in row for row in kinds)]
    art, missing = Art("tiles"), []
    for k in needed:
        stem = f"maptex-{k}-" + _key("tiles", k, desc, style, backend)[:10]
        hit = _cached(d, stem)
        if hit:
            art.textures[k] = _read(hit)
        else:
            missing.append((k, stem))
    art.missing = [k for k, _ in missing]
    if not generate:
        return art if art.textures or not needed else None
    errors = []
    for k, stem in missing:
        failed = d / f"{stem}-failed"      # no extension: never served, never a cache hit
        try:
            if time.time() - failed.stat().st_mtime < RETRY_AFTER_S:
                continue   # failed recently — don't stall every re-send on it
        except OSError:
            pass
        seed = random.randint(1, 2**31 - 1)
        try:
            data = texture(k, desc, seed, cfg)
            path = _store(d, stem, data, {"kind": "map-texture", "subject": f"{k}: {desc}",
                                          "prompt": desc, "seed": seed, "backend": backend})
            art.textures[k] = _read(path)
            art.missing.remove(k)
        except ImageError as e:
            errors.append(f"{k}: {e}")
            log(f"texture {k} failed — {e}")
            failed.touch()
    if not art.textures and needed:
        raise ImageError("; ".join(errors) or "textures failed recently — retrying later")
    return art
