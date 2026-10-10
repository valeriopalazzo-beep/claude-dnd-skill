#!/usr/bin/env python3
"""encounter.py — encounters sized for the party that is actually at the table.

    encounter.py build --campaign corona-di-cenere --difficulty hard
    encounter.py build --campaign corona-di-cenere --difficulty hard --with hobgoblin --with goblin
    encounter.py build --level 3 --players 4 --difficulty medium --type undead
    encounter.py build --party 5,5,4,4 --difficulty deadly --type fiend,undead --options 5
    encounter.py rate  --campaign corona-di-cenere --monsters "goblin:4,hobgoblin"

The party is read from the campaign's sheets (PCs named in `absent:` are left
out), or given as `--party` levels, or as `--level` and `--players`.

`build --with NAME` (repeatable) is for when the fiction already says who is
there: the script finds how many of each make the difficulty asked for. With
no `--with` it proposes a few differently shaped groups — one creature, a
pair, a pack, a leader with followers — from the SRD, all of one creature
type per option; `--type` narrows it, `--exclude` keeps names out, `--max-cr`
caps a single creature (default: party level + 1).

`rate` says how hard a given group is. Both print the XP maths (5e encounter
budget: each PC's threshold summed, monster XP multiplied by their number and
adjusted for a small or a large party), the `--monsters` string for
`xp.py award` and the combatants for `combat.py init`.

Names are printed in the campaign's language (`language:` flag, or `--lang`);
Italian names are understood in `--with` and `--monsters`. `--seed` makes a
build repeatable.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import re
import sys

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import paths  # noqa: E402  (also forces UTF-8 output on Windows)
import xp as _xp  # noqa: E402

DIFFICULTIES = ("easy", "medium", "hard", "deadly")
# Multiplier steps; the number of monsters picks one, a party under 3 moves a
# step up and a party of 6 or more a step down.
_STEPS = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0)
_MAX_RANDOM = 8          # most monsters in a proposed group
_MAX_WITH = 12           # most of one kind when the DM names the creatures


class EncounterError(Exception):
    """A request that cannot be answered."""


# ─── The party ────────────────────────────────────────────────────────────────

_ABSENT_RE = re.compile(r"^[ \t]*(?:[-*][ \t]*)?absent:[ \t]*(.*)$", re.MULTILINE | re.IGNORECASE)
_LEVEL_RE = re.compile(r"\*\*(?:Level|等级):\*\*\s*(\d+)")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def campaign_party(campaign: str) -> list:
    """[(name, level)] of the PCs present, from the campaign's sheets."""
    root = paths.find_campaign(campaign)
    folder = root / "characters"
    if not folder.is_dir():
        raise EncounterError(f"no characters folder in campaign {campaign!r}")
    absent = set()
    try:
        m = _ABSENT_RE.search((root / "state.md").read_text(encoding="utf-8", errors="replace"))
        if m and m.group(1).strip().lower() not in ("", "none", "nessuno"):
            absent = {_slug(n) for n in m.group(1).split(",")}
    except OSError:
        pass
    party = []
    for path in sorted(folder.glob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        first = text.splitlines()[0] if text else ""
        name = first[2:].strip() if first.startswith("# ") else path.stem
        if _slug(name) in absent or _slug(path.stem) in absent:
            continue
        m = _LEVEL_RE.search(text)
        party.append((name, max(1, min(20, int(m.group(1)))) if m else 1))
    if not party:
        raise EncounterError(f"no PC is present in campaign {campaign!r}")
    return party


def thresholds(levels: list) -> tuple:
    """(easy, medium, hard, deadly) XP for the whole party."""
    return tuple(sum(_xp.XP_THRESHOLDS[lv][i] for lv in levels) for i in range(4))


def multiplier(monsters: int, party_size: int) -> float:
    step = 1 if monsters <= 1 else 2 if monsters == 2 else 3 if monsters <= 6 else 4 if monsters <= 10 \
        else 5 if monsters <= 14 else 6
    if party_size < 3:
        step += 1
    elif party_size >= 6:
        step -= 1
    return _STEPS[max(0, min(len(_STEPS) - 1, step))]


def classify(adjusted: int, th: tuple) -> str:
    label = "trivial"
    for name, floor in zip(DIFFICULTIES, th):
        if adjusted >= floor:
            label = name
    return label


def band(th: tuple, difficulty: str) -> tuple:
    """[low, high) of adjusted XP that counts as this difficulty."""
    i = DIFFICULTIES.index(difficulty)
    return th[i], (th[i + 1] if i < 3 else int(th[i] * 1.5) + 1)


# ─── Monsters ─────────────────────────────────────────────────────────────────

def cr_key(value) -> str:
    return _xp._normalise_cr(str(value))


def cr_number(value) -> float:
    key = cr_key(value)
    if "/" in key:
        a, b = key.split("/")
        return int(a) / int(b)
    return float(key)


def monster_xp(rec: dict) -> int:
    return _xp.CR_XP.get(cr_key(rec.get("cr", 0)), 0)


def local_name(rec: dict, lang: str) -> str:
    return ((rec.get("_i18n") or {}).get(lang) or {}).get("name") or rec.get("name", "")


def load_monsters(ruleset: str = "2014") -> list:
    import lookup
    return [m for m in lookup._get_records("monsters", ruleset) if cr_key(m.get("cr", 0)) in _xp.CR_XP]


def find_monster(query: str, pool: list) -> dict:
    import lookup
    found = lookup._find(query, pool, 3)
    if not found:
        raise EncounterError(f"no monster called {query!r} in the SRD")
    return found[0]


def _type_words(lang: str) -> dict:
    """Creature-type words of the table's language → the SRD's English type."""
    import lookup
    lookup._load()
    return {v.lower(): k.lower() for k, v in (lookup._vocab_by_lang.get(lang, {}).get("type") or {}).items()}


def _base_type(rec: dict) -> str:
    kind = str(rec.get("type", "")).lower()
    return "beast" if kind.startswith("swarm") else kind


# ─── Groups ───────────────────────────────────────────────────────────────────

def measure(group: list, levels: list) -> dict:
    """XP maths for a group: [(record, count)] against the party."""
    count = sum(n for _, n in group)
    raw = sum(monster_xp(r) * n for r, n in group)
    mult = multiplier(count, len(levels))
    th = thresholds(levels)
    adjusted = int(raw * mult)
    return {"group": group, "count": count, "raw": raw, "multiplier": mult, "adjusted": adjusted,
            "difficulty": classify(adjusted, th), "thresholds": th}


def fit_counts(records: list, levels: list, difficulty: str) -> dict:
    """How many of each named creature land on the difficulty (closest if none do)."""
    low, high = band(thresholds(levels), difficulty)
    aim = low + (high - low) * 0.35
    best, best_score = None, None
    counts = [1] * len(records)
    while True:
        m = measure(list(zip(records, counts)), levels)
        inside = low <= m["adjusted"] < high
        score = (0 if inside else 1, abs(m["adjusted"] - aim), m["count"])
        if best_score is None or score < best_score:
            best, best_score = m, score
        i = 0
        while i < len(counts):                       # odometer over 1.._MAX_WITH per kind
            counts[i] += 1
            if counts[i] <= _MAX_WITH:
                break
            counts[i] = 1
            i += 1
        if i == len(counts):
            break
    best["shape"] = "named"
    best["on_target"] = low <= best["adjusted"] < high
    return best


def _same_n(pool: list, n: int, levels: list, low: int, high: int, rng: random.Random):
    mult = multiplier(n, len(levels))
    fit = [r for r in pool if low <= int(monster_xp(r) * n * mult) < high]
    return [(rng.choice(fit), n)] if fit else None


def _leader_and_followers(pool: list, levels: list, low: int, high: int, rng: random.Random):
    leaders = pool[:]
    rng.shuffle(leaders)
    for leader in leaders[:60]:
        followers = [r for r in pool if _base_type(r) == _base_type(leader)
                     and monster_xp(r) * 2 <= monster_xp(leader) and r is not leader]
        rng.shuffle(followers)
        for follower in followers[:20]:
            ks = list(range(2, _MAX_RANDOM))
            rng.shuffle(ks)
            for k in ks:
                raw = monster_xp(leader) + monster_xp(follower) * k
                if low <= int(raw * multiplier(k + 1, len(levels))) < high:
                    return [(leader, 1), (follower, k)]
    return None


SHAPES = ("solo", "pair", "pack", "leader")


def propose(pool: list, levels: list, difficulty: str, rng: random.Random, options: int = 3) -> list:
    """Up to `options` groups on the difficulty, each a different shape or creature type."""
    low, high = band(thresholds(levels), difficulty)
    by_type: dict = {}
    for r in pool:
        by_type.setdefault(_base_type(r), []).append(r)
    kinds = sorted(by_type)
    out, used, shapes_used = [], set(), set()
    attempts = options * 40
    for attempt in range(attempts):
        if len(out) >= options or not kinds:
            break
        shape = SHAPES[attempt % len(SHAPES)]
        if shape in shapes_used and attempt < attempts // 2 and len(shapes_used) < len(SHAPES):
            continue                                 # a shape not shown yet comes first
        sub = by_type[rng.choice(kinds)]
        if shape == "solo":
            group = _same_n(sub, 1, levels, low, high, rng)
        elif shape == "pair":
            group = _same_n(sub, 2, levels, low, high, rng)
        elif shape == "pack":
            ns = list(range(3, _MAX_RANDOM + 1))
            rng.shuffle(ns)
            group = next((g for g in (_same_n(sub, n, levels, low, high, rng) for n in ns) if g), None)
        else:
            group = _leader_and_followers(sub, levels, low, high, rng)
        if not group:
            continue
        key = tuple(sorted(r.get("index", r.get("name")) for r, _ in group))
        if key in used or (shape, _base_type(group[0][0])) in used:
            continue
        used.update({key, (shape, _base_type(group[0][0]))})
        shapes_used.add(shape)
        m = measure(group, levels)
        m["shape"], m["on_target"] = shape, True
        out.append(m)
    return out


# ─── Printing ─────────────────────────────────────────────────────────────────

def _dex_mod(rec: dict) -> int:
    try:
        return (int(rec.get("dex", 10)) - 10) // 2
    except (TypeError, ValueError):
        return 0


def combatants(group: list, lang: str) -> list:
    out = []
    for rec, n in group:
        name = local_name(rec, lang)
        for i in range(1, n + 1):
            out.append({"name": f"{name} {i}" if n > 1 else name, "dex_mod": _dex_mod(rec),
                        "hp": rec.get("hp", 1), "ac": rec.get("ac", 10), "type": "npc"})
    return out


_SHAPE_WORDS = {"solo": "one creature", "pair": "a pair", "pack": "a pack", "leader": "a leader and followers",
                "named": "the creatures you named"}


def describe(m: dict, lang: str, title: str) -> str:
    th = m["thresholds"]
    lines = [f"{title} — {m['difficulty'].upper()} ({_SHAPE_WORDS.get(m.get('shape'), 'group')})"]
    for rec, n in m["group"]:
        name, en = local_name(rec, lang), rec.get("name", "")
        also = f" ({en})" if en and en != name else ""
        lines.append(f"  {n} × {name}{also} — CR {cr_key(rec.get('cr', 0))}, {monster_xp(rec):,} XP each · "
                     f"AC {rec.get('ac', '?')}, HP {rec.get('hp', '?')} ({rec.get('hp_dice', '')}), "
                     f"DEX {_dex_mod(rec):+d}")
    lines.append(f"  {m['raw']:,} XP × {m['multiplier']:g} ({m['count']} monster{'s' if m['count'] != 1 else ''}) "
                 f"= {m['adjusted']:,} adjusted · party: easy {th[0]:,} · medium {th[1]:,} · hard {th[2]:,} "
                 f"· deadly {th[3]:,}")
    monsters = ",".join(f"{rec.get('name', '')}:{cr_key(rec.get('cr', 0))}:{n}" for rec, n in m["group"])
    lines.append(f'  xp.py award:    --monsters "{monsters}"')
    lines.append("  combat.py init: " + json.dumps(combatants(m["group"], lang), ensure_ascii=False))
    return "\n".join(lines)


# ─── Command line ─────────────────────────────────────────────────────────────

def _party_levels(args) -> tuple:
    """(levels, description) from --party, --level/--players or the campaign."""
    if args.party:
        try:
            levels = [int(x) for x in args.party.replace(" ", "").split(",") if x]
        except ValueError:
            raise EncounterError(f"--party wants levels like 3,3,2 — not {args.party!r}")
        note = f"party of {len(levels)}"
    elif args.level:
        levels = [args.level] * max(1, args.players or 4)
        note = f"{len(levels)} PCs of level {args.level}"
    elif args.campaign:
        party = campaign_party(args.campaign)
        levels = [lv for _, lv in party]
        note = ", ".join(f"{name} {lv}" for name, lv in party)
    else:
        raise EncounterError("which party? pass --campaign, --party 3,3,2 or --level with --players")
    if not levels or any(not 1 <= lv <= 20 for lv in levels):
        raise EncounterError("levels go from 1 to 20")
    return levels, note


def _parse_group(text: str, pool: list) -> list:
    group = []
    for entry in (text or "").split(","):
        parts = [p.strip() for p in entry.split(":") if p.strip()]
        if not parts:
            continue
        count = int(parts[-1]) if len(parts) > 1 and parts[-1].isdigit() else 1
        group.append((find_monster(parts[0], pool), max(1, count)))
    if not group:
        raise EncounterError('--monsters wants "goblin:4,hobgoblin"')
    return group


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="Encounters sized for the party at the table.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd in ("build", "rate"):
        p = sub.add_parser(cmd)
        p.add_argument("--campaign", "-c", default="", help="read the party (and the language) from this campaign")
        p.add_argument("--party", default="", help="levels of the PCs present: 3,3,2")
        p.add_argument("--level", type=int, default=0)
        p.add_argument("--players", type=int, default=0)
        p.add_argument("--lang", default="", help="language of the names (default: the campaign's)")
        p.add_argument("--ruleset", default="", help="2014 or 2024 (default: the campaign's)")
        if cmd == "build":
            p.add_argument("--difficulty", "-d", default="medium", choices=DIFFICULTIES)
            p.add_argument("--with", dest="named", action="append", default=[], metavar="MONSTER",
                           help="a creature the fiction puts there (repeatable); the script finds how many")
            p.add_argument("--type", default="", help="creature types to draw from: undead,fiend")
            p.add_argument("--exclude", default="", help="names to leave out: dragon,lich")
            p.add_argument("--max-cr", default="", help="highest CR for one creature (default: party level + 1)")
            p.add_argument("--options", type=int, default=3)
            p.add_argument("--seed", default=None)
        else:
            p.add_argument("--monsters", required=True, help='"goblin:4,hobgoblin"')
    args = ap.parse_args(argv)

    try:
        levels, note = _party_levels(args)
        lang = args.lang or paths.campaign_language(args.campaign)
        ruleset = args.ruleset or (paths.campaign_ruleset(args.campaign) if args.campaign else "2014")
        pool = load_monsters(ruleset)
        print(f"Party: {note}")
        if args.cmd == "rate":
            print(describe(dict(measure(_parse_group(args.monsters, pool), levels), shape="named"), lang, "Encounter"))
            return 0

        if args.named:
            m = fit_counts([find_monster(q, pool) for q in args.named], levels, args.difficulty)
            print(describe(m, lang, "Encounter"))
            if not m["on_target"]:
                print(f"  ⚠ these creatures cannot make a {args.difficulty} encounter for this party; "
                      f"this is the closest ({m['difficulty']}).")
            return 0

        rng = random.Random(args.seed) if args.seed is not None else random.Random()
        avg = sum(levels) / len(levels)
        cap = cr_number(args.max_cr) if args.max_cr else avg + 1
        words = _type_words(lang)
        wanted = {words.get(t, t) for t in (x.strip().lower() for x in args.type.split(",")) if t}
        known = {_base_type(r) for r in pool}
        if wanted - known:
            raise EncounterError(f"unknown creature type {sorted(wanted - known)}; there are: {', '.join(sorted(known))}")
        banned = [_slug(x) for x in args.exclude.split(",") if x.strip()]
        usable = [r for r in pool
                  if cr_number(r.get("cr", 0)) <= cap and monster_xp(r) > 10
                  and (not wanted or _base_type(r) in wanted)
                  and not any(b and b in _slug(r.get("name", "")) for b in banned)]
        found = propose(usable, levels, args.difficulty, rng, max(1, args.options))
        if not found:
            raise EncounterError(f"nothing fits a {args.difficulty} encounter with these limits; "
                                 "widen --type, raise --max-cr or name the creatures with --with")
        for i, m in enumerate(found, 1):
            print(describe(m, lang, f"Option {i}"))
    except (EncounterError, ValueError) as e:
        print(f"encounter: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
