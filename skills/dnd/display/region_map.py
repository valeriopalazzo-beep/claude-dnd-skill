#!/usr/bin/env python3
"""region_map.py — the map of the region, drawn as the party discovers it.

Places the party has visited or heard of, the roads between them with the days
of travel, and where the party is now. The DM adds a place when the characters
learn of it — never before — so the map grows with the story. It is kept in
<campaign>/region.json, drawn on parchment as an SVG in <campaign>/media/, and
every screen has it one tap away, with travel times from where the party is.

    python3 region_map.py init "Valle di Cenere" --legend "I numeri sulle strade sono giorni di cammino"

    # The first place needs a position: --at x,y on a 0–100 grid (x → east, y → south).
    python3 region_map.py add "Sarvenna" --kind city --at 35,60 --note "Capitale del ducato"
    # After that, say where a place is from one already on the map: it is placed
    # in that direction, that many days away, and the road is drawn too.
    python3 region_map.py add "Cripta di Vessar" --kind dungeon --near "Sarvenna" --dir NE --days 2 --road trail
    python3 region_map.py add "Porto Grigio" --kind port --near "Sarvenna" --dir W --days 3 --rumor   # heard of, not seen

    python3 region_map.py road "Cripta di Vessar" "Porto Grigio" --days 4 --kind trail
    python3 region_map.py here "Sarvenna"              # the party is here (marks it visited, shows the map)
    python3 region_map.py travel "Cripta di Vessar"    # shortest known route: prints the legs and the days, moves the party
    python3 region_map.py note "Sarvenna" "Il duca è morto; reggenza del siniscalco"
    python3 region_map.py mark "Porto Grigio" visited  # or rumor — nothing goes in the story, the party stays put
    python3 region_map.py here "Sarvenna" --quiet      # move the marker without a postcard (travel --quiet too)
    python3 region_map.py remove "Porto Grigio"
    python3 region_map.py list
    python3 region_map.py show                         # put the map in the story now

A small region — a village and what lies an hour or two around it — is drawn
in hours instead of days, so that an hour and half a day are not the same
stretch of paper:

    python3 region_map.py scale hours                  # the roads now read in hours; the places are set out again
    python3 region_map.py add "Carbonaia" --near "Vallombra" --dir W --hours 1 --road trail
    python3 region_map.py road "Carbonaia" "La Rocca Muta" --hours 3
    python3 region_map.py relayout                     # set every place at the distance its road says

Kinds: city, town, village, castle, tower, ruin, dungeon, forest, mountain,
lake, port, temple, camp, landmark (Italian words such as città, villaggio,
rovine, cripta, bosco are understood). Roads: road, trail, river, sea.

`--campaign NAME` (before the command) works on a campaign other than the
active one. `travel` does not move the calendar: it prints the calendar.py
command for the days it took, because what happens on the road is the DM's call
(and the days at the party's pace, when travel.py keeps one).

A party that got lost (travel.py) and knows it is drawn as a question mark off
the roads, with no distances on the players' screens; `travel` refuses until it
is found, and `here` puts it somewhere and ends it.
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import os
import sys
from xml.sax.saxutils import escape

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

W, H = 1200, 900                      # the drawing, in px
PAD_X, PAD_TOP, PAD_BOTTOM = 90, 120, 100
KEEP_FILES = 12                       # older renderings of the map kept for the story's replay
MIN_GAP_PX = 105                      # two linked places are drawn at least this far apart
HOURS_PER_DAY = 8                     # a day on the road is eight hours of travel
UNITS = ("days", "hours")
MAX_ZOOM = 2.5                        # how much a map in hours may be enlarged to fill the sheet
CLOSE_UP_MARGIN = 100                 # …and the room it leaves at each side for the names

KINDS = ("city", "town", "village", "castle", "tower", "ruin", "dungeon", "forest",
         "mountain", "lake", "port", "temple", "camp", "landmark")
KIND_ALIASES = {
    "citta": "city", "città": "city", "capitale": "city", "cittadina": "town", "paese": "town",
    "borgo": "town", "villaggio": "village", "castello": "castle", "fortezza": "castle",
    "rocca": "castle", "keep": "castle", "torre": "tower", "rovine": "ruin", "rovina": "ruin",
    "ruins": "ruin", "cripta": "dungeon", "caverna": "dungeon", "grotta": "dungeon",
    "cave": "dungeon", "miniera": "dungeon", "mine": "dungeon", "crypt": "dungeon",
    "foresta": "forest", "bosco": "forest", "montagna": "mountain", "monti": "mountain",
    "mountains": "mountain", "lago": "lake", "porto": "port", "tempio": "temple",
    "santuario": "temple", "abbazia": "temple", "accampamento": "camp", "campo": "camp",
    "luogo": "landmark",
}
ROAD_KINDS = ("road", "trail", "river", "sea")
ROAD_ALIASES = {"strada": "road", "sentiero": "trail", "pista": "trail", "fiume": "river", "mare": "sea"}
DIRECTIONS = {
    "N": (0, -1), "NE": (1, -1), "E": (1, 0), "SE": (1, 1),
    "S": (0, 1), "SW": (-1, 1), "W": (-1, 0), "NW": (-1, -1),
    "SO": (-1, 1), "O": (-1, 0), "NO": (-1, -1),      # Italian: ovest
}
C = {"bg": "#efe4c8", "ink": "#3b2f22", "road": "#6b4a2b", "water": "#5f8fa8",
     "party": "#b03a2e", "shade": "#c9b78c"}


class RegionError(Exception):
    """A request that cannot be applied; the map is left as it was."""


# ─── The data ─────────────────────────────────────────────────────────────────

def _campaign_dir(campaign: str = ""):
    import media
    camp = media.safe_campaign(campaign) if campaign else media.active_campaign()
    if not camp:
        raise RegionError("no active campaign — pass --campaign or load one first")
    return media.find_campaign(camp)


def data_path(campaign: str = "") -> str:
    return str(_campaign_dir(campaign) / "region.json")


def empty() -> dict:
    return {"title": "", "legend": "", "day_px": 100, "unit": "days", "hour_px": 110, "party": "",
            "places": [], "roads": [], "file": "", "lost": None}


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        return empty()
    except ValueError as e:
        raise RegionError(f"region.json is not valid JSON ({e})")
    base = empty()
    if isinstance(data, dict):
        base.update({k: data[k] for k in base if k in data})
    return base


def save(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _key(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def find_place(data: dict, name: str) -> dict:
    """The place `name` means: exact name first, else a unique part of it."""
    want = _key(name)
    if not want:
        raise RegionError("which place?")
    hits = [p for p in data["places"] if _key(p["name"]) == want] or \
           [p for p in data["places"] if want in _key(p["name"])]
    if not hits:
        known = ", ".join(p["name"] for p in data["places"]) or "none yet"
        raise RegionError(f"no place called {name!r} on the map (places: {known})")
    if len(hits) > 1:
        raise RegionError(f"{name!r} matches several places: " + ", ".join(p["name"] for p in hits))
    return hits[0]


def _kind(value: str, table: tuple, aliases: dict, what: str) -> str:
    k = (value or table[-1 if what == "place" else 0]).strip().lower()
    k = aliases.get(k, k)
    if k not in table:
        raise RegionError(f"unknown {what} kind {value!r} (use: {', '.join(table)})")
    return k


def _days(value) -> float:
    try:
        d = float(str(value).replace(",", "."))
    except ValueError:
        raise RegionError(f"days must be a number (got {value!r})")
    if not 0 < d <= 365:
        raise RegionError("days must be more than 0")
    return d


def fmt_days(d: float) -> str:
    if abs(d - round(d)) < 1e-9:
        return str(int(round(d)))
    whole, frac = int(d), d - int(d)
    if abs(frac - 0.5) < 1e-9:
        return (str(whole) if whole else "") + "½"
    return f"{d:.1f}".rstrip("0").rstrip(".")


def unit(data: dict) -> str:
    return data.get("unit") if data.get("unit") in UNITS else "days"


def fmt_hours(days: float) -> str:
    """A stretch of road in hours, to the quarter of an hour: ¼, 1, 1½, 3¾, 12."""
    h = float(days) * HOURS_PER_DAY
    if h >= 10:
        return str(int(round(h)))
    quarters = int(round(h * 4)) or (1 if h > 0 else 0)
    if not quarters:
        return "0"
    whole, part = divmod(quarters, 4)
    return (str(whole) if whole else "") + ("", "¼", "½", "¾")[part]


def fmt_dist(data: dict, days: float) -> str:
    """A distance as this map counts it: days, or hours on a small region."""
    return fmt_hours(days) if unit(data) == "hours" else fmt_days(days)


def _length(days=None, hours=None) -> float:
    """A road's length in days, from --days or --hours (one of them; a day when neither is given)."""
    if days is not None and hours is not None:
        raise RegionError("give --days or --hours, not both")
    if hours is None:
        return _days(days if days is not None else 1)
    try:
        h = float(str(hours).replace(",", "."))
    except ValueError:
        raise RegionError(f"hours must be a number (got {hours!r})")
    if not 0 < h <= 365 * HOURS_PER_DAY:
        raise RegionError("hours must be more than 0")
    return h / HOURS_PER_DAY


def _step_px(data: dict, days: float) -> float:
    """How long a road of `days` is on the drawing, before any zoom — never shorter than MIN_GAP_PX."""
    if unit(data) == "hours":
        return max(MIN_GAP_PX, days * HOURS_PER_DAY * float(data.get("hour_px") or 110))
    return max(MIN_GAP_PX, days * float(data.get("day_px") or 100))


def add_place(data: dict, name: str, kind: str = "", at: str = "", near: str = "", direction: str = "",
              days=None, road: str = "", note: str = "", rumor: bool = False, hours=None) -> dict:
    name = " ".join((name or "").split())[:60]
    if not name:
        raise RegionError("a place needs a name")
    if any(_key(p["name"]) == _key(name) for p in data["places"]):
        raise RegionError(f"{name!r} is already on the map (use note / road / here to change it)")
    kind = _kind(kind, KINDS, KIND_ALIASES, "place")
    link = None
    if at:
        try:
            x, y = (float(v) for v in at.replace(";", ",").split(","))
        except ValueError:
            raise RegionError(f"--at wants two numbers from 0 to 100: x,y (got {at!r})")
    elif near:
        origin = find_place(data, near)
        d = (direction or "").strip().upper()
        if d not in DIRECTIONS:
            raise RegionError(f"--dir must be one of N NE E SE S SW W NW (got {direction!r})")
        n = _length(days, hours)
        dx, dy = DIRECTIONS[d]
        # One day is the same length on the drawing whichever way you walk, and
        # never so short that two places would sit on top of each other.
        length = math.hypot(dx, dy)
        step = _step_px(data, n)
        X0, Y0 = _raw(origin)
        x = (X0 + dx / length * step) / _AREA_W * 100
        y = (Y0 + dy / length * step) / _AREA_H * 100
        link = {"from": origin["name"], "to": name, "days": n,
                "kind": _kind(road, ROAD_KINDS, ROAD_ALIASES, "road")}
    else:
        raise RegionError("say where it is: --at x,y, or --near PLACE --dir NE --days 2 (or --hours 3)")
    place = {"name": name, "kind": kind, "x": round(x, 1), "y": round(y, 1),
             "status": "known" if rumor else "visited",
             "note": " ".join((note or "").split())[:300]}
    data["places"].append(place)
    if link:
        data["roads"].append(link)
    return place


def add_road(data: dict, a: str, b: str, days=None, kind: str = "", hours=None) -> dict:
    pa, pb = find_place(data, a), find_place(data, b)
    if pa is pb:
        raise RegionError("a road needs two different places")
    if days is None and hours is None:
        raise RegionError("how long is the road? --days 2, or --hours 3")
    link = {"from": pa["name"], "to": pb["name"], "days": _length(days, hours),
            "kind": _kind(kind, ROAD_KINDS, ROAD_ALIASES, "road")}
    pair = {_key(pa["name"]), _key(pb["name"])}
    data["roads"] = [r for r in data["roads"] if {_key(r["from"]), _key(r["to"])} != pair] + [link]
    return link


def remove_place(data: dict, name: str) -> str:
    place = find_place(data, name)
    k = _key(place["name"])
    data["places"] = [p for p in data["places"] if p is not place]
    data["roads"] = [r for r in data["roads"] if k not in (_key(r["from"]), _key(r["to"]))]
    if _key(data.get("party", "")) == k:
        data["party"] = ""
    return place["name"]


def mark(data: dict, name: str, status: str) -> dict:
    """A place the party has now seen, or only heard of — the party's marker does not move."""
    status = {"visited": "visited", "visto": "visited", "visitato": "visited", "seen": "visited",
              "rumor": "known", "rumour": "known", "known": "known", "voce": "known",
              "sentito": "known"}.get((status or "").strip().lower())
    if not status:
        raise RegionError("mark a place visited or rumor")
    place = find_place(data, name)
    place["status"] = status
    return place


def relayout(data: dict) -> int:
    """Set every place at the distance its road says, keeping the directions. Returns how many moved.

    The first place of each group of linked places stays where it is and the
    others are set out from it along the roads, in the order the roads were
    drawn — so a place sits where its first road puts it, as `add --near` does.
    A place no road reaches keeps its offset from the nearest linked one.
    """
    old = {_key(p["name"]): _raw(p) for p in data["places"]}
    links: dict = {}
    for r in data["roads"]:
        a, b = _key(r["from"]), _key(r["to"])
        if a in old and b in old and a != b:
            links.setdefault(a, []).append((b, float(r["days"])))
            links.setdefault(b, []).append((a, float(r["days"])))
    new: dict = {}
    for root in old:
        if root in new or root not in links:
            continue
        new[root] = old[root]
        queue = [root]
        while queue:
            at = queue.pop(0)
            for nxt, days in links[at]:
                if nxt in new:
                    continue
                dx, dy = old[nxt][0] - old[at][0], old[nxt][1] - old[at][1]
                length = math.hypot(dx, dy)
                if length < 1e-6:
                    dx, dy, length = 1.0, 0.0, 1.0
                step = _step_px(data, days)
                new[nxt] = (new[at][0] + dx / length * step, new[at][1] + dy / length * step)
                queue.append(nxt)
    for k in old:
        if k not in new:
            anchor = min(new, key=lambda n: math.hypot(old[n][0] - old[k][0], old[n][1] - old[k][1]), default=None)
            new[k] = old[k] if anchor is None else (new[anchor][0] + old[k][0] - old[anchor][0],
                                                    new[anchor][1] + old[k][1] - old[anchor][1])
    moved = 0
    for p in data["places"]:
        x, y = new[_key(p["name"])]
        x, y = round(x / _AREA_W * 100, 1), round(y / _AREA_H * 100, 1)
        moved += (x, y) != (p["x"], p["y"])
        p["x"], p["y"] = x, y
    return moved


def set_scale(data: dict, value: str, hour_px=None, keep: bool = False) -> int:
    """Count this map in days or in hours. Returns how many places were set out again."""
    value = {"giorni": "days", "day": "days", "ore": "hours", "hour": "hours"}.get((value or "").strip().lower(),
                                                                                  (value or "").strip().lower())
    if value not in UNITS:
        raise RegionError("scale is days or hours")
    if hour_px is not None:
        if not 30 <= float(hour_px) <= 400:
            raise RegionError("--hour-px must be between 30 and 400")
        data["hour_px"] = float(hour_px)
    data["unit"] = value
    return 0 if keep else relayout(data)


def distances(data: dict, start: str) -> dict:
    """Days of travel from `start` to every place a known road reaches: {key: (days, path)}."""
    graph: dict = {}
    for r in data["roads"]:
        a, b = _key(r["from"]), _key(r["to"])
        graph.setdefault(a, []).append((b, float(r["days"])))
        graph.setdefault(b, []).append((a, float(r["days"])))
    names = {_key(p["name"]): p["name"] for p in data["places"]}
    s = _key(start)
    if s not in names:
        return {}
    best = {s: (0.0, [names[s]])}
    heap = [(0.0, s)]
    while heap:
        d, node = heapq.heappop(heap)
        if d > best[node][0]:
            continue
        for nxt, cost in graph.get(node, []):
            if nxt in names and (nxt not in best or d + cost < best[nxt][0] - 1e-9):
                best[nxt] = (d + cost, best[node][1] + [names[nxt]])
                heapq.heappush(heap, (d + cost, nxt))
    return best


def set_here(data: dict, name: str) -> dict:
    place = find_place(data, name)
    place["status"] = "visited"
    data["party"] = place["name"]
    data["lost"] = None                      # the DM put the party somewhere: it is not lost any more
    return place


def travel(data: dict, name: str) -> tuple:
    """Move the party along the shortest known route: (days, [place names])."""
    dest = find_place(data, name)
    if not data.get("party"):
        raise RegionError("the map does not know where the party is — use `here PLACE` first")
    if data.get("lost"):
        raise RegionError("the party is lost (travel.py) — it finds the way first (travel.py navigate / found), "
                          "or use `here` to put it somewhere")
    if _key(dest["name"]) == _key(data["party"]):
        raise RegionError(f"the party is already at {dest['name']}")
    reach = distances(data, data["party"]).get(_key(dest["name"]))
    if not reach:
        raise RegionError(f"no known road leads from {data['party']} to {dest['name']} — "
                          f"add one with `road`, or use `here` to move the party without a route")
    days, path = reach
    for stop in path:                        # they walked through every place on the way
        find_place(data, stop)["status"] = "visited"
    data["party"] = dest["name"]
    return days, path


def public_view(data: dict) -> dict:
    """What a player's screen needs: places with their spot on the picture and the days from the party.

    A party that knows it is lost (travel.py) is nowhere on it: no "you are here", no distances."""
    lost = bool((data.get("lost") or {}).get("known"))
    reach = distances(data, data["party"]) if data.get("party") and not lost else {}
    tf = _transform(data)
    spot = _lost_spot(data, {_key(p["name"]): p for p in data["places"]}, tf, _layout(data)) if lost else None
    places = []
    for p in data["places"]:
        X, Y = _xy(p, tf)
        d = reach.get(_key(p["name"]))
        places.append({"name": p["name"], "kind": p["kind"], "status": p["status"], "note": p.get("note", ""),
                       "px": round(X / W * 100, 2), "py": round(Y / H * 100, 2),
                       "days": None if d is None else fmt_days(d[0]),
                       "dist": None if d is None else fmt_dist(data, d[0]),
                       "here": not lost and _key(p["name"]) == _key(data.get("party", ""))})
    return {"title": data.get("title", ""), "legend": data.get("legend", ""), "file": data.get("file", ""),
            "party": "" if lost else data.get("party", ""), "unit": unit(data), "places": places, "lost": lost,
            "lost_at": {"px": round(spot[0] / W * 100, 2), "py": round(spot[1] / H * 100, 2)} if spot else None}


# ─── The drawing ──────────────────────────────────────────────────────────────

_AREA_W, _AREA_H = W - 2 * PAD_X, H - PAD_TOP - PAD_BOTTOM      # where places may be drawn


def _raw(p: dict) -> tuple:
    """A place's spot before any zoom: the 0–100 grid stretched over the drawing area."""
    return p["x"] / 100 * _AREA_W, p["y"] / 100 * _AREA_H


def _transform(data: dict) -> tuple:
    """(scale, x offset, y offset) that keeps every place inside the frame.

    A region that fits is drawn exactly where its coordinates say. One that has
    grown past an edge is zoomed out as a whole and centred, so distances stay
    in proportion and nothing is pushed against the border. A map counted in
    hours is a close-up: it is also enlarged (up to MAX_ZOOM) to fill the sheet,
    so that places an hour apart do not huddle in the middle of it.
    """
    pts = [_raw(p) for p in data["places"]]
    close_up = unit(data) == "hours" and len(pts) > 1
    if not pts or (not close_up and all(0 <= x <= _AREA_W and 0 <= y <= _AREA_H for x, y in pts)):
        return 1.0, PAD_X, PAD_TOP
    x0, x1 = min(x for x, _ in pts), max(x for x, _ in pts)
    y0, y1 = min(y for _, y in pts), max(y for _, y in pts)
    # A close-up keeps a margin at the sides: the names of the outermost places need the room.
    room_w, room_h = (_AREA_W - 2 * CLOSE_UP_MARGIN, _AREA_H - 40) if close_up else (_AREA_W, _AREA_H)
    s = min(MAX_ZOOM if close_up else 1.0, room_w / max(x1 - x0, 1.0), room_h / max(y1 - y0, 1.0))
    return s, PAD_X + (_AREA_W - (x1 - x0) * s) / 2 - x0 * s, PAD_TOP + (_AREA_H - (y1 - y0) * s) / 2 - y0 * s


def _xy(p: dict, tf: tuple) -> tuple:
    s, ox, oy = tf
    rx, ry = _raw(p)
    return ox + rx * s, oy + ry * s


_ICONS = {  # drawn around (0,0) in a box of about ±24 px; "F" = filled with paper, "L" = lines only
    "city": [("F", "M-24 13 V-3 h6 v-6 h5 v6 h3 V-13 h4 v-6 h12 v6 h4 V-3 h3 v-6 h5 v6 h6 V13 Z"),
             ("L", "M-4 13 V5 a4 4 0 0 1 8 0 V13 M-2 -19 v-9 l8 3 l-8 3")],
    "town": [("F", "M-20 13 V1 l8 -9 l8 9 V13 Z"), ("F", "M-1 13 V-3 l10 -11 l10 11 V13 Z")],
    "village": [("F", "M-11 13 V-1 l11 -11 l11 11 V13 Z"), ("L", "M-3 13 V5 h6 V13")],
    "castle": [("F", "M-19 13 V-7 h5 v4 h4 v-4 h4 V-17 h12 V-7 h4 v4 h4 v-4 h5 V13 Z"),
               ("L", "M0 -17 v-11 l9 3 l-9 3 M-3 13 V4 a3 3 0 0 1 6 0 V13")],
    "tower": [("F", "M-8 13 V-11 h-3 v-8 h5 v3 h4 v-3 h4 v3 h4 v-3 h5 v8 h-3 V13 Z"),
              ("L", "M-2 13 V6 h4 V13 M-2 -4 h4")],
    "ruin": [("L", "M-22 13 H22 M-16 13 V-5 h6 V13 M-3 13 V-15 h6 V13 M11 13 V2 l3 -4 l3 4 V13 M-18 -5 h10 M-5 -15 h10")],
    "dungeon": [("F", "M-24 13 Q-20 -17 0 -17 Q20 -17 24 13 Z"), ("I", "M-7 13 V3 a7 7 0 0 1 14 0 V13 Z")],
    "forest": [("L", "M-14 7 v6 M0 3 v10 M14 7 v6"),
               ("F", "M-22 7 l8 -18 l8 18 Z"), ("F", "M6 7 l8 -18 l8 18 Z"), ("F", "M-9 3 l9 -22 l9 22 Z")],
    "mountain": [("F", "M-26 13 L-9 -17 L1 -1 L10 -11 L26 13 Z"), ("L", "M-14 -8 l5 4 l4 -5 M6 -6 l4 3 l4 -3")],
    "lake": [("W", "M-24 2 Q-22 -10 0 -10 Q22 -10 24 2 Q22 13 0 13 Q-22 13 -24 2 Z"),
             ("L", "M-12 0 q4 -4 8 0 t8 0 M-4 6 q4 -4 8 0 t8 0")],
    "port": [("L", "M0 -8 V13 M-9 -1 H9 M-15 2 Q-13 13 0 13 Q13 13 15 2"), ("F", "M-4 -12 a4 4 0 1 0 8 0 a4 4 0 1 0 -8 0 Z")],
    "temple": [("F", "M-19 -4 L0 -17 L19 -4 Z"), ("L", "M-18 13 H18 M-13 -4 V13 M-4 -4 V13 M4 -4 V13 M13 -4 V13")],
    "camp": [("F", "M-17 13 L0 -13 L17 13 Z"), ("L", "M0 -13 V13 M0 -13 l-5 -7 M0 -13 l5 -7")],
    "landmark": [("F", "M0 -17 L10 -2 L5 13 H-5 L-10 -2 Z")],
}


def _icon(kind: str) -> str:
    out = []
    for mode, d in _ICONS.get(kind, _ICONS["landmark"]):
        fill = {"F": C["bg"], "L": "none", "I": C["ink"], "W": "#bcd6e0"}[mode]
        out.append(f'<path d="{d}" fill="{fill}"/>')
    return "".join(out)


def _curve(a: tuple, b: tuple, seed: str) -> tuple:
    """A gently bent path from a to b: (svg path, control point).

    The bend's side is fixed by the names, so a road never jumps when the map is redrawn.
    """
    (x1, y1), (x2, y2) = a, b
    length = math.hypot(x2 - x1, y2 - y1) or 1.0
    side = 1 if int(hashlib.md5(seed.encode("utf-8")).hexdigest(), 16) % 2 else -1
    bend = side * min(40.0, length * 0.09)
    cx = (x1 + x2) / 2 - (y2 - y1) / length * bend
    cy = (y1 + y2) / 2 + (x2 - x1) / length * bend
    return f"M{x1:.1f} {y1:.1f} Q{cx:.1f} {cy:.1f} {x2:.1f} {y2:.1f}", (cx, cy)


def _overlap(a: tuple, b: tuple) -> float:
    """Shared area of two boxes (x1, y1, x2, y2)."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _layout(data: dict) -> dict:
    """Where each name goes: {place key: (x, y, anchor, box)}.

    A name sits under its icon unless that spot is taken by another icon or
    name; then above, right or left, whichever collides least.
    """
    icons, out, taken = {}, {}, []
    tf = _transform(data)
    party = _key(data.get("party", ""))
    for p in data["places"]:
        X, Y = _xy(p, tf)
        s = 1.25 if p["kind"] in ("city", "castle") else 1.0
        icons[_key(p["name"])] = (X - 27 * s, Y - 30 * s, X + 27 * s, Y + 16 * s)
    spots_by_key = {_key(p["name"]): _xy(p, tf) for p in data["places"]}
    on_roads = []                                 # points along every road: a name should not lie across one
    for r in data["roads"]:
        a, b = spots_by_key.get(_key(r["from"])), spots_by_key.get(_key(r["to"]))
        if not a or not b:
            continue
        _path, (cx, cy) = _curve(a, b, _key(r["from"]) + "|" + _key(r["to"]))
        for i in range(2, 19):
            t = i / 20
            on_roads.append(((1 - t) ** 2 * a[0] + 2 * t * (1 - t) * cx + t * t * b[0],
                             (1 - t) ** 2 * a[1] + 2 * t * (1 - t) * cy + t * t * b[1]))
    for p in data["places"]:
        k = _key(p["name"])
        X, Y = _xy(p, tf)
        big = p["kind"] in ("city", "castle")
        s, size = (1.25, 25) if big else (1.0, 21)
        text = p["name"] + ("" if p.get("status") == "visited" else " ?")
        w, h = len(text) * size * 0.5 + 8, size + 4
        drop = 18 * s if k == party else 0        # clear of the ring drawn around the party's place
        spots = [  # (text x, baseline y, anchor, box)
            (X, Y + 16 * s + size + 4 + drop, "middle",
             (X - w / 2, Y + 16 * s + 6 + drop, X + w / 2, Y + 16 * s + 6 + h + drop)),
            (X, Y - 30 * s - 9, "middle", (X - w / 2, Y - 30 * s - 9 - size, X + w / 2, Y - 30 * s - 5)),
            (X + 30 * s, Y + 4, "start", (X + 30 * s, Y + 4 - size, X + 30 * s + w, Y + 8)),
            (X - 30 * s, Y + 4, "end", (X - 30 * s - w, Y + 4 - size, X - 30 * s, Y + 8)),
        ]
        best, best_cost = spots[0], None
        for rank, spot in enumerate(spots):
            box = spot[3]
            cost = sum(_overlap(box, b) for kk, b in icons.items() if kk != k)
            cost += sum(_overlap(box, b) for b in taken)
            cost += 250 * sum(1 for x, y in on_roads if box[0] - 3 <= x <= box[2] + 3 and box[1] - 3 <= y <= box[3] + 3)
            cost += 40 * (max(0, 34 - box[0]) + max(0, box[2] - (W - 34)) + max(0, 100 - box[1]) + max(0, box[3] - (H - 34)))
            cost += rank * 0.5                    # all else equal: below, above, right, left
            if best_cost is None or cost < best_cost:
                best, best_cost = spot, cost
        out[k] = best
        taken.append(best[3])
    return out


def render(data: dict) -> str:
    """The whole map as one SVG document. Every text is escaped."""
    by_key = {_key(p["name"]): p for p in data["places"]}
    tf = _transform(data)
    labels = _layout(data)
    lost_at = _lost_spot(data, by_key, tf, labels)
    busy = [spot[3] for spot in labels.values()]
    for p in data["places"]:
        X, Y = _xy(p, tf)
        busy.append((X - 30, Y - 32, X + 30, Y + 20))
        if _key(p["name"]) == _key(data.get("party", "")) and not lost_at:       # the ring and the flag
            s = 1.25 if p["kind"] in ("city", "castle") else 1.0
            busy.append((X - 42 * s, Y - 54 * s, X + 58 * s, Y + 36 * s))
    font = "'IM Fell English', 'Palatino Linotype', Georgia, 'Times New Roman', serif"
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
         f'font-family="{font}">',
         '<defs>'
         '<radialGradient id="vg" cx="50%" cy="50%" r="72%"><stop offset="60%" stop-color="#000" stop-opacity="0"/>'
         '<stop offset="100%" stop-color="#5a3a14" stop-opacity=".38"/></radialGradient>'
         '<filter id="grain" x="0" y="0" width="100%" height="100%">'
         '<feTurbulence type="fractalNoise" baseFrequency=".035" numOctaves="3" seed="7"/>'
         '<feColorMatrix values="0 0 0 0 .42  0 0 0 0 .3  0 0 0 0 .14  0 0 0 .5 -.12"/></filter>'
         '</defs>',
         f'<rect width="{W}" height="{H}" fill="{C["bg"]}"/>',
         f'<rect width="{W}" height="{H}" filter="url(#grain)"/>',
         f'<rect width="{W}" height="{H}" fill="url(#vg)"/>',
         f'<rect x="18" y="18" width="{W - 36}" height="{H - 36}" fill="none" stroke="{C["ink"]}" stroke-width="3"/>',
         f'<rect x="27" y="27" width="{W - 54}" height="{H - 54}" fill="none" stroke="{C["ink"]}" stroke-width="1"/>']

    # Roads first, under everything. Each one's day count goes where the road is clear of names and icons.
    day_marks = []
    for r in data["roads"]:
        a, b = by_key.get(_key(r["from"])), by_key.get(_key(r["to"]))
        if not a or not b:
            continue
        (x1, y1), (x2, y2) = _xy(a, tf), _xy(b, tf)
        path, (cx, cy) = _curve((x1, y1), (x2, y2), _key(r["from"]) + "|" + _key(r["to"]))
        style = {"road": f'stroke="{C["road"]}" stroke-width="3.2"',
                 "trail": f'stroke="{C["road"]}" stroke-width="2.4" stroke-dasharray="9 7"',
                 "river": f'stroke="{C["water"]}" stroke-width="4"',
                 "sea": f'stroke="{C["water"]}" stroke-width="2.6" stroke-dasharray="2 8"'}[r.get("kind", "road")]
        o.append(f'<path d="{path}" fill="none" stroke-linecap="round" {style}/>')
        best, best_cost = None, None
        for t in (0.5, 0.42, 0.58, 0.34, 0.66, 0.27, 0.73):
            mx = (1 - t) ** 2 * x1 + 2 * t * (1 - t) * cx + t * t * x2
            my = (1 - t) ** 2 * y1 + 2 * t * (1 - t) * cy + t * t * y2
            box = (mx - 16, my - 16, mx + 16, my + 16)
            cost = sum(_overlap(box, other) for other in busy)
            if best_cost is None or cost < best_cost - 1e-9:
                best, best_cost = (mx, my), cost
            if cost == 0:
                break
        day_marks.append((best, fmt_dist(data, float(r["days"])) + ("h" if unit(data) == "hours" else "")))
        busy.append((best[0] - 16, best[1] - 16, best[0] + 16, best[1] + 16))

    party = _key(data.get("party", ""))
    for p in data["places"]:
        X, Y = _xy(p, tf)
        rumor = p.get("status") != "visited"
        scale = 1.25 if p["kind"] in ("city", "castle") else 1.0
        o.append(f'<g opacity="{0.55 if rumor else 1}" transform="translate({X:.1f} {Y:.1f}) scale({scale})" '
                 f'stroke="{C["ink"]}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round">'
                 f'{_icon(p["kind"])}</g>')
    for (mx, my), text in day_marks:
        o.append(f'<circle cx="{mx:.1f}" cy="{my:.1f}" r="{14 if len(text) < 4 else 17}" fill="{C["bg"]}" '
                 f'stroke="{C["ink"]}" stroke-width="1.4"/>'
                 f'<text x="{mx:.1f}" y="{my + (6 if len(text) < 3 else 5):.1f}" text-anchor="middle" '
                 f'font-size="{17 if len(text) < 3 else 13}" '
                 f'fill="{C["ink"]}">{escape(text)}</text>')
    for p in data["places"]:
        X, Y = _xy(p, tf)
        rumor = p.get("status") != "visited"
        big = p["kind"] in ("city", "castle")
        scale = 1.25 if big else 1.0
        tx, ty, anchor, _box = labels[_key(p["name"])]
        slant = 'font-style="italic" ' if rumor else ""
        o.append(f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="{anchor}" opacity="{0.6 if rumor else 1}" '
                 f'font-size="{25 if big else 21}" {slant}fill="{C["ink"]}" '
                 f'stroke="{C["bg"]}" stroke-width="5" paint-order="stroke" stroke-linejoin="round">'
                 f'{escape(p["name"])}{" ?" if rumor else ""}</text>')
        if _key(p["name"]) == party and not lost_at:
            o.append(f'<circle cx="{X:.1f}" cy="{Y - 6 * scale:.1f}" r="{38 * scale:.0f}" fill="none" stroke="{C["party"]}" '
                     f'stroke-width="3" stroke-dasharray="7 6"/>'
                     f'<g transform="translate({X + 30 * scale:.1f} {Y - 32 * scale:.1f})">'
                     f'<path d="M0 14 V-18" stroke="{C["ink"]}" stroke-width="2.6" stroke-linecap="round"/>'
                     f'<path d="M1 -18 L24 -11 L1 -4 Z" fill="{C["party"]}" stroke="{C["ink"]}" stroke-width="1.4" '
                     f'stroke-linejoin="round"/></g>')

    if lost_at:                              # lost (and they know it): a question mark off the roads
        lx, ly = lost_at
        o.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="40" fill="none" stroke="{C["party"]}" stroke-width="3" '
                 f'stroke-dasharray="3 7"/>'
                 f'<text x="{lx:.1f}" y="{ly + 17:.1f}" text-anchor="middle" font-size="52" fill="{C["party"]}" '
                 f'stroke="{C["bg"]}" stroke-width="5" paint-order="stroke">?</text>')

    title = escape(data.get("title", "") or "")
    if title:
        o.append(f'<text x="{W / 2}" y="78" text-anchor="middle" font-size="40" letter-spacing="3" fill="{C["ink"]}" '
                 f'stroke="{C["bg"]}" stroke-width="6" paint-order="stroke">{title}</text>'
                 f'<path d="M{W / 2 - 150} 94 H{W / 2 + 150}" stroke="{C["ink"]}" stroke-width="1.6"/>'
                 f'<path d="M{W / 2 - 6} 94 l6 -5 l6 5 l-6 5 Z" fill="{C["ink"]}"/>')
    legend = escape(data.get("legend", "") or "")
    if legend:
        o.append(f'<text x="52" y="{H - 48}" font-size="19" font-style="italic" fill="{C["ink"]}">{legend}</text>')
    cx, cy = W - 96, H - 104                      # compass rose
    o.append(f'<g transform="translate({cx} {cy})" stroke="{C["ink"]}" stroke-width="1.5" stroke-linejoin="round">'
             f'<circle r="34" fill="none"/><circle r="27" fill="none" stroke-width=".8"/>'
             f'<path d="M0 -44 L7 0 L0 44 L-7 0 Z" fill="{C["bg"]}"/><path d="M0 -44 L7 0 L0 0 Z" fill="{C["ink"]}"/>'
             f'<path d="M-44 0 L0 -7 L44 0 L0 7 Z" fill="{C["bg"]}"/><path d="M44 0 L0 7 L0 0 Z" fill="{C["ink"]}"/>'
             f'<text y="-50" text-anchor="middle" font-size="22" stroke="none" fill="{C["ink"]}">N</text></g>')
    o.append("</svg>")
    return "".join(o)


def _lost_spot(data: dict, by_key: dict, tf: tuple, labels: dict):
    """Where a party that knows it is lost is drawn: a little way from the place it left, on clear paper.

    Of a ring of spots around that place, the one that covers the fewest names, icons and roads."""
    lost = data.get("lost") or {}
    if not lost.get("known"):
        return None
    near = by_key.get(_key(lost.get("near") or lost.get("from") or data.get("party", "")))
    X, Y = _xy(near, tf) if near else (W / 2, H / 2)
    busy = [spot[3] for spot in labels.values()]
    for p in data["places"]:
        px, py = _xy(p, tf)
        busy.append((px - 30, py - 32, px + 30, py + 20))
    for r in data["roads"]:                  # a road is a row of small boxes along its curve
        a, b = by_key.get(_key(r["from"])), by_key.get(_key(r["to"]))
        if a and b:
            (x1, y1), (x2, y2) = _xy(a, tf), _xy(b, tf)
            _path, (cx, cy) = _curve((x1, y1), (x2, y2), _key(r["from"]) + "|" + _key(r["to"]))
            for i in range(1, 12):
                t = i / 12
                mx = (1 - t) ** 2 * x1 + 2 * t * (1 - t) * cx + t * t * x2
                my = (1 - t) ** 2 * y1 + 2 * t * (1 - t) * cy + t * t * y2
                busy.append((mx - 18, my - 18, mx + 18, my + 18))
    best, best_cost = None, None
    for radius in (130, 170, 210):
        for step in range(16):
            angle = step * math.pi / 8
            x, y = X + radius * math.cos(angle), Y + radius * math.sin(angle)
            if not (PAD_X + 45 <= x <= W - PAD_X - 45 and PAD_TOP + 45 <= y <= H - PAD_BOTTOM - 45):
                continue
            box = (x - 45, y - 45, x + 45, y + 45)
            cost = sum(_overlap(box, other) for other in busy)
            if best_cost is None or cost < best_cost - 1e-9:
                best, best_cost = (x, y), cost
        if best_cost == 0:
            break
    return best or (min(max(X, PAD_X + 45), W - PAD_X - 45), min(max(Y + 130, PAD_TOP + 45), H - PAD_BOTTOM - 45))


def write_image(data: dict, campaign: str = "") -> str:
    """Render to <campaign>/media/region-<hash>.svg and return the file name."""
    import media
    d = media.media_dir(campaign or None)
    svg = render(data)
    name = "region-" + hashlib.sha256(svg.encode("utf-8")).hexdigest()[:10] + ".svg"
    with open(d / name, "w", encoding="utf-8") as f:
        f.write(svg)
    old = sorted((p for p in d.glob("region-*.svg") if p.name != name), key=lambda p: p.stat().st_mtime)
    for p in old[:-KEEP_FILES]:
        try:
            p.unlink()
        except OSError:
            pass
    return name


def _at_pace(data: dict, days: float, campaign: str = "") -> str:
    """The same road at the pace the party keeps (travel.py): a normal day is 36 km, fast 48, slow 24 (PHB p. 182)."""
    try:
        import travel
        t = travel.load(travel.data_path(campaign))
    except Exception:
        return ""
    if not t.get("on") or t.get("pace") == "normal":
        return ""
    factor = travel.PACES["normal"]["km_day"] / travel.PACES[t["pace"]]["km_day"]
    d = days * factor
    return (f" ({fmt_hours(d)} hours at the {t['pace']} pace)" if unit(data) == "hours"
            else f" ({fmt_days(d)} days at the {t['pace']} pace)")


def _aboard(data: dict, route: list, days: float, campaign: str = "") -> str:
    """The same road aboard the party's boat or ship (vehicles.py): its speed, the current and the wind counted.

    '' on foot, or aboard a cart (it goes at the animals' pace)."""
    try:
        import vehicles
        vd = vehicles.load(vehicles.data_path(campaign))
        wind = vehicles._wind_strong(vehicles._campaign(campaign))
        jr = vehicles.journey(vd, days, wind)
        v = vehicles.find(vd, vd["aboard"]) if jr else None
    except Exception:
        return ""
    if not jr or jr.get("drawn"):
        return f" (aboard {jr['vehicle']}, at the animals' pace)" if jr else ""
    if jr.get("stuck"):
        return f" — aboard {jr['vehicle']} it cannot go against this current"
    land = []
    if vehicles.KINDS[v["kind"]].get("water"):
        for a, b in zip(route, route[1:]):
            r = next((r for r in data["roads"] if {_key(r["from"]), _key(r["to"])} == {_key(a), _key(b)}), None)
            if r and r.get("kind") not in ("river", "sea"):
                land.append(f"{a}–{b}")
    hours = jr["hours"]
    took = (f"{hours:.0f}" if hours >= 10 else f"{hours:.1f}".rstrip("0").rstrip(".")) + " hours"
    if jr["hours_day"] < 24 and hours > jr["hours_day"]:
        took += f" ({fmt_days(round(jr['days'] * 2) / 2 or 0.5)} days at {jr['hours_day']} h a day)"
    out = f" — aboard {jr['vehicle']}: {took}" + "".join(f"; {w}" for w in jr.get("why") or [])
    if land:
        out += f" ⚠ by land, where the {v['kind']} does not go: {', '.join(land)}"
    import math
    when = (f"{max(1, round(hours))} hours" if jr["hours_day"] >= 24 or hours <= jr["hours_day"]
            else f"{math.ceil(jr['days'])} days")
    return out + f" [aboard: calendar.py advance {when}]"


def _announce(data: dict, story: bool) -> None:
    """Tell the display the map changed; `story` also puts it in the narration feed."""
    try:
        import media
        import send
        send._post(f"{send.BASE_URL}/region", b"{}", send._read_token())
        if story and data.get("file"):
            media.post_image(data["file"], caption=data.get("title", ""), kind="region", subject="region")
    except Exception:
        pass   # the display is off: the map is saved and will be there next time


# ─── Command line ─────────────────────────────────────────────────────────────

def main(argv: list | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    ap = argparse.ArgumentParser(description="The map of the region, drawn as the party discovers it.")
    ap.add_argument("--campaign", default="", help="campaign to work in (default: the active one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="name the region (keeps the places already on the map)")
    p.add_argument("title")
    p.add_argument("--legend", default=None, help="a line at the bottom, in the campaign's language")
    p.add_argument("--day-px", type=float, default=None,
                   help="how long one day of travel is on the 1200-px-wide drawing (default 100; "
                        "lower it for a region more than ten days across)")
    p = sub.add_parser("add", help="add a place the party has reached or heard of")
    p.add_argument("name")
    p.add_argument("--kind", default="landmark")
    p.add_argument("--at", default="", help="x,y on the 0–100 grid (x east, y south)")
    p.add_argument("--near", default="", help="a place already on the map")
    p.add_argument("--dir", default="", help="N NE E SE S SW W NW, from --near")
    p.add_argument("--days", default=None, help="days of travel from --near (default 1)")
    p.add_argument("--hours", default=None, help="hours of travel from --near, instead of --days")
    p.add_argument("--road", default="", help="what links it to --near: road (default), trail, river, sea")
    p.add_argument("--note", default="")
    p.add_argument("--rumor", action="store_true", help="heard of, not visited: drawn faint, with a question mark")
    p.add_argument("--show", action="store_true", help="also put the map in the story")
    p = sub.add_parser("road", help="link two places, or change the link they have")
    p.add_argument("a"); p.add_argument("b")
    p.add_argument("--days", default=None)
    p.add_argument("--hours", default=None, help="instead of --days")
    p.add_argument("--kind", default="road")
    p = sub.add_parser("here", help="the party is at this place now")
    p.add_argument("name")
    p.add_argument("--quiet", action="store_true", help="do not put the map in the story")
    p = sub.add_parser("travel", help="move the party there by the shortest known route")
    p.add_argument("name")
    p.add_argument("--quiet", action="store_true", help="do not put the map in the story")
    p = sub.add_parser("mark", help="a place is now visited, or only a rumor — the party does not move")
    p.add_argument("name"); p.add_argument("status", help="visited or rumor")
    p = sub.add_parser("scale", help="count this map in days or in hours")
    p.add_argument("unit", help="days or hours")
    p.add_argument("--hour-px", type=float, default=None,
                   help="how long one hour of travel is on the drawing (default 110)")
    p.add_argument("--keep", action="store_true", help="leave the places where they are")
    sub.add_parser("relayout", help="set every place at the distance its road says")
    p = sub.add_parser("note", help="set what the party knows about a place")
    p.add_argument("name"); p.add_argument("text")
    p = sub.add_parser("remove", help="take a place (and its roads) off the map")
    p.add_argument("name")
    sub.add_parser("list", help="print the places and the roads")
    sub.add_parser("show", help="put the map in the story now")
    args = ap.parse_args(argv)

    try:
        path = data_path(args.campaign)
        data = load(path)
        story, message = False, ""

        if args.cmd == "list":
            print(f'{data["title"] or "(untitled region)"} — party at: {data["party"] or "unknown"} — counted in {unit(data)}')
            for pl in data["places"]:
                print(f'  {pl["name"]} [{pl["kind"]}, {pl["status"]}] at {pl["x"]},{pl["y"]}'
                      + (f' — {pl["note"]}' if pl.get("note") else ""))
            for r in data["roads"]:
                print(f'  {r["from"]} ↔ {r["to"]}: {fmt_dist(data, float(r["days"]))} {unit(data)} ({r["kind"]})')
            return 0
        if args.cmd == "init":
            data["title"] = " ".join(args.title.split())[:60]
            if args.legend is not None:
                data["legend"] = " ".join(args.legend.split())[:120]
            if args.day_px is not None:
                if not 30 <= args.day_px <= 300:
                    raise RegionError("--day-px must be between 30 and 300")
                data["day_px"] = args.day_px
            message = f'region "{data["title"]}" ready'
        elif args.cmd == "add":
            pl = add_place(data, args.name, args.kind, args.at, args.near, args.dir, args.days,
                           args.road, args.note, args.rumor, args.hours)
            story = args.show
            message = f'{pl["name"]} added at {pl["x"]},{pl["y"]} ({pl["kind"]}, {pl["status"]})'
        elif args.cmd == "road":
            r = add_road(data, args.a, args.b, args.days, args.kind, args.hours)
            message = f'{r["from"]} ↔ {r["to"]}: {fmt_dist(data, r["days"])} {unit(data)} ({r["kind"]})'
        elif args.cmd == "here":
            pl = set_here(data, args.name)
            story, message = not args.quiet, f'the party is at {pl["name"]}'
        elif args.cmd == "travel":
            days, route = travel(data, args.name)
            story = not args.quiet
            hours = max(1, int(round(days * HOURS_PER_DAY)))
            took = (f'{fmt_hours(days)} hours of travel', f'{hours} hours') if unit(data) == "hours" else \
                   (f'{fmt_days(days)} days of travel', f'{fmt_days(days).replace("½", ".5")} days')
            boat = _aboard(data, route, days, args.campaign)
            message = (" → ".join(route) + f': {took[0]}' + (" on foot" if boat.startswith(" — aboard") else "")
                       + f'{_at_pace(data, days, args.campaign)}{boat}. '
                       f'The calendar has NOT moved — when the journey is played: '
                       f'calendar.py -c <campaign> advance {took[1]}' + (" (on foot)" if boat.startswith(" — aboard") else ""))
        elif args.cmd == "mark":
            pl = mark(data, args.name, args.status)
            message = f'{pl["name"]}: {"visited" if pl["status"] == "visited" else "a rumor"}'
        elif args.cmd == "scale":
            moved = set_scale(data, args.unit, args.hour_px, args.keep)
            message = f'the map is counted in {unit(data)}; {moved} place(s) set out again'
        elif args.cmd == "relayout":
            message = f'{relayout(data)} place(s) set out again'
        elif args.cmd == "note":
            pl = find_place(data, args.name)
            pl["note"] = " ".join(args.text.split())[:300]
            message = f'{pl["name"]}: note updated'
        elif args.cmd == "remove":
            message = f"{remove_place(data, args.name)} removed"
        elif args.cmd == "show":
            if not data["places"]:
                raise RegionError("the map is empty — add a place first")
            story, message = True, "map shown"

        data["file"] = write_image(data, args.campaign)
        save(path, data)
        _announce(data, story)
        print(message)
    except RegionError as e:
        print(f"region_map: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    except RuntimeError as e:          # media.media_dir: no active campaign
        print(f"region_map: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
