"""
sheet_print.py — a character's .md sheet as a printable A4 page, and as a PDF.

The display serves both: /character/<name>/print (the page, with a print
button) and /character/<name>/pdf (the same page printed by a headless
Chrome/Edge/Chromium found on this machine). The page is built here from the
sheet file plus the live stats the DM pushed (current HP, slots, conditions),
so a player downloads the sheet as it stands at the table right now.

Labels are translated with the display's own i18n strings: the md.<English>
keys the full-sheet view already uses, plus print.* keys for this page.
No third-party packages — the markdown here is the small subset the sheet
template uses (headings, bullets, bold/italic, tables).
"""

import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

ABILITIES = ["str", "dex", "con", "int", "wis", "cha"]

# md.* keys that are templates, not phrases to find in the sheet text.
_MD_TEMPLATE_KEYS = {"racialHeading", "backgroundHeading", "feet", "decimal"}


# ── Strings ──────────────────────────────────────────────────────────────────

class Strings:
    """The display's translations for one language, English underneath."""

    def __init__(self, i18n_dir: str, lang: str):
        self.lang = "en"
        self.s = self._read(i18n_dir, "en")
        if lang and lang != "en":
            extra = self._read(i18n_dir, lang)
            if extra:
                self.s.update(extra)
                self.lang = lang

    @staticmethod
    def _read(i18n_dir: str, code: str) -> dict:
        if not re.fullmatch(r"[a-z]{2,3}(-[A-Za-z]{2,4})?", code or ""):
            return {}
        try:
            with open(os.path.join(i18n_dir, code + ".json"), encoding="utf-8") as f:
                data = json.load(f)
            return {k: v for k, v in data.items() if isinstance(v, str)}
        except (OSError, ValueError):
            return {}

    def t(self, key: str, fallback: str = "", **vars) -> str:
        text = self.s.get(key, fallback or key)
        for k, v in vars.items():
            text = text.replace("{" + k + "}", str(v))
        return text


def pick_language(i18n_dir: str, asked: str, accept_language: str) -> str:
    """The language asked for, else the best Accept-Language match, else en."""
    have = {fn[:-5] for fn in _listdir(i18n_dir) if fn.endswith(".json")}
    if asked in have:
        return asked
    for part in (accept_language or "").split(","):
        code = part.split(";")[0].strip().lower()
        for c in (code, code.split("-")[0]):
            if c in have:
                return c
    return "en"


def _listdir(path: str) -> list:
    try:
        return os.listdir(path)
    except OSError:
        return []


# ── Translating sheet text (mirrors _localizeSheetMd in index.html) ─────────

def localize(md: str, tr: Strings) -> str:
    md = re.sub(r"^### (.+?) Racial\s*$",
                lambda m: "### " + tr.t("md.racialHeading", m[1] + " Racial", name=m[1]),
                md, flags=re.M)
    md = re.sub(r"^### (.+?) Background\s*$",
                lambda m: "### " + tr.t("md.backgroundHeading", m[1] + " Background", name=m[1]),
                md, flags=re.M)
    md = re.sub(r"(\d)\*(?=\s*\|)", r"\1 ✱", md)      # "+5*" = proficient save
    md = md.replace("\\*", "✱")
    md = re.sub(r"^(\s*(?:-\s+)?\*\*.*)$",              # "A | B" label rows
                lambda m: re.sub(r"\s+\|\s+", "  ·  ", m[1]), md, flags=re.M)
    phrases = {}
    for key, text in tr.s.items():
        if key.startswith("md.") and not key.startswith("md.coin."):
            phrase = key[3:]
            if phrase not in _MD_TEMPLATE_KEYS and text != phrase:
                phrases[phrase] = text
    for ab in ABILITIES:
        t = tr.t("ab." + ab, ab.upper())
        if t != ab.upper():
            phrases[ab.upper()] = t
    if phrases:
        keys = sorted(phrases, key=len, reverse=True)
        rx = re.compile(r"(^|[^A-Za-z])(" + "|".join(re.escape(k) for k in keys) + r")(?![A-Za-z])")
        md = rx.sub(lambda m: m[1] + phrases[m[2]], md)
    md = re.sub(r"(\d+)\s*(pp|gp|ep|sp|cp)\b",
                lambda m: tr.t("md.coin." + m[2], m[0], n=m[1]), md)
    # "30 ft", "30/120 ft", "5×30 ft" — a metric note already after it is dropped.
    return _FEET_RX.sub(lambda m: _feet(m, tr), md)


_NUM = r"\d+(?:[.,]\d+)?"
_FEET_RX = re.compile(rf"({_NUM})(?:\s*([/×x])\s*({_NUM}))?\s*ft\b\.?"
                      rf"(\s*\({_NUM}(?:\s*[/×x]\s*{_NUM})?\s*m\))?")


def _feet(m, tr: Strings) -> str:
    dec = tr.t("md.decimal", ".")
    conv = lambda v: ("%g" % round(float(v.replace(",", ".")) * 0.3, 1)).replace(".", dec)  # noqa: E731
    if m[2]:
        ft, metres = f"{m[1]}{m[2]}{m[3]}", f"{conv(m[1])}{m[2]}{conv(m[3])}"
    else:
        ft, metres = m[1], conv(m[1])
    return tr.t("md.feet", m[0], ft=ft, m=metres)


# ── Markdown → HTML (the subset the sheet uses) ──────────────────────────────

def _inline(text: str) -> str:
    s = html.escape(text, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", s)
    return s


def _cells(line: str) -> list:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_rule(line: str) -> bool:
    return bool(re.fullmatch(r"\|?[\s:|-]+\|?", line.strip())) and "-" in line


def render_md(md: str) -> str:
    out, para, items = [], [], []
    lines = md.replace("\r\n", "\n").split("\n")

    def flush():
        if para:
            out.append("<p>" + " ".join(_inline(p) for p in para) + "</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(items) + "</ul>")
            items.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush()
        elif stripped.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i])
                i += 1
            out.append(_table(rows))
            continue
        elif re.match(r"#{1,6} ", stripped):
            flush()
            level = min(len(stripped) - len(stripped.lstrip("#")), 6)
            out.append(f"<h{level}>{_inline(stripped.lstrip('#').strip())}</h{level}>")
        elif re.match(r"[-*+] |\d+[.)] ", stripped):
            if para:
                flush()
            sub = " class=\"sub\"" if len(line) - len(line.lstrip()) >= 2 else ""
            items.append(f"<li{sub}>{_inline(re.sub(r'^([-*+]|\d+[.)]) ', '', stripped))}</li>")
        elif items and line.startswith("  "):
            items[-1] = items[-1][:-5] + " " + _inline(stripped) + "</li>"
        else:
            if items:
                flush()
            para.append(stripped)
        i += 1
    flush()
    return "\n".join(out)


def _table(rows: list) -> str:
    body = [r for r in rows if not _is_rule(r)]
    if not body:
        return ""
    head = _cells(body[0]) if len(rows) > 1 and _is_rule(rows[1]) else None
    data = body[1:] if head else body
    h = ""
    if head:
        h = "<thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead>"
    b = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in _cells(r)) + "</tr>"
                for r in data if any(c for c in _cells(r)))
    return f"<table>{h}<tbody>{b}</tbody></table>" if b else ""


# ── Reading the sheet ────────────────────────────────────────────────────────

def split_sections(md: str):
    """(name, {section title: body}) — "## Title" sections in file order."""
    md = md.replace("\r\n", "\n")
    m = re.search(r"^# (.+)$", md, re.M)
    name = m.group(1).strip() if m else ""
    sections = OrderedDict()
    for part in re.split(r"^(?=## )", md, flags=re.M):
        head = re.match(r"## (.+)\n?", part)
        if head:
            sections[head.group(1).strip()] = part[head.end():].strip("\n")
    return name, sections


def section(sections: dict, *starts: str) -> str:
    for title, body in sections.items():
        if any(title.startswith(s) for s in starts):
            return body
    return ""


def fields(line: str) -> list:
    """[(label, value)] from "- **A:** x | **B:** y"; a "| text" without a
    label belongs to the value before it ("Successes: 0 | Failures: 0")."""
    line = re.sub(r"^\s*[-*]\s+", "", line)
    out = []
    for part in re.split(r"\s+\|\s+", line):
        m = re.match(r"\*\*(.+?):\*\*\s*(.*)", part.strip())
        if m:
            out.append([m.group(1).strip(), m.group(2).strip()])
        elif out:
            out[-1][1] += " | " + part.strip()
    return [tuple(f) for f in out]


def table_rows(body: str) -> list:
    rows = [l for l in body.split("\n") if l.strip().startswith("|")]
    return [_cells(r) for r in rows[1:] if not _is_rule(r)] if len(rows) > 1 else []


def non_table(body: str) -> str:
    return "\n".join(l for l in body.split("\n") if not l.strip().startswith("|")).strip()


def _num(text, default=None):
    m = re.search(r"[+-]?\d+", str(text or ""))
    return int(m.group()) if m else default


def _signed(n) -> str:
    return "—" if n is None else (f"+{n}" if n >= 0 else str(n))


def _blank(value: str) -> bool:
    return value.strip() in ("", "—", "-", "–", "none", "None")


class Sheet:
    """What the page shows, read from the .md and topped up with live stats."""

    def __init__(self, md: str, live: Optional[dict]):
        live = live or {}
        self.name, self.sections = split_sections(md)
        self.name = live.get("name") or self.name
        head = md.replace("\r\n", "\n").split("\n## ", 1)[0]
        m = re.search(r"\*\*Player:\*\*\s*(.+?)(?=\s{2,}\*\*|$)", head, re.M)
        self.player = m.group(1).strip() if m else ""

        # Identity
        self.ident, self.ident_notes = {}, []
        for line in section(self.sections, "Identity").split("\n"):
            if not line.strip():
                continue
            fs = fields(line)
            known = {"Race", "Class", "Level", "Background", "Alignment", "XP", "Inspiration",
                     "Image look"}
            if fs and all(k in known for k, _ in fs):
                self.ident.update(dict(fs))
            else:
                self.ident_notes.append(line)
        self.level = live.get("level") or _num(self.ident.get("Level"), 1)
        xp = re.findall(r"\d+", self.ident.get("XP", ""))
        self.xp = (int(xp[0]), int(xp[1])) if len(xp) >= 2 else None
        if isinstance(live.get("xp"), dict) and live["xp"].get("current") is not None:
            self.xp = (live["xp"]["current"], live["xp"].get("next") or (self.xp or (0, 0))[1])
        insp = self.ident.get("Inspiration", "")
        self.inspiration = bool(re.search(r"✓|✔|\byes\b|\bsì\b", insp, re.I))
        if "inspiration" in live:
            self.inspiration = bool(live["inspiration"])

        # Ability scores
        self.scores = {}
        ab_body = section(self.sections, "Ability Scores")
        rows = table_rows(ab_body)
        if rows:
            for key, cell in zip(ABILITIES, rows[0]):
                m = re.match(r"\s*(\d+)\s*\(([+-]?\d+)\)", cell)
                if m:
                    self.scores[key] = (int(m.group(1)), int(m.group(2)))
                elif _num(cell) is not None:
                    self.scores[key] = (_num(cell), (_num(cell) - 10) // 2)
        for key, d in (live.get("ability_scores") or {}).items():
            if key in ABILITIES and isinstance(d, dict) and d.get("score") is not None:
                self.scores[key] = (int(d["score"]), _num(d.get("mod"), (int(d["score"]) - 10) // 2))
        self.ability_note = non_table(ab_body)

        # Saving throws
        sv_body = section(self.sections, "Saving Throws")
        self.saves = {}
        rows = table_rows(sv_body)
        if rows:
            for key, cell in zip(ABILITIES, rows[0]):
                self.saves[key] = (_num(cell), "*" in cell or "✱" in cell)
        m = re.search(r"proficiency bonus\s*\+?(\d+)", sv_body)
        self.prof = int(m.group(1)) if m else 2 + (max(1, self.level) - 1) // 4
        self.saves_note = "\n".join(l for l in non_table(sv_body).split("\n")
                                    if "proficient" not in l and l.strip())

        # Skills, then the tool/language lines under the table
        sk_body = section(self.sections, "Skills")
        self.skills = []
        for r in table_rows(sk_body):
            if len(r) >= 3 and r[0]:
                mark = r[3] if len(r) > 3 else ""
                self.skills.append({"name": r[0], "ability": r[1], "bonus": r[2],
                                    "prof": not _blank(mark),
                                    "expert": bool(re.search(r"expert|maestr|×2|x2", mark, re.I))})
        self.skills_note = non_table(sk_body)

        # Combat stats
        self.combat, self.combat_notes = {}, []
        known = {"HP", "Temp HP", "AC", "Initiative", "Speed", "Hit Dice", "Death Saves",
                 "Passive Perception"}
        for line in section(self.sections, "Combat Stats").split("\n"):
            if not line.strip():
                continue
            fs = fields(line)
            if fs and all(k in known for k, _ in fs):
                self.combat.update(dict(fs))
            else:
                self.combat_notes.append(line)
        hp = re.findall(r"\d+", self.combat.get("HP", ""))
        self.hp = [int(hp[0]), int(hp[1])] if len(hp) >= 2 else None
        self.temp_hp = _num(self.combat.get("Temp HP"), 0)
        if isinstance(live.get("hp"), dict):
            h = live["hp"]
            if h.get("max") is not None:
                self.hp = [h.get("current", h["max"]), h["max"]]
            if h.get("temp") is not None:
                self.temp_hp = h["temp"]
        ac = self.combat.get("AC", "")
        self.ac = _num(live.get("ac")) if live.get("ac") is not None else _num(ac)
        m = re.match(r"\s*\d+\s*\((.+)\)\s*$", ac)
        self.ac_note = m.group(1) if m else ""
        self.initiative = self.combat.get("Initiative") or str(live.get("initiative") or "")
        if not self.initiative and "dex" in self.scores:
            self.initiative = _signed(self.scores["dex"][1])
        self.speed = self.combat.get("Speed") or (f"{live['speed']} ft" if live.get("speed") else "")
        hd = self.combat.get("Hit Dice", "")
        m = re.match(r"\s*(\d*)d(\d+)", hd)
        self.hit_die = "d" + m.group(2) if m else ""
        self.hit_max = int(m.group(1)) if m and m.group(1) else self.level
        self.hit_left = _num(re.search(r"remaining:?\s*(\d+)", hd).group(1)) \
            if re.search(r"remaining:?\s*(\d+)", hd) else self.hit_max
        lhd = live.get("hit_dice")
        if isinstance(lhd, dict):
            self.hit_die = lhd.get("die") or self.hit_die
            self.hit_max = lhd.get("max", self.hit_max)
            self.hit_left = lhd.get("remaining", self.hit_left)
        ds = self.combat.get("Death Saves", "")
        self.death = (_num(re.search(r"Successes:?\s*(\d+)", ds).group(1)) if "Successes" in ds else 0,
                      _num(re.search(r"Failures:?\s*(\d+)", ds).group(1)) if "Failures" in ds else 0)
        self.passive = _num(self.combat.get("Passive Perception"))
        if self.passive is None:
            m = re.search(r"(?:Passive Perception|Percezione passiva)\s*:?\s*(\d+)", md, re.I)
            self.passive = int(m.group(1)) if m else None
        if self.passive is None:
            perc = next((s for s in self.skills if s["name"].split(" (")[0] == "Perception"), None)
            if perc and _num(perc["bonus"]) is not None:
                self.passive = 10 + _num(perc["bonus"])
        self.conditions = [c for c in (live.get("conditions") or []) if c]
        self.concentration = live.get("concentration") or ""

        # Attacks
        at_body = section(self.sections, "Attacks")
        self.attacks = [r for r in table_rows(at_body) if r and not _blank(r[0])]
        self.attacks_note = non_table(at_body)
        if not self.attacks:
            for a in (live.get("sheet") or {}).get("attacks") or []:
                self.attacks.append([a.get("name", ""), a.get("bonus", ""), a.get("damage", ""),
                                     a.get("type", ""), a.get("notes", "")])

        # Spell slots: {level: [max, used]}
        sl_body = section(self.sections, "Spell Slots")
        self.slots = OrderedDict()
        for r in table_rows(sl_body):
            lvl, total = _num(r[0]), _num(r[1] if len(r) > 1 else "")
            if lvl and total:
                self.slots[lvl] = [total, _num(r[2] if len(r) > 2 else "", 0) or 0]
        for lvl, s in sorted((live.get("spell_slots") or {}).items(), key=lambda kv: int(kv[0])):
            if isinstance(s, dict) and s.get("max"):
                self.slots[int(lvl)] = [int(s["max"]), int(s.get("used") or 0)]
        self.slots_note = non_table(sl_body)
        self.spells = section(self.sections, "Known Spells")
        if not self.spells.strip() and (live.get("sheet") or {}).get("spells"):
            sp = live["sheet"]["spells"]
            parts = []
            for key, label in (("cantrips", "Cantrips"), ("level1", "1st"), ("prepared", "Prepared")):
                if sp.get(key):
                    parts.append(f"- **{label}:** " + ", ".join(sp[key]))
            self.spells = "\n".join(parts)

    def other_sections(self):
        """Sections without a fixed place on the page, in file order."""
        placed = ("Identity", "Campaign History", "Ability Scores", "Saving Throws", "Skills",
                  "Combat Stats", "Attacks", "Spell Slots", "Known Spells")
        return [(t, b) for t, b in self.sections.items()
                if not any(t.startswith(p) for p in placed) and b.strip()]


# ── The page ─────────────────────────────────────────────────────────────────

_CSS = r"""
@page { size: A4; margin: 11mm 11mm 12mm; }
:root {
  --ink: #2a2119; --muted: #6d5d49; --faint: #9a8a72;
  --line: #c9b48a; --line-soft: #e6dac2; --accent: #7b2318; --gold: #8a6420;
  --paper: #fffdf8; --box: #fbf6ea;
}
* { box-sizing: border-box; }
html { background: #e9e2d3; }
body { margin: 0; color: var(--ink); font: 10pt/1.38 'Alegreya', Georgia, 'Times New Roman', serif;
       -webkit-print-color-adjust: exact; print-color-adjust: exact; }
.toolbar { position: sticky; top: 0; z-index: 5; display: flex; gap: 10px; justify-content: center;
           flex-wrap: wrap; padding: 10px 16px; background: #2a2119; }
.toolbar a, .toolbar button { font: 600 15px/1 'Alegreya Sans', system-ui, sans-serif; color: #2a2119;
           background: #e8c879; border: 0; border-radius: 8px; padding: 10px 16px; cursor: pointer;
           text-decoration: none; }
.toolbar button.secondary { background: #4a3d2e; color: #f1e6cf; }
.page { width: 210mm; max-width: 100%; margin: 16px auto; padding: 11mm; background: var(--paper);
        box-shadow: 0 2px 18px rgba(60,40,10,.25); }
h1, h2, h3, .label, .cap { font-family: 'Cinzel', 'Alegreya SC', Georgia, serif; }

/* Header */
.head { display: grid; grid-template-columns: 1fr auto; gap: 6mm; align-items: end;
        border-bottom: 2px solid var(--accent); padding-bottom: 3mm; margin-bottom: 4mm; }
.head h1 { margin: 0; font-size: 25pt; font-weight: 700; letter-spacing: .02em; line-height: 1.05; }
.head .sub { margin-top: 1.5mm; font-size: 11.5pt; color: var(--muted); }
.meta { display: grid; grid-template-columns: auto auto; gap: .6mm 4mm; margin: 0; font-size: 9pt; }
.meta dt { color: var(--faint); text-transform: uppercase; letter-spacing: .08em; font-size: 7pt;
           font-family: 'Cinzel', Georgia, serif; align-self: center; text-align: right; }
.meta dd { margin: 0; font-weight: 700; }
.notes-line { font-size: 9pt; color: var(--muted); margin: -2mm 0 3mm; }
.notes-line p, .notes-line ul { margin: 0; }
.notes-line ul { padding: 0; list-style: none; }

/* Ability scores */
.abilities { display: grid; grid-template-columns: repeat(6, 1fr); gap: 2.5mm; margin-bottom: 3mm; }
.ab { border: 1.3px solid var(--line); border-radius: 3mm; background: var(--box); text-align: center;
      padding: 1.6mm 1mm 1.8mm; }
.ab .label { font-size: 7.5pt; letter-spacing: .12em; color: var(--muted); }
.ab .mod { font: 700 21pt/1.05 'Cinzel', Georgia, serif; margin: .6mm 0; }
.ab .score { display: inline-block; min-width: 10mm; border: 1px solid var(--line); border-radius: 99px;
             background: var(--paper); font-size: 10pt; font-weight: 700; padding: 0 2mm; }
.ab .save { margin-top: 1.4mm; padding-top: 1.2mm; border-top: 1px dashed var(--line);
            font-size: 8.5pt; color: var(--muted); }
.ab .save b { color: var(--ink); }
.dot { display: inline-block; width: 2.3mm; height: 2.3mm; border-radius: 50%; border: 1px solid var(--gold);
       vertical-align: -.2mm; margin-right: 1mm; }
.dot.on { background: var(--gold); }
.dot.expert { background: var(--accent); border-color: var(--accent); }

/* Vitals */
.vitals { display: grid; grid-template-columns: repeat(5, 1fr) 2.5fr; gap: 2.5mm; margin-bottom: 4mm; }
.stat { border: 1.3px solid var(--line); border-radius: 3mm; background: var(--box); text-align: center;
        padding: 1.6mm 1mm; display: flex; flex-direction: column; justify-content: center; }
.stat .label { font-size: 6.8pt; letter-spacing: .1em; color: var(--muted); text-transform: uppercase; }
.stat .big { font: 700 17pt/1.1 'Cinzel', Georgia, serif; }
.stat .small { font-size: 7.5pt; color: var(--faint); line-height: 1.2; }
.stat.ac { border-radius: 3mm 3mm 42% 42% / 3mm 3mm 16% 16%; border-color: var(--accent); }
.hp { display: grid; grid-template-columns: auto 1fr; gap: 4mm; text-align: left; padding: 1.6mm 3mm;
      justify-content: start; }
.hp > div + div { border-left: 1px dashed var(--line); padding-left: 3mm; }
.hp .big { font-size: 19pt; }
.hp .row { font-size: 8.5pt; color: var(--muted); }
.hp .row b { color: var(--ink); }
.boxes { display: inline-flex; gap: .9mm; vertical-align: -.3mm; }
.boxes i { width: 2.6mm; height: 2.6mm; border: 1px solid var(--gold); border-radius: 50%; display: inline-block; }
.boxes i.on { background: var(--gold); }
.boxes.fail i.on { background: var(--accent); border-color: var(--accent); }
.cond { margin: -2mm 0 3mm; font-size: 9pt; }
.cond b { color: var(--accent); }

/* Columns */
.cols { display: grid; grid-template-columns: 58mm 1fr; gap: 4mm; align-items: start; }
.panel { border: 1.3px solid var(--line); border-radius: 3mm; padding: 2.2mm 3mm 2.6mm; margin-bottom: 3.5mm;
         background: var(--paper); break-inside: avoid; }
.panel.flow { break-inside: auto; }
h2 { margin: 0 0 1.8mm; font-size: 9.5pt; letter-spacing: .14em; text-transform: uppercase; color: var(--accent);
     border-bottom: 1px solid var(--line-soft); padding-bottom: .8mm; break-after: avoid; }
h3 { margin: 2.4mm 0 .8mm; font-size: 9pt; letter-spacing: .05em; color: var(--gold); break-after: avoid; }
h4, h5, h6 { margin: 2mm 0 .6mm; font-size: 9.5pt; }
.skills { list-style: none; margin: 0; padding: 0; font-size: 9pt; }
.skills li { display: grid; grid-template-columns: 3.5mm 8mm 1fr auto; align-items: baseline;
             padding: .45mm 0; border-bottom: 1px dotted var(--line-soft); }
.skills .bonus { font-weight: 700; text-align: right; padding-right: 1.5mm; }
.skills .ab-tag { font-size: 7pt; color: var(--faint); font-family: 'Cinzel', Georgia, serif; }
.passive { margin-top: 2mm; font-size: 9pt; }
.passive b { font-size: 11pt; }
table { width: 100%; border-collapse: collapse; font-size: 9pt; margin: .5mm 0 1.5mm; }
th { text-align: left; font: 600 7pt/1.2 'Cinzel', Georgia, serif; letter-spacing: .06em; color: var(--muted);
     text-transform: uppercase; border-bottom: 1px solid var(--line); padding: .8mm 1.2mm; }
td { padding: .9mm 1.2mm; border-bottom: 1px dotted var(--line-soft); vertical-align: top; }
td:first-child { font-weight: 700; }
.attacks td:nth-child(2), .attacks td:nth-child(3) { white-space: nowrap; }
.attacks td:last-child { color: var(--muted); font-size: 8.5pt; }
.slots { display: flex; flex-wrap: wrap; gap: 1.5mm 5mm; margin: .5mm 0 1.8mm; font-size: 9pt; }
.slots span { white-space: nowrap; }
.md p { margin: 0 0 1.4mm; }
.md ul { margin: 0 0 1.4mm; padding-left: 4mm; }
.md li { margin: 0 0 .7mm; }
.md li.sub { margin-left: 4mm; list-style: circle; }
.md em { color: var(--muted); }
.small-note { font-size: 8.5pt; color: var(--muted); }
.small-note p { margin: .6mm 0 0; }
.two-col { column-count: 2; column-gap: 6mm; column-rule: 1px solid var(--line-soft); }
.two-col h3 { break-inside: avoid; }
.two-col li { break-inside: avoid; }
.two-col .grp { break-inside: avoid; }
.two-col .grp > :first-child { margin-top: 0; }
.foot { margin-top: 3mm; text-align: right; font-size: 7.5pt; color: var(--faint); }

@media screen and (max-width: 760px) {
  html { background: var(--paper); }
  .page { margin: 0; padding: 14px 16px; box-shadow: none; width: auto; }
  .head { grid-template-columns: 1fr; }
  .meta { grid-template-columns: auto 1fr; }
  .meta dt { text-align: left; }
  .abilities { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .vitals { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .vitals .hp { grid-column: 1 / -1; }
  .cols { grid-template-columns: minmax(0, 1fr); }
  .cols > div, .stat, .ab { min-width: 0; }
  .panel { overflow-x: auto; }
  .head h1 { font-size: 26px; }
  .two-col { column-count: 1; }
}
@media print {
  html, body { background: #fff; }
  .toolbar { display: none !important; }
  .page { width: auto; margin: 0; padding: 0; box-shadow: none; background: #fff; }
}
"""

_FONTS = ("https://fonts.googleapis.com/css2?family=Alegreya:ital,wght@0,400;0,700;1,400"
          "&family=Alegreya+Sans:wght@600&family=Cinzel:wght@600;700&display=swap")


def render(md: str, live: Optional[dict], tr: Strings, *, pdf_href: str = "",
           for_pdf: bool = False, stamp: str = "") -> str:
    """The full HTML page for one sheet."""
    sh = Sheet(md, live)
    T = tr.t
    loc = lambda s: localize(s, tr)                       # noqa: E731
    mdh = lambda s: render_md(localize(s, tr))            # noqa: E731
    e = lambda s: html.escape(str(s), quote=False)        # noqa: E731

    def heading(title: str) -> str:
        return e(T("md." + title, title))

    # Header
    id_ = sh.ident
    sub_parts = [loc(id_.get("Race", "")), loc(id_.get("Class", "")) +
                 (f" {sh.level}" if id_.get("Class") and sh.level else ""), loc(id_.get("Background", ""))]
    sub = "  ·  ".join(e(p) for p in sub_parts if p and not _blank(p))
    meta = []
    meta.append((T("md.Level", "Level"), str(sh.level)))
    if id_.get("Alignment") and not _blank(id_["Alignment"]):
        meta.append((T("md.Alignment", "Alignment"), loc(id_["Alignment"])))
    if sh.xp:
        meta.append((T("md.XP", "XP"), f"{sh.xp[0]} / {sh.xp[1]}"))
    meta.append((T("md.Inspiration", "Inspiration"), "✓" if sh.inspiration else "—"))
    if sh.player and not _blank(sh.player):
        meta.append((T("print.player", "Player"), sh.player))
    meta_html = "".join(f"<dt>{e(k)}</dt><dd>{e(v)}</dd>" for k, v in meta)
    ident_notes = [l for l in sh.ident_notes
                   if not re.match(r"\s*-\s*\*\*(Image look|Active hooks):", l)]

    # Abilities with their saving throw underneath
    abil = []
    for key in ABILITIES:
        score, mod = sh.scores.get(key, (None, None))
        save, prof = sh.saves.get(key, (None, False))
        if save is None and mod is not None:
            save = mod
        abil.append(
            f'<div class="ab"><div class="label">{e(T("ab." + key, key.upper()))}</div>'
            f'<div class="mod">{_signed(mod)}</div>'
            f'<div class="score">{score if score is not None else "—"}</div>'
            f'<div class="save"><span class="dot{" on" if prof else ""}"></span>'
            f'{e(T("print.save", "Save"))} <b>{_signed(save)}</b></div></div>')

    # Vitals
    def stat(label, big, small="", cls=""):
        return (f'<div class="stat {cls}"><div class="label">{e(label)}</div>'
                f'<div class="big">{e(big)}</div>'
                + (f'<div class="small">{e(small)}</div>' if small else "") + "</div>")

    speed = loc(sh.speed) if sh.speed else "—"
    sp_big, _, sp_small = speed.partition(" (")
    hp_cur, hp_max = (sh.hp or ["—", "—"])
    vit = [
        stat(T("md.AC", "AC"), sh.ac if sh.ac is not None else "—", loc(sh.ac_note), "ac"),
        stat(T("md.Initiative", "Initiative"), sh.initiative or "—"),
        stat(T("md.Speed", "Speed"), sp_big, sp_small.rstrip(")")),
        stat(T("print.prof", "Proficiency"), _signed(sh.prof)),
        stat(T("md.Hit Dice", "Hit Dice"), f"{sh.hit_left}/{sh.hit_max}",
             f"{sh.hit_max}{sh.hit_die}" if sh.hit_die else ""),
        '<div class="stat hp">'
        f'<div><div class="label">{e(T("md.HP", "HP"))}</div>'
        f'<div class="big">{e(hp_cur)} <span style="font-size:11pt;color:var(--faint)">/ {e(hp_max)}</span></div>'
        f'<div class="row">{e(T("md.Temp HP", "Temp HP"))}: <b>{sh.temp_hp or 0}</b></div></div>'
        f'<div><div class="label">{e(T("md.Death Saves", "Death Saves"))}</div>'
        f'<div class="row">{e(T("md.Successes", "Successes"))} {_boxes(sh.death[0])}</div>'
        f'<div class="row">{e(T("md.Failures", "Failures"))} {_boxes(sh.death[1], "fail")}</div></div>'
        '</div>',
    ]
    cond = []
    if sh.conditions:
        cond.append(f'<b>{e(T("print.conditions", "Conditions"))}:</b> ' + e(", ".join(sh.conditions)))
    if sh.concentration:
        cond.append(f'<b>{e(T("print.concentration", "Concentrating on"))}:</b> ' + e(sh.concentration))

    # Left column: skills + proficiencies
    skills = []
    for s in sh.skills:
        name = loc(s["name"])
        m = re.match(r"(.+?) \((.+)\)$", name)
        if m and m.group(1).casefold() == m.group(2).casefold():
            name = m.group(1)
        dot = "expert" if s["expert"] else ("on" if s["prof"] else "")
        skills.append(f'<li><span class="dot {dot}"></span><span class="bonus">{e(s["bonus"])}</span>'
                      f'<span>{e(name)}</span><span class="ab-tag">{e(loc(s["ability"]))}</span></li>')
    left = []
    if skills:
        passive = (f'<div class="passive">{e(T("md.Passive Perception", "Passive Perception"))}: '
                   f'<b>{sh.passive}</b></div>' if sh.passive is not None else "")
        left.append(f'<section class="panel"><h2>{heading("Skills")}</h2>'
                    f'<ul class="skills">{"".join(skills)}</ul>{passive}</section>')
    extra = "\n\n".join(x for x in (sh.skills_note, sh.saves_note, sh.ability_note) if x.strip())
    extra = "\n".join(l for l in extra.split("\n")
                      if not re.search(r"(Passive Perception|Percezione passiva)\s*:?\s*\d+\.?\s*\*?$", l))
    if extra.strip():
        left.append(f'<section class="panel"><h2>{e(T("print.proficiencies", "Proficiencies & languages"))}'
                    f'</h2><div class="md small-note">{mdh(_paragraphs(extra))}</div></section>')

    # Right column: attacks, spells, combat notes
    right = []
    if sh.attacks:
        head = [T("md.Name", "Name"), T("md.Attack Bonus", "Attack Bonus"), T("md.Damage", "Damage"),
                T("md.Type", "Type"), T("md.Notes", "Notes")]
        rows = "".join("<tr>" + "".join(f"<td>{_inline(loc(c))}</td>" for c in (r + [""] * 5)[:5])
                       + "</tr>" for r in sh.attacks)
        note = f'<div class="md small-note">{mdh(sh.attacks_note)}</div>' if sh.attacks_note else ""
        right.append(f'<section class="panel"><h2>{heading("Attacks")}</h2><table class="attacks"><thead><tr>'
                     + "".join(f"<th>{e(h)}</th>" for h in head) + f"</tr></thead><tbody>{rows}</tbody></table>"
                     f"{note}</section>")
    if sh.slots or sh.spells.strip():
        slots = "".join(
            f'<span><b>{e(T("md." + _ordinal(l), _ordinal(l)))}</b> {_boxes(mx, "", used, mx)} '
            f'<span class="small-note">{mx - used}/{mx}</span></span>'
            for l, (mx, used) in sh.slots.items())
        right.append(
            f'<section class="panel flow"><h2>{e(T("print.spells", "Spellcasting"))}</h2>'
            + (f'<div class="slots">{slots}</div>' if slots else "")
            + (f'<div class="md small-note">{mdh(sh.slots_note)}</div>' if sh.slots_note else "")
            + f'<div class="md">{mdh(sh.spells)}</div></section>')
    if sh.combat_notes:
        right.append(f'<section class="panel"><h2>{heading("Combat Stats")}</h2>'
                     f'<div class="md">{mdh(chr(10).join(sh.combat_notes))}</div></section>')

    # Full-width sections
    rest = []
    for title, body in sh.other_sections():
        body = "\n".join(l for l in body.split("\n")
                         if not re.match(r"\s*-\s*\*\*(Image look|Active hooks|Origin campaign|"
                                         r"Previous campaigns):\*\*", l)
                         and not re.match(r"\s*\*\(Leave blank", l))
        if not body.strip() or re.fullmatch(r"[-\s|]*", body):
            continue
        wide = title.startswith(("Features", "Equipment"))
        rest.append(f'<section class="panel{" flow" if wide else ""}"><h2>{heading(title)}</h2>'
                    f'<div class="md{" two-col" if wide else ""}">'
                    f'{_groups(mdh(_paragraphs(body))) if wide else mdh(_paragraphs(body))}</div></section>')

    toolbar = ""
    if not for_pdf:
        dl = (f'<a href="{html.escape(pdf_href)}" download>⬇ {e(T("print.download", "Download PDF"))}</a>'
              if pdf_href else "")
        toolbar = (f'<div class="toolbar">{dl}<button type="button" class="{"secondary" if dl else ""}" '
                   f'onclick="window.print()">🖨 {e(T("print.print", "Print"))}</button></div>')

    title = T("print.filename", "Character sheet - {name}", name=sh.name)
    return f"""<!doctype html>
<html lang="{e(tr.lang)}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{_FONTS}">
<style>{_CSS}</style></head>
<body>{toolbar}
<main class="page">
<header class="head"><div><h1>{e(sh.name)}</h1><div class="sub">{sub}</div></div><dl class="meta">{meta_html}</dl></header>
{f'<div class="notes-line md">{mdh(chr(10).join(ident_notes))}</div>' if ident_notes else ""}
<div class="abilities">{"".join(abil)}</div>
<div class="vitals">{"".join(vit)}</div>
{f'<div class="cond">{"  ·  ".join(cond)}</div>' if cond else ""}
<div class="cols"><div>{"".join(left)}</div><div>{"".join(right)}</div></div>
{"".join(rest)}
<div class="foot">{e(stamp)}</div>
</main></body></html>"""


def _boxes(n, cls: str = "", filled: Optional[int] = None, total: int = 3) -> str:
    on = n if filled is None else filled
    count = total
    return (f'<span class="boxes {cls}">'
            + "".join(f'<i class="{"on" if k < (on or 0) else ""}"></i>' for k in range(count)) + "</span>")


def _groups(body: str) -> str:
    """Wrap each sub-heading with what follows it, so a column break never
    leaves "Weapons:" at the bottom of one column and its list in the next."""
    parts = re.split(r"(?=<h[3-6]>|<p><strong>[^<]*</strong></p>)", body)
    return "".join(f'<div class="grp">{p}</div>' for p in parts if p.strip())


def _ordinal(level: int) -> str:
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(level, f"{level}th")


def _paragraphs(md: str) -> str:
    """"**Languages:** …" lines become their own paragraphs, as in the display."""
    return "\n".join(("\n" + l) if re.match(r"\*\*[^*]+:\*\*", l) else l for l in md.split("\n"))


# ── PDF ──────────────────────────────────────────────────────────────────────

_browser_cache = []
_pdf_lock = threading.Lock()


def find_browser() -> Optional[str]:
    """A Chrome, Edge or Chromium that can print headless, or None."""
    if _browser_cache:
        return _browser_cache[0]
    found = os.environ.get("DND_PDF_BROWSER") or None
    if not found:
        candidates = []
        if sys.platform == "win32":
            for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                         os.environ.get("LOCALAPPDATA")):
                if base:
                    candidates += [os.path.join(base, r"Google\Chrome\Application\chrome.exe"),
                                   os.path.join(base, r"Microsoft\Edge\Application\msedge.exe"),
                                   os.path.join(base, r"Chromium\Application\chrome.exe")]
        elif sys.platform == "darwin":
            candidates += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                           "/Applications/Chromium.app/Contents/MacOS/Chromium",
                           "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                     "microsoft-edge", "msedge", "chrome"):
            w = shutil.which(name)
            if w:
                candidates.append(w)
        found = next((c for c in candidates if c and os.path.isfile(c)), None)
    if found:
        _browser_cache.append(found)
    return found


def to_pdf(page_html: str, timeout: int = 60) -> Optional[bytes]:
    """Print the page to PDF with a headless browser; None when that's not possible."""
    exe = find_browser()
    if not exe:
        return None
    with _pdf_lock, tempfile.TemporaryDirectory(prefix="dnd-sheet-", ignore_cleanup_errors=True) as tmp:
        src = Path(tmp) / "sheet.html"
        out = Path(tmp) / "sheet.pdf"
        src.write_text(page_html, encoding="utf-8")
        cmd = [exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
               "--disable-extensions", "--hide-scrollbars", f"--user-data-dir={Path(tmp) / 'profile'}",
               "--no-pdf-header-footer", f"--print-to-pdf={out}", src.as_uri()]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        deadline = time.monotonic() + timeout
        try:
            subprocess.run(cmd, timeout=timeout, capture_output=True, creationflags=flags)
        except (OSError, subprocess.SubprocessError):
            return None
        # On Windows chrome.exe can return before the PDF is on disk (the
        # work is done by a child process), so wait for a complete file.
        while time.monotonic() < deadline:
            try:
                data = out.read_bytes()
            except OSError:
                data = b""
            if data.startswith(b"%PDF") and data.rstrip().endswith(b"%%EOF"):
                return data
            time.sleep(0.2)
        return None


if __name__ == "__main__":   # python sheet_print.py sheet.md [lang] > out.html
    _here = os.path.dirname(os.path.abspath(__file__))
    _md = Path(sys.argv[1]).read_text(encoding="utf-8")
    sys.stdout.write(render(_md, None, Strings(os.path.join(_here, "i18n"),
                                               sys.argv[2] if len(sys.argv) > 2 else "en")))
