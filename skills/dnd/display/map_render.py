#!/usr/bin/env python3
"""map_render.py — turn an ASCII battle map into an SVG and show it on the display.

The DM (Claude) writes the layout as text; this script owns every position, so
grid, coordinates and tokens are exact and all text is escaped. The terrain's
LOOK comes from the image backend configured for image_gen.py (see map_art.py):
painted over the layout, or tiled from generated textures. The art is cached
per terrain, so re-sending a map with moved tokens is instant. Without a
backend, or if it fails, the map is drawn as plain vector shapes.

Input (stdin or --file). Header and legend are optional; `---` separates them:

    title: Cripta di Vessar
    scale: 1 quadretto = 1,5 m
    art: damp crypt, cracked flagstones, scattered bones, candlelight
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

Effects (in the legend section): what just happened on the field, drawn as a
tinted area over the map without touching the terrain, so painted art stays
cached and the re-send is instant. Coordinates are the ruler's (column,row):
    @ 9,8 r1 fire | Esplosione di cenere     9,8 plus 1 square around (3x3)
    @ 4,2-6,3 magic | Portale                a rectangle, corner to corner
    @ 12,5 light                             one square, no label
Kinds: fire, magic, cold, poison, acid, lightning, dark, light, blood, smoke.

Areas of effect (spell templates, PHB 2014 p. 204), measured in feet, metres
or squares (`20ft`, `6m`, `4q`; the scale line sets how big a square is,
5 ft = 1,5 m by default). The squares whose centre falls inside are tinted,
the exact shape is outlined, and the tokens caught in it are listed on the
map and printed for the DM:
    @ 9,8 sphere 20ft fire | Palla di fuoco      centred on square 9,8 (cylinder too)
    @ 3,4 cone 15ft E fire | Mani brucianti      from the side of 3,4 that faces east
    @ 3,4 line 30ft x5ft NE lightning | Fulmine  length, then width (default 5 ft)
    @ 6,6 cube 10ft SE magic | Onda tonante      from the side (or corner) of 6,6
Directions: N NE E SE S SW W NW (Italian O for W, NO and SO too). Italian
shape names work: sfera, cilindro, cono, linea, cubo.

Fog of war (header lines): the map shows only what the party can see.
    fog: on            what pc and ally tokens see from where they stand, stopped by
                       walls (#) and doors (+); what they saw on earlier sends stays
                       on the map, dimmed; the rest is dark
    sight: 6           how many squares they see (default 12)
    fog: reset         forget what was seen before (a new level under the same title)
In the legend section, `? 4,2-9,6` (or `? 9,8 r2`) lights an area whatever the
tokens see: a room lit by a spell, a hall seen from a balcony. A foe, npc or
neutral token standing where nobody is looking is not drawn at all — write
the whole map, the script decides what shows.

Light and vision (PHB 2014 pp. 183-185): a header line sets the light of the
place, and the map shades what is dim or dark; with fog on, the party sees
only what its light and its eyes reach.
    light: dark        dark | dim | bright (Italian: buio | fioca | intensa)
In the legend section, a light source stands on a square or is carried by a
token (it moves with it):
    light 4,5 torch                  luce 4,5 torcia
    light A lantern                  Aldric carries a hooded lantern
    light B bullseye E               a bullseye lantern's cone, pointing east
    light 9,2 20ft/20ft              bright 20 ft, then dim 20 ft more (one size: both)
Kinds: candle, torch, lamp, lantern (hooded), hooded-low, bullseye, light,
daylight, continual-flame, dancing-lights, produce-flame, flame-blade, fire
(Italian names too). A `!` square (fire, brazier) lights its surroundings as
`fire` does. Walls and closed doors stop light as they stop the eye.
A token's senses go after its side; PCs without them are read from their
sheets (Darkvision / Scurovisione and its range):
    A: Aldric | pc | darkvision 18m
    g: Goblin | foe | darkvision 60ft
    k: Melma | foe | blindsight 3m       also truesight, devil's sight
Darkvision sees dim light as bright and darkness as dim, in grey; a `dark`
effect (`@ … dark`, e.g. the Darkness spell) is magical darkness: only
truesight, devil's sight and blindsight get through. The script prints, for
the DM, who sees whom and how well — dim light and darkvision mean lightly
obscured (disadvantage on Perception by sight); not seen means attacks
against it have advantage and its own attacks disadvantage.

Usage:
    python3 map_render.py << 'DNDEND'  ...map...  DNDEND      # render + show
    python3 map_render.py --file map.txt --no-send             # render only
    python3 map_render.py --art off < map.txt                  # plain vector map
    python3 map_render.py --symbols                            # print the key
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
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

HEADER_KEYS = ("title", "scale", "titolo", "scala", "art", "fog", "nebbia", "sight", "vista", "light", "luce")
# effect kind → (fill, glyph)
EFFECTS = {
    "fire": ("#ff6a1a", "✹"), "magic": ("#9b5de5", "✦"), "cold": ("#5fc4ef", "❄"),
    "poison": ("#5fae3a", "☠"), "acid": ("#b5d334", "☠"), "lightning": ("#ffd93d", "ϟ"),
    "dark": ("#1b1726", "●"), "light": ("#fff1a8", "☀"), "blood": ("#a4161a", "✖"),
    "smoke": ("#8d8d8d", "≋"),
}
EFFECT_ALIASES = {"portal": "magic", "arcane": "magic", "explosion": "fire", "flame": "fire",
                  "ice": "cold", "frost": "cold", "shadow": "dark", "darkness": "dark",
                  "holy": "light", "radiant": "light", "thunder": "lightning", "fog": "smoke",
                  "gas": "poison", "fuoco": "fire", "fiamme": "fire", "magia": "magic", "arcano": "magic",
                  "freddo": "cold", "gelo": "cold", "ghiaccio": "cold", "veleno": "poison", "acido": "acid",
                  "fulmine": "lightning", "tuono": "lightning", "buio": "dark", "ombra": "dark", "luce": "light",
                  "radioso": "light", "sangue": "blood", "fumo": "smoke", "nebbia": "smoke"}
_EFFECT_RE = re.compile(r"^@\s*(\d+)\s*,\s*(\d+)(?:\s*-\s*(\d+)\s*,\s*(\d+))?"
                        r"(?:\s+r(\d+))?(?:\s+([A-Za-z]+))?\s*(?:\|\s*(.*))?$")
SHAPES = {"sphere": "sphere", "sfera": "sphere", "radius": "sphere", "raggio": "sphere",
          "cylinder": "sphere", "cilindro": "sphere", "cone": "cone", "cono": "cone",
          "line": "line", "linea": "line", "cube": "cube", "cubo": "cube"}
DIRECTIONS = {"N": (0, -1), "NE": (1, -1), "E": (1, 0), "SE": (1, 1), "S": (0, 1), "SW": (-1, 1),
              "W": (-1, 0), "NW": (-1, -1), "O": (-1, 0), "NO": (-1, -1), "SO": (-1, 1)}
_SIZE_RE = re.compile(r"^x?(\d+(?:[.,]\d+)?)\s*(ft|feet|piedi|m|metri|q|sq|squares|quadretti)?$", re.I)
TEXTURED = ("floor", "wall", "water", "grass", "rough")   # map_art.TEXTURED
PAINT_MARKERS = ("door", "trap", "stairs_up", "stairs_down")  # kept visible over painted art
TEX_TILE = 4 * CELL   # one texture spans 4x4 squares; its seams fall on grid lines

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
    art: str = ""        # English visual description for the generated terrain
    rows: list = field(default_factory=list)
    legend: dict = field(default_factory=dict)   # letter → (name, side)
    effects: list = field(default_factory=list)  # Effect, drawn over terrain, under tokens
    warnings: list = field(default_factory=list)
    fog: bool = False        # fog of war: show only what pc/ally tokens see or have seen
    fog_reset: bool = False  # forget what was seen on earlier sends
    sight: int = 0           # squares; 0 = SIGHT_DEFAULT
    reveal: list = field(default_factory=list)   # (x0, y0, x1, y1) lit whatever the tokens see
    light: str = ""          # the place's light: "", bright, dim or dark ("" = no light model)
    lights: list = field(default_factory=list)   # LightSource
    vision: dict = field(default_factory=dict)   # letter → Vision written in the legend


@dataclass
class Vision:
    """How far (in squares) a creature sees without light, or past it."""
    dark: float = 0.0     # darkvision
    blind: float = 0.0    # blindsight
    true: float = 0.0     # truesight
    devil: float = 0.0    # devil's sight: darkness, magical too, as if lit

    def __bool__(self):
        return bool(self.dark or self.blind or self.true or self.devil)


@dataclass
class LightSource:
    x: int                # where it shines from (a carried light: its token's square)
    y: int
    bright: float         # squares of bright light
    dim: float            # squares of dim light beyond the bright
    kind: str = ""
    token: str = ""       # the letter of the token carrying it
    direction: tuple = None   # a cone (bullseye lantern) pointing this way


@dataclass
class Effect:
    x0: int   # 0-based, inclusive
    y0: int
    x1: int
    y1: int
    kind: str
    label: str = ""
    cells: frozenset = None   # a spell template: exactly these squares (else the whole rectangle)
    outline: tuple = None     # ("circle", cx, cy, r) or ("poly", [(x, y), …]) in squares
    shape: str = ""           # sphere, cone, line or cube, and its size, for the DM's line


def squares_per_foot(spec) -> float:
    """1 / feet per square, read from the scale line (5 ft = 1,5 m when it says nothing)."""
    m = re.search(r"=\s*(\d+(?:[.,]\d+)?)\s*(ft|feet|piedi|m|metri)", spec.scale or "", re.I)
    if not m:
        return 1 / 5
    v = float(m.group(1).replace(",", "."))
    feet = v * 5 / 1.5 if m.group(2).lower().startswith("m") else v
    return 1 / feet if feet > 0 else 1 / 5


def _size(token: str, per_foot: float):
    """'20ft' / '6m' / '4q' / '20' (feet) → squares, or None."""
    m = _SIZE_RE.match(token.strip())
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    unit = (m.group(2) or "ft").lower()
    if unit in ("q", "sq", "squares", "quadretti"):
        return v
    feet = v * 5 / 1.5 if unit in ("m", "metri") else v
    return feet * per_foot


def template_cells(shape: str, x: int, y: int, size: float, width: float = 1.0, direction=(1, 0)):
    """(squares inside, outline) of a template from square x,y — sizes in squares.

    A square is inside when its centre is (the DMG's "half a square" rule of thumb)."""
    import math
    eps = 1e-9
    dx, dy = direction
    if shape == "sphere":
        ox, oy = x + 0.5, y + 0.5
        cells = {(i, j) for j in range(int(y - size - 1), int(y + size + 2)) for i in range(int(x - size - 1), int(x + size + 2))
                 if math.hypot(i + 0.5 - ox, j + 0.5 - oy) <= size + eps}
        return frozenset(cells), ("circle", ox, oy, size)
    # Cones, lines and cubes start from the side (or corner) of the square that faces the direction.
    ox, oy = x + 0.5 + dx * 0.5, y + 0.5 + dy * 0.5
    if shape == "cube" and dx and dy:
        x0, x1 = sorted((ox, ox + dx * size))
        y0, y1 = sorted((oy, oy + dy * size))
        cells = {(i, j) for j in range(int(y0) - 1, int(y1) + 2) for i in range(int(x0) - 1, int(x1) + 2)
                 if x0 - eps <= i + 0.5 <= x1 + eps and y0 - eps <= j + 0.5 <= y1 + eps}
        return frozenset(cells), ("poly", [(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
    n = math.hypot(dx, dy)
    ux, uy = dx / n, dy / n
    px, py = -uy, ux
    half = {"cone": None, "line": width / 2, "cube": size / 2}[shape]
    reach = int(size + width + 2)
    cells = set()
    for j in range(int(oy) - reach, int(oy) + reach + 1):
        for i in range(int(ox) - reach, int(ox) + reach + 1):
            vx, vy = i + 0.5 - ox, j + 0.5 - oy
            t, q = vx * ux + vy * uy, vx * px + vy * py
            lim = t / 2 if half is None else half
            if eps < t <= size + eps and abs(q) <= lim + eps:
                cells.add((i, j))
    far = (ox + ux * size, oy + uy * size)
    w2 = size / 2 if half is None else half
    if half is None:
        poly = [(ox, oy), (far[0] + px * w2, far[1] + py * w2), (far[0] - px * w2, far[1] - py * w2)]
    else:
        poly = [(ox + px * w2, oy + py * w2), (far[0] + px * w2, far[1] + py * w2),
                (far[0] - px * w2, far[1] - py * w2), (ox - px * w2, oy - py * w2)]
    return frozenset(cells), ("poly", poly)


def _parse_template(line: str, w: int, h: int, warnings: list, per_foot: float):
    """An `@ x,y <shape> <size> …` line as an Effect; None when the line is not a template."""
    body, _, label = line.strip()[1:].partition("|")
    words = body.split()
    if len(words) < 3 or words[1].lower() not in SHAPES:
        return None
    m = re.fullmatch(r"(\d+)\s*,\s*(\d+)", words[0])
    if not m:
        return None
    shape = SHAPES[words[1].lower()]
    x, y = int(m.group(1)) - 1, int(m.group(2)) - 1
    size, width, direction, kind = None, None, None, None
    for word in words[2:]:
        if word.upper() in DIRECTIONS and direction is None:
            direction = DIRECTIONS[word.upper()]
        elif _size(word, per_foot) is not None and (size is None or width is None):
            if size is None and not word.lower().startswith("x"):
                size = _size(word, per_foot)
            else:
                width = _size(word, per_foot)
        elif kind is None and word.isalpha():
            kind = EFFECT_ALIASES.get(word.lower(), word.lower())
        else:
            warnings.append(f"template: {word!r} not understood in {line.strip()!r}")
    if not size or size <= 0:
        warnings.append(f"template without a size: {line.strip()!r} (write e.g. 20ft or 6m)")
        return False
    if shape in ("cone", "line", "cube") and direction is None:
        if shape != "cube":
            warnings.append(f"{shape} without a direction — pointing east: {line.strip()!r}")
        direction = DIRECTIONS["E" if shape != "cube" else "SE"]
    kind = kind or "fire"
    if kind not in EFFECTS:
        warnings.append(f"unknown effect kind {kind!r} — drawn as magic")
        kind = "magic"
    cells, outline = template_cells(shape, x, y, size, width or per_foot * 5, direction or (1, 0))
    cells = frozenset((i, j) for i, j in cells if 0 <= i < w and 0 <= j < h)
    if not cells:
        warnings.append(f"template outside the map: {line.strip()!r}")
        return False
    xs, ys = [c[0] for c in cells], [c[1] for c in cells]
    words_size = words[2] if _size(words[2], per_foot) is not None else ""
    return Effect(min(xs), min(ys), max(xs), max(ys), kind, label.strip(), cells, outline,
                  f"{shape} {words_size}".strip())


def tokens_in(spec, e) -> list:
    """(letter, name, side) of every token standing in the effect's squares, in reading order."""
    out = []
    for y, row in enumerate(spec.rows):
        for x, ch in enumerate(row):
            inside = (x, y) in e.cells if e.cells is not None else (e.x0 <= x <= e.x1 and e.y0 <= y <= e.y1)
            if ch.isalpha() and inside:
                name, side = spec.legend.get(ch, (ch, "pc" if ch.isupper() else "foe"))
                out.append((ch, name, side))
    return out


def _parse_effect(line: str, w: int, h: int, warnings: list):
    m = _EFFECT_RE.match(line.strip())
    if not m:
        warnings.append(f"effect line not understood: {line.strip()!r}")
        return None
    c0, r0, c1, r1, rad, kind, label = m.groups()
    kind = (kind or "fire").lower()
    kind = EFFECT_ALIASES.get(kind, kind)
    if kind not in EFFECTS:
        warnings.append(f"unknown effect kind {kind!r} — drawn as magic")
        kind = "magic"
    x0, y0 = int(c0) - 1, int(r0) - 1
    x1, y1 = (int(c1) - 1, int(r1) - 1) if c1 else (x0, y0)
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    r = int(rad or 0)
    x0, y0, x1, y1 = x0 - r, y0 - r, x1 + r, y1 + r
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w - 1, x1), min(h - 1, y1)
    if x0 > x1 or y0 > y1:
        warnings.append(f"effect outside the map: {line.strip()!r}")
        return None
    return Effect(x0, y0, x1, y1, kind, (label or "").strip())


# Light sources: kind → (bright ft, dim ft beyond the bright, cone?). PHB 2014
# p. 152-153 (candle, lamp, lanterns, torch) and the spells' own text (light,
# daylight, continual flame = a torch, dancing lights, produce flame, flame
# blade). `fire` — a brazier or a campfire — is the script's choice: the PHB
# gives no number for it, so it shines like produce flame.
LIGHT_KINDS = {
    "candle": (5, 5, False), "torch": (20, 20, False), "lamp": (15, 30, False), "lantern": (30, 30, False),
    "hooded-low": (0, 5, False), "bullseye": (60, 60, True), "light": (20, 20, False),
    "daylight": (60, 60, False), "continual-flame": (20, 20, False), "dancing-lights": (0, 10, False),
    "produce-flame": (10, 10, False), "flame-blade": (10, 10, False), "fire": (10, 10, False),
}
LIGHT_WORDS = {
    "candela": "candle", "candele": "candle", "torcia": "torch", "torce": "torch", "lampada": "lamp",
    "lanterna": "lantern", "hooded": "lantern", "hooded-lantern": "lantern", "lanterna-schermabile": "lantern",
    "schermabile": "lantern", "lantern-low": "hooded-low", "lanterna-abbassata": "hooded-low",
    "lanterna-schermabile-abbassata": "hooded-low", "bullseye-lantern": "bullseye",
    "lanterna-a-lente-sporgente": "bullseye", "lente-sporgente": "bullseye", "luce": "light",
    "light-spell": "light", "luce-diurna": "daylight", "fiamma-perenne": "continual-flame",
    "luci-danzanti": "dancing-lights", "produrre-fiamma": "produce-flame", "lama-infuocata": "flame-blade",
    "fuoco": "fire", "braciere": "fire", "brazier": "fire", "campfire": "fire", "falò": "fire", "fiamma": "fire",
}
LIGHT_LEVELS = {"bright": 2, "dim": 1, "dark": 0, "intensa": 2, "luminoso": 2, "giorno": 2, "day": 2,
                "fioca": 1, "penombra": 1, "crepuscolo": 1, "buio": 0, "oscurità": 0, "oscurita": 0, "notte": 0,
                "night": 0, "darkness": 0}
LIGHT_NAMES = {2: "bright", 1: "dim", 0: "dark"}
SENSES = {"darkvision": "dark", "superior-darkvision": "dark", "scurovisione": "dark",
          "scurovisione-superiore": "dark", "blindsight": "blind", "vista-cieca": "blind",
          "percezione-cieca": "blind", "truesight": "true", "vista-pura": "true", "visione-del-vero": "true",
          "devil's-sight": "devil", "devils-sight": "devil", "devil-sight": "devil", "vista-del-diavolo": "devil"}
SENSE_DEFAULT_FT = {"dark": 60, "blind": 10, "true": 120, "devil": 120}
_LIGHT_RE = re.compile(r"^(?:light|luce)\s+(\S+)\s*(.*)$", re.I)


def _light_size(word: str, per_foot: float):
    """'20ft/20ft' → (bright, dim) squares; '20ft' → the same for both; None when not a size."""
    parts = word.split("/")
    if len(parts) > 2:
        return None
    sizes = [_size(p, per_foot) if p not in ("0", "") else 0.0 for p in parts]
    if any(s is None for s in sizes):
        return None
    return (sizes[0], sizes[-1])


def _parse_light(line: str, w: int, h: int, warnings: list, per_foot: float):
    """A `light <x,y | letter> [kind] [bright/dim] [direction]` line as a LightSource (position unresolved for a token)."""
    m = _LIGHT_RE.match(line.strip())
    if not m:
        return None
    where, rest = m.group(1), m.group(2).split()
    token, x, y = "", -1, -1
    pos = re.fullmatch(r"(\d+)\s*,\s*(\d+)", where)
    if pos:
        x, y = int(pos.group(1)) - 1, int(pos.group(2)) - 1
        if not (0 <= x < w and 0 <= y < h):
            warnings.append(f"light outside the map: {line.strip()!r}")
            return False
    elif len(where) == 1 and where.isalpha():
        token = where
    else:
        warnings.append(f"light: {where!r} is neither col,row nor a token letter in {line.strip()!r}")
        return False
    kind, size, direction, words = "", None, None, []
    for word in rest:
        if word.upper() in DIRECTIONS and direction is None:
            direction = DIRECTIONS[word.upper()]
        elif size is None and _light_size(word, per_foot) is not None:
            size = _light_size(word, per_foot)
        else:
            words.append(word.lower())
    if words:
        name = "-".join(words)
        kind = LIGHT_WORDS.get(name, name)
        if kind not in LIGHT_KINDS:
            warnings.append(f"light: unknown kind {name!r} — a torch: {line.strip()!r}")
            kind = "torch"
    elif size is None:
        kind = "torch"
    if size is None:
        bright, dim, cone = LIGHT_KINDS[kind]
        size = (bright * per_foot, dim * per_foot)
        if cone and direction is None:
            warnings.append(f"{kind} without a direction — pointing east: {line.strip()!r}")
            direction = DIRECTIONS["E"]
        if not cone:
            direction = None
    return LightSource(x, y, size[0], size[1], kind or "custom", token, direction)


def parse_senses(text: str, per_foot: float, warnings: list = None) -> Vision:
    """'darkvision 18m, blindsight 3m' → Vision (in squares). Unknown words are reported, not guessed."""
    vis = Vision()
    for part in re.split(r"[,;]", text or ""):
        words = part.strip().lower().split()
        if not words:
            continue
        size = _size(words[-1], per_foot) if len(words) > 1 else None
        name = "-".join(words[:-1] if size is not None else words)
        sense = SENSES.get(name)
        if sense is None:
            if warnings is not None:
                warnings.append(f"sense {part.strip()!r} not understood (darkvision, blindsight, truesight, devil's sight)")
            continue
        setattr(vis, sense, size if size is not None else SENSE_DEFAULT_FT[sense] * per_foot)
    return vis


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
                                  in HEADER_KEYS for l in body)

    if len(sections) >= 2 and is_header(sections[0]):
        for l in sections[0]:
            if ":" in l:
                k, v = l.split(":", 1)
                k = k.strip().lower()
                if k in ("title", "titolo"):
                    spec.title = v.strip()
                elif k in ("scale", "scala"):
                    spec.scale = v.strip()
                elif k == "art":
                    spec.art = v.strip()
                elif k in ("fog", "nebbia"):
                    word = v.strip().lower()
                    spec.fog = word not in ("", "off", "no", "false", "0")
                    spec.fog_reset = word in ("reset", "new", "nuova")
                elif k in ("sight", "vista"):
                    num = re.match(r"\s*(\d+)", v)
                    if num and int(num.group(1)) > 0:
                        spec.sight = min(int(num.group(1)), MAX_W)
                    else:
                        spec.warnings.append(f"sight is a number of squares, not {v.strip()!r} — using {SIGHT_DEFAULT}")
                elif k in ("light", "luce"):
                    word = v.strip().lower()
                    if word in LIGHT_LEVELS:
                        spec.light = LIGHT_NAMES[LIGHT_LEVELS[word]]
                    elif word not in ("", "off", "no"):
                        spec.warnings.append(f"light is dark, dim or bright, not {v.strip()!r} — no light model")
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
            if l.strip().startswith("@"):
                eff = _parse_template(l, width, len(spec.rows), spec.warnings, squares_per_foot(spec))
                if eff is None:
                    eff = _parse_effect(l, width, len(spec.rows), spec.warnings)
                if eff:
                    spec.effects.append(eff)
                continue
            if l.strip().startswith("?"):
                # A lit area: same coordinates as an effect, no kind.
                area = _parse_effect("@" + l.strip()[1:], width, len(spec.rows), spec.warnings)
                if area:
                    spec.reveal.append((area.x0, area.y0, area.x1, area.y1))
                continue
            if _LIGHT_RE.match(l.strip()) and ":" not in l.split()[0]:
                src = _parse_light(l, width, len(spec.rows), spec.warnings, squares_per_foot(spec))
                if src:
                    spec.lights.append(src)
                continue
            if ":" not in l:
                continue
            k, v = l.split(":", 1)
            k = k.strip()
            if len(k) != 1 or not k.isalpha():
                continue
            parts = [p.strip() for p in v.split("|")]
            name, rest = parts[0], parts[1:]
            side = rest[0].lower() if rest else ""
            side = SIDE_ALIASES.get(side, side)
            if side in SIDES:
                rest = rest[1:]
            else:
                side = "pc" if k.isupper() else "foe"
            spec.legend[k] = (name or k, side)
            if rest:
                vis = parse_senses(", ".join(rest), squares_per_foot(spec), spec.warnings)
                if vis:
                    spec.vision[k] = vis

    carried = []
    for src in spec.lights:
        if src.token:
            at = token_position(spec, src.token)
            if at is None:
                spec.warnings.append(f"light carried by {src.token!r}, but no such token is on the map — left out")
                continue
            src.x, src.y = at
        carried.append(src)
    spec.lights = carried
    if spec.lights and not spec.light:
        spec.warnings.append("light sources but no `light:` line in the header — they change nothing until it says dark or dim")

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


def _draw_cell(kind: str, spec: MapSpec, x: int, y: int, art=None) -> list:
    px, py, s = PAD + x * CELL, PAD + y * CELL, CELL
    cx, cy = px + s / 2, py + s / 2
    out = []
    if kind == "void":
        return out      # off-map: leave the parchment (or the painted art) showing
    tex = art.textures if art is not None else {}
    surface = kind if kind in TEXTURED else "floor"
    if art is not None and art.mode == "paint":
        if kind not in PAINT_MARKERS:
            return out  # the painting already shows it
    elif surface in tex:
        out.append(f'<rect x="{px}" y="{py}" width="{s}" height="{s}" fill="url(#tex-{surface})"/>')
        if kind == "wall":  # walls must read as solid at a glance
            out.append(f'<rect x="{px}" y="{py}" width="{s}" height="{s}" '
                       f'fill="{C["wall_edge"]}" opacity="0.45"/>')
        if kind in TEXTURED:
            return out  # the texture is the detail
    else:
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


# ── Fog of war ───────────────────────────────────────────────────────────────
# With `fog: on` the map shows what the party's tokens (pc, ally) can see from
# where they stand — up to `sight` squares, stopped by walls and doors — plus
# what they saw before, dimmed. The rest is covered, and a creature standing
# where nobody is looking is left out of the picture altogether.

SIGHT_DEFAULT = 12
FOG_BLOCKS = ("wall", "door", "void")
FOG_DARK, FOG_DIM = "#14100b", 0.5


def visible_cells(spec: MapSpec) -> set:
    """Squares some pc or ally token sees right now, plus the `?` areas the DM lit.

    With a `light:` line, what they see depends on the light and their eyes too
    (see party_view)."""
    if spec.light:
        return set(party_view(spec, light_levels(spec)))
    w, h = len(spec.rows[0]), len(spec.rows)
    blocks = [[_cell_kind(spec, x, y) in FOG_BLOCKS for x in range(w)] for y in range(h)]
    radius = spec.sight if spec.sight > 0 else SIGHT_DEFAULT
    seen = set()
    for vy, row in enumerate(spec.rows):
        for vx, ch in enumerate(row):
            if not ch.isalpha() or spec.legend.get(ch, (ch, "pc" if ch.isupper() else "foe"))[1] not in ("pc", "ally"):
                continue
            seen.add((vx, vy))
            for ty in range(max(0, vy - radius), min(h, vy + radius + 1)):
                for tx in range(max(0, vx - radius), min(w, vx + radius + 1)):
                    if (tx, ty) in seen or (tx - vx) ** 2 + (ty - vy) ** 2 > (radius + 0.5) ** 2:
                        continue
                    # Walk the straight line from the viewer; a wall on the way hides what is behind it.
                    steps = max(abs(tx - vx), abs(ty - vy))
                    clear = True
                    for i in range(1, steps):
                        x = int(vx + (tx - vx) * i / steps + 0.5)
                        y = int(vy + (ty - vy) * i / steps + 0.5)
                        if blocks[y][x]:
                            clear = False
                            break
                    if clear:
                        seen.add((tx, ty))
    # The walls and doors that close a seen floor square are seen too.
    for (x, y) in [c for c in seen if not blocks[c[1]][c[0]]]:
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h and blocks[ny][nx] and _cell_kind(spec, nx, ny) != "void":
                    seen.add((nx, ny))
    for (x0, y0, x1, y1) in spec.reveal:
        seen.update((x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1))
    return seen


# ── Light and vision ─────────────────────────────────────────────────────────
# PHB 2014 pp. 183-185. Every square is bright, dim or dark: the place's light
# (`light:`), raised by the light sources that reach it in a straight line.
# Dim light and darkness seen through darkvision are lightly obscured
# (disadvantage on Perception by sight); darkness is heavily obscured, so what
# stands in it is not seen at all. Magical darkness (a `dark` effect) lets
# only truesight, devil's sight and blindsight through.

EPS = 1e-6
CLEAR, DIM_SEEN, GREY_SEEN, SENSED = 3, 2, 1, 0      # how well a square is seen
QUALITY_NAMES = {CLEAR: "", DIM_SEEN: "dim", GREY_SEEN: "darkvision, grey", SENSED: "blindsight only"}
WHERE_NAMES = {2: "in bright light", 1: "in dim light", 0: "in darkness", -1: "in magical darkness"}
LIGHT_SHADE = {1: ("#0b0d1a", 0.36), 0: ("#06070d", 0.72), -1: ("#000000", 0.88)}     # no fog: the place's light
VIEW_SHADE = {DIM_SEEN: ("#0b0d1a", 0.34), GREY_SEEN: ("#2b3646", 0.55), SENSED: ("#06070d", 0.72)}  # fog: as seen
LIGHT_KEY = {"it": ("luce intensa", "luce fioca", "buio", "scurovisione"),
             "en": ("bright light", "dim light", "darkness", "darkvision")}


def token_position(spec: MapSpec, letter: str):
    """(x, y) of the first square holding that token, or None."""
    for y, row in enumerate(spec.rows):
        x = row.find(letter)
        if x >= 0:
            return (x, y)
    return None


def _blocks(spec: MapSpec) -> list:
    return [[_cell_kind(spec, x, y) in FOG_BLOCKS for x in range(len(spec.rows[0]))] for y in range(len(spec.rows))]


def _clear(blocks: list, vx: int, vy: int, tx: int, ty: int) -> bool:
    """Nothing that blocks stands on the straight line between two squares (the ends excluded)."""
    steps = max(abs(tx - vx), abs(ty - vy))
    for i in range(1, steps):
        if blocks[int(vy + (ty - vy) * i / steps + 0.5)][int(vx + (tx - vx) * i / steps + 0.5)]:
            return False
    return True


def all_lights(spec: MapSpec) -> list:
    """The legend's light sources, plus every `!` square (a fire lights what is around it)."""
    out = list(spec.lights)
    per_foot = squares_per_foot(spec)
    bright, dim, _cone = LIGHT_KINDS["fire"]
    for y, row in enumerate(spec.rows):
        for x, ch in enumerate(row):
            if ch == "!":
                out.append(LightSource(x, y, bright * per_foot, dim * per_foot, "fire"))
    return out


def light_levels(spec: MapSpec) -> dict:
    """(x, y) → 2 bright, 1 dim, 0 dark, -1 magical darkness; {} without a `light:` line."""
    import math
    if not spec.light:
        return {}
    w, h = len(spec.rows[0]), len(spec.rows)
    blocks = _blocks(spec)
    ambient = LIGHT_LEVELS[spec.light]
    level = {(x, y): ambient for y in range(h) for x in range(w)}
    if ambient < 2:
        for src in all_lights(spec):
            reach = src.bright + src.dim
            if src.direction:
                cone, _ = template_cells("cone", src.x, src.y, reach, 1.0, src.direction)
                cand = set(cone) | {(src.x, src.y)}
            else:
                r = int(reach) + 1
                cand = {(i, j) for j in range(src.y - r, src.y + r + 1) for i in range(src.x - r, src.x + r + 1)}
            for (i, j) in cand:
                if not (0 <= i < w and 0 <= j < h) or blocks[j][i]:
                    continue
                d = math.hypot(i - src.x, j - src.y)
                if d > reach + EPS or not _clear(blocks, src.x, src.y, i, j):
                    continue
                lv = 2 if d <= src.bright + EPS else 1
                if lv > level[(i, j)]:
                    level[(i, j)] = lv
        # A wall or a door is as lit as the brightest open square next to it.
        for y in range(h):
            for x in range(w):
                if blocks[y][x]:
                    near = [level[(x + dx, y + dy)] for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                            if 0 <= x + dx < w and 0 <= y + dy < h and not blocks[y + dy][x + dx]]
                    level[(x, y)] = max([ambient] + near)
    for e in spec.effects:
        if e.kind == "dark":
            cells = e.cells if e.cells is not None else {(x, y) for y in range(e.y0, e.y1 + 1) for x in range(e.x0, e.x1 + 1)}
            for c in cells:
                if c in level:
                    level[c] = -1
    return level


def sees(vis, d: float, lv: int):
    """How well eyes `vis` see a square `d` squares away lit at `lv`: CLEAR, DIM_SEEN, GREY_SEEN, SENSED or None."""
    vis = vis or Vision()

    def within(r):
        return r > 0 and d <= r + EPS
    if lv == -1:
        if within(vis.true) or within(vis.devil):
            return CLEAR
        return SENSED if within(vis.blind) else None
    if lv == 2:
        return CLEAR
    if lv == 1:
        return CLEAR if within(vis.dark) or within(vis.true) or within(vis.devil) else DIM_SEEN
    if within(vis.true) or within(vis.devil):
        return CLEAR
    if within(vis.dark):
        return GREY_SEEN
    return SENSED if within(vis.blind) else None


def viewer_sees(spec, level, blocks, vx, vy, vis, tx, ty, radius):
    import math
    if (tx, ty) == (vx, vy):
        q = sees(vis, 0.0, level[(tx, ty)])
        return SENSED if q is None else q      # a creature knows where it stands
    d = math.hypot(tx - vx, ty - vy)
    if d > radius + 0.5 or not _clear(blocks, vx, vy, tx, ty):
        return None
    return sees(vis, d, level[(tx, ty)])


def _sight_radius(spec: MapSpec) -> int:
    # With a light model the light limits the eye; `sight:` still caps it when written.
    return spec.sight if spec.sight > 0 else max(len(spec.rows[0]), len(spec.rows))


def _side(spec: MapSpec, ch: str) -> str:
    return spec.legend.get(ch, (ch, "pc" if ch.isupper() else "foe"))[1]


def party_view(spec: MapSpec, level: dict) -> dict:
    """(x, y) → how well the party (pc and ally tokens) sees it, for every square someone sees."""
    w, h = len(spec.rows[0]), len(spec.rows)
    blocks = _blocks(spec)
    radius = _sight_radius(spec)
    view = {}
    for vy, row in enumerate(spec.rows):
        for vx, ch in enumerate(row):
            if not ch.isalpha() or _side(spec, ch) not in ("pc", "ally"):
                continue
            vis = spec.vision.get(ch)
            for ty in range(max(0, vy - radius), min(h, vy + radius + 1)):
                for tx in range(max(0, vx - radius), min(w, vx + radius + 1)):
                    if blocks[ty][tx] and (tx, ty) != (vx, vy):
                        continue
                    q = viewer_sees(spec, level, blocks, vx, vy, vis, tx, ty, radius)
                    if q is not None and q > view.get((tx, ty), -1):
                        view[(tx, ty)] = q
    # The walls and doors that close a seen open square are seen as well as it.
    for (x, y), q in list(view.items()):
        if blocks[y][x]:
            continue
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h and blocks[ny][nx] and _cell_kind(spec, nx, ny) != "void":
                    view[(nx, ny)] = max(view.get((nx, ny), -1), q)
    for (x0, y0, x1, y1) in spec.reveal:
        view.update({(x, y): CLEAR for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)})
    return view


def _feet(squares: float, spec: MapSpec) -> str:
    return f"{round(squares / squares_per_foot(spec))} ft"


def sight_report(spec: MapSpec, level: dict) -> list:
    """For the DM: who sees whom on this map, and how well (only with a light model)."""
    if not level:
        return []
    blocks = _blocks(spec)
    radius = _sight_radius(spec)
    tokens = []
    for y, row in enumerate(spec.rows):
        for x, ch in enumerate(row):
            if ch.isalpha() and all(t[0] != ch for t in tokens):
                tokens.append((ch, x, y))
    party = [t for t in tokens if _side(spec, t[0]) in ("pc", "ally")]
    others = [t for t in tokens if _side(spec, t[0]) not in ("pc", "ally")]
    lines = []
    for viewer in party + others:
        ch, vx, vy = viewer
        vis = spec.vision.get(ch)
        name = spec.legend.get(ch, (ch, ""))[0]
        senses = ", ".join(f"{label} {_feet(getattr(vis, attr), spec)}" for attr, label in
                           (("dark", "darkvision"), ("blind", "blindsight"), ("true", "truesight"), ("devil", "devil's sight"))
                           if vis and getattr(vis, attr))
        seen, unseen = [], []
        for t, tx, ty in (others if viewer in party else party):
            q = viewer_sees(spec, level, blocks, vx, vy, vis, tx, ty, radius)
            if q is None:
                unseen.append(t)
            else:
                seen.append(t + (f" ({QUALITY_NAMES[q]})" if QUALITY_NAMES[q] else ""))
        head = f"{name} ({ch}, {WHERE_NAMES[level[(vx, vy)]]}" + (f", {senses}" if senses else "") + ")"
        lines.append(f"{head} sees: {', '.join(seen) or 'nobody'}" + (f"; does not see: {', '.join(unseen)}" if unseen else ""))
    blind = [t[0] for t in others if not spec.vision.get(t[0])]
    if blind and any(level[(x, y)] < 2 for _c, x, y in tokens):
        lines.append(f"no senses written for {', '.join(blind)} — if they have darkvision, add `| darkvision 18m` "
                     "to their legend line")
    return lines


def _draw_light(spec: MapSpec, level: dict, fog, view) -> list:
    """Shade what is dim or dark — the place's light, or (with fog) how well the party sees each square."""
    out = []
    for y, row in enumerate(spec.rows):
        x = 0
        while x < len(row):
            def shade(i):
                if _cell_kind(spec, i, y) == "void":
                    return None
                if fog is None:
                    return LIGHT_SHADE.get(level[(i, y)])
                return VIEW_SHADE.get(view.get((i, y))) if (i, y) in fog[0] else None
            s = shade(x)
            if s is None:
                x += 1
                continue
            start = x
            while x < len(row) and shade(x) == s:
                x += 1
            out.append(f'<rect class="light" x="{PAD + start * CELL}" y="{PAD + y * CELL}" width="{(x - start) * CELL}" '
                       f'height="{CELL}" fill="{s[0]}" fill-opacity="{s[1]}"/>')
    for src in spec.lights:
        if src.token or (fog is not None and (src.x, src.y) not in fog[0]):
            continue
        cx, cy = PAD + src.x * CELL + CELL / 2, PAD + src.y * CELL + CELL / 2
        out.append(f'<g><title>{escape(src.kind)}</title><circle cx="{cx}" cy="{cy}" r="{CELL * 0.3}" fill="#ffd27a" '
                   f'fill-opacity="0.55"/><text x="{cx}" y="{cy + 6}" font-size="17" text-anchor="middle" '
                   f'fill="#a85a00">✹</text></g>')
    return out


def _draw_carried(spec: MapSpec, fog) -> list:
    """A small flame on the tokens that carry a light."""
    out = []
    for src in spec.lights:
        if not src.token or not _token_shown(spec, src.token, src.x, src.y, fog):
            continue
        cx, cy = PAD + src.x * CELL + CELL * 0.82, PAD + src.y * CELL + CELL * 0.18
        out.append(f'<g><title>{escape(src.kind)}</title><circle cx="{cx}" cy="{cy}" r="7.5" fill="#ffd27a" '
                   f'stroke="#a85a00" stroke-width="1.2"/><text x="{cx}" y="{cy + 4}" font-size="10" '
                   f'text-anchor="middle" fill="#a85a00">✹</text></g>')
    return out


# Senses and lights that live outside the map text: the PCs' sheets and the
# party's supplies (supplies.py light … --by NAME).
_SHEET_SENSE_RE = re.compile(
    r"(superior\s+darkvision|darkvision|scurovisione(?:\s+superiore)?|blindsight|vista\s+cieca|truesight|vista\s+pura"
    r"|devil'?s\s+sight|vista\s+del\s+diavolo)\b[^\n\d]{0,120}?(\d+(?:[.,]\d+)?)\s*(m\b|metri|ft\b|feet|piedi)", re.I)


def sheet_senses(text: str, per_foot: float) -> Vision:
    """The senses a character sheet names with their range (Darkvision / Scurovisione 18 m …)."""
    vis = Vision()
    for m in _SHEET_SENSE_RE.finditer(text or ""):
        sense = SENSES.get("-".join(m.group(1).lower().split()))
        size = _size(m.group(2) + m.group(3).lower().rstrip(), per_foot)
        if sense and size and size > getattr(vis, sense):
            setattr(vis, sense, size)
    return vis


def _name_key(text: str) -> str:
    return " ".join((text or "").casefold().split())


def match_name(names: list, wanted: str):
    """The one name of `names` that `wanted` means (exact, else the first word, else a part), or None."""
    w = _name_key(wanted)
    if not w:
        return None
    for test in (lambda n: _name_key(n) == w,
                 lambda n: _name_key(n).split()[0] == w.split()[0],
                 lambda n: w in _name_key(n) or _name_key(n) in w):
        hits = [n for n in names if n and test(n)]
        if len(hits) == 1:
            return hits[0]
        if hits:
            return None
    return None


def add_sheet_senses(spec: MapSpec, campaign: str) -> list:
    """Give every pc token without senses in the legend the ones its sheet names. Returns the letters done."""
    import media
    try:
        folder = media.find_campaign(campaign) / "characters"
    except Exception:
        return []
    sheets = {}
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        first = text.splitlines()[0] if text else ""
        sheets[first[2:].strip() if first.startswith("# ") else path.stem] = text
    done = []
    for ch, (name, side) in spec.legend.items():
        if side != "pc" or ch in spec.vision:
            continue
        hit = match_name(list(sheets), name)
        if hit:
            vis = sheet_senses(sheets[hit], squares_per_foot(spec))
            if vis:
                spec.vision[ch] = vis
                done.append(ch)
    return done


def add_supply_lights(spec: MapSpec, campaign: str) -> list:
    """The lights burning in the party's supplies, on the tokens carrying them. Returns lines for the DM."""
    import json
    import media
    try:
        data = json.loads((media.find_campaign(campaign) / "supplies.json").read_text(encoding="utf-8"))
    except Exception:
        return []
    per_foot = squares_per_foot(spec)
    names = {name: ch for ch, (name, _side) in spec.legend.items()}
    news = []
    for lit in data.get("lights") or []:
        kind = LIGHT_WORDS.get(str(lit.get("kind", "")), str(lit.get("kind", "")))
        if kind not in LIGHT_KINDS:
            continue
        by = str(lit.get("by") or "")
        hit = match_name(list(names), by) if by else None
        if not hit:
            news.append(f"supplies: a {kind} is burning" + (f" for {by!r}, who is not on this map" if by else
                        " but nobody carries it — supplies.py carry <kind> <NAME> puts it in a hand"))
            continue
        ch = names[hit]
        if any(s.token == ch and s.kind == kind for s in spec.lights):
            continue
        at = token_position(spec, ch)
        if at is None:
            continue
        bright, dim, _cone = LIGHT_KINDS[kind]
        spec.lights.append(LightSource(at[0], at[1], bright * per_foot, dim * per_foot, kind, ch))
    return news


def _fog_file(d, map_id: str):
    return d / f"fog-{map_id}.json"


def load_explored(d, map_id: str, w: int, h: int) -> set:
    """What the party has already seen of this map; nothing if the map changed size."""
    import json
    try:
        data = json.loads(_fog_file(d, map_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(data, dict) or data.get("size") != [w, h]:
        return set()
    return {(int(x), int(y)) for x, y in data.get("seen", []) if 0 <= int(x) < w and 0 <= int(y) < h}


def save_explored(d, map_id: str, w: int, h: int, explored: set) -> None:
    import json
    _fog_file(d, map_id).write_text(json.dumps({"size": [w, h], "seen": sorted(explored)}), encoding="utf-8")


def _token_shown(spec: MapSpec, ch: str, x: int, y: int, fog) -> bool:
    if fog is None:
        return True
    side = spec.legend.get(ch, (ch, "pc" if ch.isupper() else "foe"))[1]
    return side in ("pc", "ally") or (x, y) in fog[0]


def hidden_tokens(spec: MapSpec, fog) -> list:
    return [ch for y, row in enumerate(spec.rows) for x, ch in enumerate(row)
            if ch.isalpha() and not _token_shown(spec, ch, x, y, fog)]


def _draw_fog(spec: MapSpec, fog) -> list:
    """Dark over what was never seen, a veil over what is remembered; one rect per run of squares."""
    visible, explored = fog
    out = []
    for y, row in enumerate(spec.rows):
        x = 0
        while x < len(row):
            state = 0 if (x, y) in visible else 1 if (x, y) in explored else 2
            if state == 0:
                x += 1
                continue
            start = x
            while x < len(row) and (0 if (x, y) in visible else 1 if (x, y) in explored else 2) == state:
                x += 1
            out.append(f'<rect class="fog{state}" x="{PAD + start * CELL}" y="{PAD + y * CELL}" '
                       f'width="{(x - start) * CELL}" height="{CELL}" fill="{FOG_DARK}"'
                       + (f' fill-opacity="{FOG_DIM}"' if state == 1 else "") + "/>")
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


def _draw_template(e: Effect) -> list:
    fill, glyph = EFFECTS[e.kind]
    out = [f'<g><title>{escape(e.label or e.shape or e.kind)}</title>']
    for (i, j) in sorted(e.cells):
        out.append(f'<rect x="{PAD + i * CELL}" y="{PAD + j * CELL}" width="{CELL}" height="{CELL}" '
                   f'fill="{fill}" fill-opacity="0.38"/>')
    px = lambda v: round(PAD + v * CELL, 1)   # noqa: E731
    if e.outline[0] == "circle":
        _, cx, cy, r = e.outline
        out.append(f'<circle cx="{px(cx)}" cy="{px(cy)}" r="{round(r * CELL, 1)}" fill="none" stroke="{fill}" '
                   f'stroke-width="3" stroke-dasharray="8 4"/>')
        gx, gy = px(cx), px(cy)
    else:
        pts = " ".join(f"{px(a)},{px(b)}" for a, b in e.outline[1])
        out.append(f'<polygon points="{pts}" fill="none" stroke="{fill}" stroke-width="3" stroke-dasharray="8 4"/>')
        gx = round(sum(px(a) for a, _ in e.outline[1]) / len(e.outline[1]), 1)
        gy = round(sum(px(b) for _, b in e.outline[1]) / len(e.outline[1]), 1)
    out.append(f'<text x="{gx}" y="{gy + 10}" font-size="28" text-anchor="middle" fill="{fill}" stroke="#000" '
               f'stroke-width="0.8" opacity="0.9">{glyph}</text></g>')
    return out


def _draw_effect(e: Effect) -> list:
    if e.cells is not None:
        return _draw_template(e)
    fill, glyph = EFFECTS[e.kind]
    px, py = PAD + e.x0 * CELL, PAD + e.y0 * CELL
    w, h = (e.x1 - e.x0 + 1) * CELL, (e.y1 - e.y0 + 1) * CELL
    cx, cy = px + w / 2, py + h / 2
    out = [f'<g><title>{escape(e.label or e.kind)}</title>',
           f'<rect x="{px + 2}" y="{py + 2}" width="{w - 4}" height="{h - 4}" rx="6" fill="{fill}" '
           f'fill-opacity="0.42" stroke="{fill}" stroke-width="3" stroke-dasharray="7 4"/>',
           f'<text x="{cx}" y="{cy + 11}" font-size="{min(30, 14 + 6 * min(w, h) // CELL)}" '
           f'text-anchor="middle" fill="{fill}" stroke="#000" stroke-width="0.8" '
           f'opacity="0.9">{glyph}</text></g>']
    return out


def describe_templates(spec: MapSpec) -> list:
    """One line per template for the DM: what it is and who is in it."""
    lines = []
    for e in spec.effects:
        if e.cells is None:
            continue
        caught = tokens_in(spec, e)
        who = ", ".join(f"{name} ({side})" for _ch, name, side in caught) or "nobody"
        lines.append(f"{e.label or e.kind} [{e.shape}, {len(e.cells)} squares]: {who}")
    return lines


def kind_grid(spec: MapSpec) -> list:
    """Terrain kind of every square (tokens stand on floor) — what map_art paints."""
    return [[_cell_kind(spec, x, y) for x in range(len(spec.rows[0]))]
            for y in range(len(spec.rows))]


def _art_defs(art) -> list:
    if art is None or not art.textures:
        return []
    import map_art
    out = ["<defs>"]
    for kind, (mime, data) in art.textures.items():
        # Only the middle half of the texture is used: models often add
        # perspective or walls near the edges, and Pollinations a corner logo.
        out.append(f'<pattern id="tex-{kind}" patternUnits="userSpaceOnUse" x="{PAD}" y="{PAD}" '
                   f'width="{TEX_TILE}" height="{TEX_TILE}"><image href="{map_art.data_uri(mime, data)}" '
                   f'x="{-TEX_TILE / 2}" y="{-TEX_TILE / 2}" width="{TEX_TILE * 2}" height="{TEX_TILE * 2}" '
                   f'preserveAspectRatio="none"/></pattern>')
    out.append("</defs>")
    return out


def render_svg(spec: MapSpec, art=None, fog=None, lang: str = "en") -> str:
    """SVG of the map; `art` (a map_art.Art) replaces the vector terrain.

    `fog` is (visible, explored) — two sets of (x, y) — or None for a map with
    no fog of war. `lang` is for the light key under a map with a light model."""
    w, h = len(spec.rows[0]), len(spec.rows)
    level = light_levels(spec)
    view = party_view(spec, level) if level and fog is not None else {}
    grid_w, grid_h = w * CELL, h * CELL
    title_h = 34 if spec.title else 0

    # Legend: tokens that actually appear on the map, in reading order.
    seen = []
    for y, row in enumerate(spec.rows):
        for x, ch in enumerate(row):
            if ch.isalpha() and ch not in seen and _token_shown(spec, ch, x, y, fog):
                seen.append(ch)
    order = list(SIDES)   # pc, ally, npc, foe, neutral
    seen.sort(key=lambda ch: order.index(
        spec.legend.get(ch, (ch, "pc" if ch.isupper() else "foe"))[1]))
    labelled = [e for e in spec.effects if e.label and (fog is None or any(
        (x, y) in fog[0] for y in range(e.y0, e.y1 + 1) for x in range(e.x0, e.x1 + 1)))]
    cols = max(1, min(3, (grid_w + PAD) // 190))
    n_items = len(seen) + len(labelled)
    leg_rows = (n_items + cols - 1) // cols
    legend_h = (leg_rows * 24 + 16) if n_items else 0
    scale_h = 20 if spec.scale else 0
    key_h = 22 if level else 0

    total_w = PAD + grid_w + 12
    total_h = title_h + PAD + grid_h + 12 + legend_h + key_h + scale_h

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="{total_h}" '
           f'viewBox="0 0 {total_w} {total_h}" font-family="Georgia, \'Times New Roman\', serif">',
           f'<rect width="100%" height="100%" fill="{C["bg"]}"/>']
    if spec.title:
        out.append(f'<text x="{total_w / 2}" y="24" font-size="19" text-anchor="middle" '
                   f'fill="{C["ink"]}" letter-spacing="1">{escape(spec.title)}</text>')

    out.append(f'<g transform="translate(0,{title_h})">')
    out.extend(_art_defs(art))
    painted = art is not None and art.image is not None
    if painted:
        import map_art
        out.append(f'<image href="{map_art.data_uri(*art.image)}" x="{PAD}" y="{PAD}" '
                   f'width="{grid_w}" height="{grid_h}" preserveAspectRatio="none"/>')
    for y, row in enumerate(spec.rows):
        for x, ch in enumerate(row):
            out.extend(_draw_cell(_cell_kind(spec, x, y), spec, x, y, art))
    # Grid lines over playable squares only (not over void); darker over art.
    grid = (f'stroke="{C["grid"]}" stroke-width="0.8"' if art is None
            else 'stroke="#000" stroke-opacity="0.35" stroke-width="1"')
    for y, row in enumerate(spec.rows):
        for x, ch in enumerate(row):
            if painted or _cell_kind(spec, x, y) != "void":
                out.append(f'<rect x="{PAD + x * CELL}" y="{PAD + y * CELL}" width="{CELL}" '
                           f'height="{CELL}" fill="none" {grid}/>')
    for e in spec.effects:
        out.extend(_draw_effect(e))
    if level:
        out.extend(_draw_light(spec, level, fog, view))
    if fog is not None:
        out.extend(_draw_fog(spec, fog))
    for y, row in enumerate(spec.rows):
        for x, ch in enumerate(row):
            if ch.isalpha() and _token_shown(spec, ch, x, y, fog):
                out.extend(_draw_token(ch, spec, x, y))
    if level:
        out.extend(_draw_carried(spec, fog))
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
    for j, e in enumerate(labelled, start=len(seen)):
        fill, glyph = EFFECTS[e.kind]
        lx = PAD + (j % cols) * col_w
        yy = ly + (j // cols) * 24
        out.append(f'<rect x="{lx}" y="{yy - 14}" width="18" height="18" rx="4" fill="{fill}" '
                   f'fill-opacity="0.6" stroke="{fill}" stroke-width="2" stroke-dasharray="4 2"/>')
        caught = list(dict.fromkeys(ch for y, row in enumerate(spec.rows) for x, ch in enumerate(row)
                                    if ch.isalpha() and e.cells is not None and (x, y) in e.cells
                                    and _token_shown(spec, ch, x, y, fog)))
        text = e.label[:28] + (f": {', '.join(caught)}" if caught else "")
        out.append(f'<text x="{lx + 24}" y="{yy}" font-size="13" font-style="italic" '
                   f'fill="{C["ink"]}">{escape(text[:40])}</text>')
    if spec.scale:
        out.append(f'<text x="{total_w - 12}" y="{total_h - 8}" font-size="11" text-anchor="end" '
                   f'font-style="italic" fill="{C["ink"]}" opacity="0.7">{escape(spec.scale)}</text>')
    if level:
        # The light key: what the shades on the map mean.
        names = LIGHT_KEY.get((lang or "en")[:2], LIGHT_KEY["en"])
        swatches = [(None, names[0]), (LIGHT_SHADE[1], names[1]), (LIGHT_SHADE[0], names[2])]
        if fog is not None:
            swatches.append((VIEW_SHADE[GREY_SEEN], names[3]))
        kx, key_y = PAD, total_h - scale_h - 9
        for shade, label in swatches:
            fill = f'fill="{shade[0]}" fill-opacity="{shade[1]}"' if shade else f'fill="{C["floor"]}"'
            out.append(f'<rect x="{kx}" y="{key_y - 10}" width="12" height="12" {fill} stroke="{C["ink"]}" '
                       f'stroke-width="0.8"/>')
            out.append(f'<text x="{kx + 16}" y="{key_y}" font-size="11" fill="{C["ink"]}" '
                       f'opacity="0.8">{escape(label)}</text>')
            kx += 24 + 6.2 * len(label)
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
    p.add_argument("--art", choices=("auto", "paint", "tiles", "off"),
                   help="terrain art from the image backend (default: map_art in images.json, else auto)")
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
    for line in describe_templates(spec):
        print(f"map_render: area — {line}", file=sys.stderr)
    lang = "en"
    if spec.light:
        # The PCs' darkvision from their sheets, the torches burning in the supplies.
        import media
        import paths
        camp = media.safe_campaign(args.campaign) if args.campaign else media.active_campaign()
        if camp:
            add_sheet_senses(spec, camp)
            for line in add_supply_lights(spec, camp):
                print(f"map_render: {line}", file=sys.stderr)
            lang = paths.campaign_language(camp)
        for line in sight_report(spec, light_levels(spec)):
            print(f"map_render: sight — {line}", file=sys.stderr)
    fog = None
    if spec.fog:
        now = visible_cells(spec)
        fog = (now, set(now))          # with --out there is no campaign to remember in
        if not now:
            print("map_render: fog is on but no pc or ally token is on the map — everything is dark", file=sys.stderr)
    svg = render_svg(spec, fog=fog, lang=lang)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(svg)
        print(args.out)
        return 0

    import image_gen
    import map_art
    import media
    try:
        d = media.media_dir(args.campaign)
    except RuntimeError as e:
        print(f"map_render: {e}", file=sys.stderr)
        return 2
    map_id = media.slug(args.name or spec.title or "map")
    caption = args.caption if args.caption is not None else spec.title
    if fog is not None:
        size = (len(spec.rows[0]), len(spec.rows))
        before = set() if spec.fog_reset else load_explored(d, map_id, *size)
        fog = (fog[0], before | fog[0])
        save_explored(d, map_id, *size, fog[1])
        svg = render_svg(spec, fog=fog, lang=lang)
        unseen = hidden_tokens(spec, fog)
        if unseen:
            print(f"map_render: fog hides {len(unseen)} token(s) the party cannot see: {', '.join(unseen)}", file=sys.stderr)

    def save_and_show(svg_text: str) -> None:
        digest = hashlib.sha1(svg_text.encode("utf-8")).hexdigest()[:8]
        fname = f"map-{map_id}-{digest}.svg"
        (d / fname).write_text(svg_text, encoding="utf-8")
        print(str(d / fname))
        if not args.no_send and not media.post_image(fname, caption=caption, kind="map", subject=map_id):
            print("map_render: display offline — map saved but not shown", file=sys.stderr)

    def log(msg: str) -> None:
        print(f"map_render: {msg}", file=sys.stderr)

    cfg = image_gen.load_config()
    mode = map_art.resolve_mode(args.art or cfg.get("map_art"), cfg["backend"])
    if mode == "off":
        save_and_show(svg)
        return 0
    kinds = kind_grid(spec)
    art = map_art.get_art(kinds, spec.art, d, cfg, mode, generate=False)
    if art is None:
        # New terrain: show the plain map now; the painted one supersedes it.
        if not args.no_send:
            save_and_show(svg)
        try:
            art = map_art.get_art(kinds, spec.art, d, cfg, mode, log=log)
        except map_art.ImageError as e:
            log(f"map art unavailable ({e}) — keeping the plain map")
            if args.no_send:
                save_and_show(svg)
            return 0
    elif art.missing:
        # Some textures failed last time: retry them; what exists is kept.
        try:
            art = map_art.get_art(kinds, spec.art, d, cfg, mode, log=log)
        except map_art.ImageError:
            pass
    save_and_show(render_svg(spec, art, fog=fog, lang=lang))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
