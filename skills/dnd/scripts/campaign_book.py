#!/usr/bin/env python3
"""campaign_book.py — the chronicle of a campaign as a book, in HTML and PDF.

    campaign_book.py --campaign corona-di-cenere
    campaign_book.py --campaign corona-di-cenere --title "La Corona di Cenere" --sessions 1-4
    campaign_book.py --campaign corona-di-cenere --loot --portraits --no-pdf --out ~/Desktop

It reads what the campaign already holds and lays it out to be read, printed
or sent to the players:

  * a cover, the party (from the character sheets), the map of the region;
  * one chapter per session from session-log.md and session-log-archive.md:
    the recap, the key events, the decisions, the closing lines — with the
    pictures of the places reached and the battle maps of that evening;
  * the documents the party found (handouts), as an appendix.

Never printed: DM Calibration, Open Threads, XP tables, DM notes, anything
from state.md or world.md. `--loot` adds each session's Loot & Changes;
`--portraits` adds the characters' portraits when the campaign has them.

The book is written to <campaign>/book/<campaign>.html and .pdf (`--out DIR`
for somewhere else). From there the display offers the PDF to every player
(a "Campaign book" entry in the menus). The PDF needs Chrome, Edge or Chromium, the same as the
character sheet's; without one the HTML is still written and can be printed
from any browser. Headings are in the campaign's language (`language:` flag,
or --lang).
"""
from __future__ import annotations

import argparse
import datetime
import html
import json
import os
import pathlib
import re
import sys

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import paths  # noqa: E402  (also forces UTF-8 output on Windows)

WORDS = {
    "en": {"chronicle": "Chronicle of the campaign", "party": "The party", "region": "The region",
           "contents": "Contents", "session": "Session", "events": "What happened", "decisions": "Decisions",
           "loot": "Loot and changes", "documents": "Documents found", "level": "level", "where": "Where",
           "when": "In the world", "played": "Played", "sessions_n": "{n} sessions", "session_1": "1 session",
           "from": "signed"},
    "it": {"chronicle": "Cronaca della campagna", "party": "La compagnia", "region": "La regione",
           "contents": "Indice", "session": "Sessione", "events": "Che cosa è successo", "decisions": "Le scelte",
           "loot": "Bottino e cambiamenti", "documents": "Documenti trovati", "level": "livello", "where": "Dove",
           "when": "Nel mondo", "played": "Giocata il", "sessions_n": "{n} sessioni", "session_1": "1 sessione",
           "from": "firmato"},
}
# Which `### ` heading of a session entry feeds which part of a chapter.
SECTIONS = (("recap", ("recap",)), ("events", ("key events",)), ("decisions", ("decisions",)),
            ("loot", ("loot",)), ("closing", ("session recap",)))
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
_SESSION_RE = re.compile(r"^##\s+Session\s+(\d+)\s*(?:[—–-]\s*(.*))?$", re.MULTILINE)
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_NIGHT_HOURS = 6          # a picture made at 1 a.m. belongs to the evening before


class BookError(Exception):
    """The book cannot be made."""


# ─── Reading the campaign ─────────────────────────────────────────────────────

def parse_sessions(text: str) -> list:
    """Session entries of a session log: number, date, title, meta line, sections."""
    out = []
    marks = list(_SESSION_RE.finditer(text or ""))
    for i, m in enumerate(marks):
        body = text[m.end():marks[i + 1].start() if i + 1 < len(marks) else len(text)]
        body = re.split(r"^##\s", body, maxsplit=1, flags=re.MULTILINE)[0]      # stop at the next H2 of any kind
        head = (m.group(2) or "").strip()
        date = _DATE_RE.search(head)
        title = head
        if date:
            title = (head[:date.start()] + head[date.end():]).strip(" —–-")
        meta = {}
        first = next((ln for ln in body.splitlines() if ln.strip()), "")
        if first.lstrip().startswith("**"):
            for part in first.split("|"):
                km = re.match(r"\s*\*\*(.+?):\*\*\s*(.*)", part)
                if km and km.group(2).strip():
                    meta[km.group(1).strip().lower()] = km.group(2).strip()
        sections = {}
        chunks = re.split(r"^###\s+(.+)$", body, flags=re.MULTILINE)
        for name, content in zip(chunks[1::2], chunks[2::2]):
            key = next((k for k, starts in SECTIONS if name.strip().lower().startswith(starts)), None)
            content = re.sub(r"\n-{3,}\s*$", "", content.strip()).strip()
            if key and content and content not in ("-", "—"):
                sections.setdefault(key, content)
        out.append({"number": int(m.group(1)), "date": date.group(1) if date else "", "title": title,
                    "meta": meta, "sections": sections})
    return out


def load_sessions(camp: pathlib.Path) -> list:
    """Every session of the campaign, oldest first; the live log wins over the archive."""
    by_number = {}
    for name in ("session-log-archive.md", "session-log.md"):
        try:
            text = (camp / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for s in parse_sessions(text):
            if s["sections"]:
                by_number[s["number"]] = s
    return [by_number[n] for n in sorted(by_number)]


def load_party(camp: pathlib.Path) -> list:
    party = []
    for path in sorted((camp / "characters").glob("*.md")) if (camp / "characters").is_dir() else []:
        text = path.read_text(encoding="utf-8", errors="replace")
        first = text.splitlines()[0] if text else ""
        name = first[2:].strip() if first.startswith("# ") else path.stem
        field = lambda key: (re.search(rf"\*\*{key}:\*\*\s*([^|\n]+)", text) or [None, ""])[1].strip()
        party.append({"name": name, "race": field("Race"), "class": field("Class"), "level": field("Level"),
                      "slug": re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")})
    return party


def _asset_day(stamp: datetime.datetime) -> str:
    return (stamp - datetime.timedelta(hours=_NIGHT_HOURS)).strftime("%Y-%m-%d")


def load_pictures(camp: pathlib.Path, portraits: bool = False) -> tuple:
    """(pictures, portraits by character slug). A picture: file, caption, kind, day."""
    media = camp / "media"
    pictures, faces = [], {}
    try:
        index = json.loads((media / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        index = []
    for e in index if isinstance(index, list) else []:
        path = media / str(e.get("file", ""))
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXT:
            continue
        kind = e.get("kind", "")
        if kind == "portrait":
            if portraits:
                slug = re.sub(r"[^a-z0-9]+", "-", str(e.get("subject", "")).lower()).strip("-")
                faces[slug] = path                              # the newest one wins
        elif kind == "scene":
            try:
                stamp = datetime.datetime.fromisoformat(str(e.get("created", "")))
            except ValueError:
                stamp = datetime.datetime.fromtimestamp(path.stat().st_mtime)
            pictures.append({"file": path, "caption": e.get("caption") or e.get("subject", ""), "kind": "scene",
                             "day": _asset_day(stamp), "stamp": stamp})
    # Battle maps: one file per re-send; keep the last state of each map on each evening.
    last = {}
    for path in media.glob("map-*.svg") if media.is_dir() else []:
        m = re.match(r"map-(.+)-[0-9a-f]{6,}$", path.stem)
        if not m:
            continue
        stamp = datetime.datetime.fromtimestamp(path.stat().st_mtime)
        key = (m.group(1), _asset_day(stamp))
        if key not in last or stamp > last[key][1]:
            last[key] = (path, stamp)
    for (slug, day), (path, stamp) in last.items():
        head = path.read_text(encoding="utf-8", errors="replace")[:1500]
        tm = re.search(r'<text[^>]*\by="24"[^>]*>([^<]+)</text>', head)
        pictures.append({"file": path, "caption": html.unescape(tm.group(1)) if tm else slug.replace("-", " ").capitalize(),
                         "kind": "map", "day": day, "stamp": stamp})
    return sorted(pictures, key=lambda p: p["stamp"]), faces


def assign_pictures(sessions: list, pictures: list) -> dict:
    """Session number → its pictures: the session played that day, else the one before."""
    dated = sorted((s for s in sessions if s["date"]), key=lambda s: s["date"])
    out = {}
    for pic in pictures:
        owner = next((s for s in reversed(dated) if s["date"] <= pic["day"]), dated[0] if dated else None)
        if owner:
            out.setdefault(owner["number"], []).append(pic)
    return out


def load_handouts(camp: pathlib.Path) -> list:
    try:
        data = json.loads((camp / "handouts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [h for h in data if isinstance(h, dict) and h.get("title") and (h.get("body") or h.get("image"))] \
        if isinstance(data, list) else []


def region_picture(camp: pathlib.Path) -> pathlib.Path | None:
    try:
        name = json.loads((camp / "region.json").read_text(encoding="utf-8")).get("file", "")
    except (OSError, ValueError, AttributeError):
        return None
    path = camp / "media" / str(name)
    return path if name and path.is_file() else None


# ─── Writing the book ─────────────────────────────────────────────────────────

_CSS = """
@page { size: A4; margin: 20mm 18mm; }
* { box-sizing: border-box; }
body { margin: 0; color: #2a2118; background: #fff; font: 12pt/1.5 'Alegreya', Georgia, 'Times New Roman', serif; }
h1, h2, h3 { font-family: 'Alegreya SC', 'Alegreya', Georgia, serif; line-height: 1.15; color: #3b2a16; }
.cover { height: 250mm; display: flex; flex-direction: column; justify-content: center; text-align: center; page-break-after: always; }
.cover h1 { font-size: 40pt; margin: 0 0 8mm; letter-spacing: .02em; }
.cover .sub { font-size: 15pt; font-style: italic; margin: 0 0 14mm; color: #6b4a2b; }
.cover .who { font-size: 13pt; margin: 0 0 4mm; }
.cover .span { font-size: 11pt; color: #6b5a48; margin: 0; }
.cover .rule { width: 60mm; height: 0; border-top: 1.5pt double #6b4a2b; margin: 0 auto 12mm; }
section { page-break-before: always; }
section > h2 { font-size: 22pt; margin: 0 0 3mm; padding-bottom: 2mm; border-bottom: 1.5pt double #6b4a2b; }
h3 { font-size: 13pt; margin: 7mm 0 2mm; text-transform: uppercase; letter-spacing: .06em; color: #6b4a2b; }
p { margin: 0 0 3mm; text-align: justify; hyphens: auto; }
ul { margin: 0 0 3mm; padding-left: 6mm; }
li { margin: 0 0 1.6mm; }
.meta { margin: 0 0 6mm; font-size: 10.5pt; color: #6b5a48; }
.meta b { color: #3b2a16; }
.lead p:first-child::first-letter { font-size: 34pt; line-height: .8; float: left; padding: 1.5mm 2mm 0 0; font-family: 'Alegreya SC', Georgia, serif; color: #6b4a2b; }
.closing { margin-top: 6mm; padding: 4mm 6mm; border-left: 2pt solid #6b4a2b; font-style: italic; background: #f6efe0; }
.closing p:last-child { margin-bottom: 0; }
figure { margin: 6mm 0; text-align: center; page-break-inside: avoid; }
figure img { max-width: 100%; max-height: 105mm; border: .6pt solid #6b4a2b; border-radius: 1mm; }
figure.map img { max-height: 120mm; }
figure.full img { max-height: 215mm; }
figcaption { margin-top: 1.5mm; font-size: 10pt; font-style: italic; color: #6b5a48; }
.party { display: grid; grid-template-columns: 1fr 1fr; gap: 5mm 8mm; }
.pc { display: flex; gap: 4mm; align-items: center; page-break-inside: avoid; }
.pc img { width: 26mm; height: 26mm; object-fit: cover; border-radius: 50%; border: .6pt solid #6b4a2b; }
.pc b { display: block; font-size: 14pt; font-family: 'Alegreya SC', Georgia, serif; }
.pc span { color: #6b5a48; }
.toc { list-style: none; padding: 0; }
.toc li { display: flex; gap: 3mm; border-bottom: .4pt dotted #a08a6a; padding: 1.4mm 0; }
.toc li span { flex: 1; }
.toc li i { color: #6b5a48; font-size: 10.5pt; }
.doc { margin: 0 0 8mm; padding: 6mm 8mm; background: #f6efe0; border: .6pt solid #c7b48f; page-break-inside: avoid; }
.doc h4 { margin: 0 0 3mm; font-size: 12.5pt; font-family: 'Alegreya SC', Georgia, serif; }
.doc pre { margin: 0; white-space: pre-wrap; font: inherit; }
.doc .from { margin: 3mm 0 0; text-align: right; font-style: italic; }
@media screen { body { max-width: 190mm; margin: 0 auto; padding: 10mm; } .cover { height: auto; padding: 30mm 0; } }
"""
_FONTS = ("https://fonts.googleapis.com/css2?family=Alegreya:ital,wght@0,400;0,700;1,400"
          "&family=Alegreya+SC:wght@500;700&display=swap")


def _md(text: str) -> str:
    sys.path.insert(0, str(paths.display_dir()))
    import sheet_print
    return sheet_print.render_md(text)


def _esc(text) -> str:
    return html.escape(str(text or ""), quote=True)


def _figure(pic_path: pathlib.Path, caption: str, cls: str = "") -> str:
    return (f'<figure class="{cls}"><img src="{_esc(pic_path.resolve().as_uri())}" alt="{_esc(caption)}">'
            + (f"<figcaption>{_esc(caption)}</figcaption>" if caption else "") + "</figure>")


def pretty_title(campaign: str) -> str:
    small = {"di", "del", "della", "dei", "delle", "e", "of", "the", "and", "la", "il", "le", "in", "a"}
    words = campaign.replace("_", "-").split("-")
    return " ".join(w if (i and w in small) else w.capitalize() for i, w in enumerate(words) if w)


def build_html(title: str, sessions: list, party: list, pictures: dict, faces: dict, handouts: list,
               region: pathlib.Path | None, lang: str = "en", loot: bool = False, media: pathlib.Path | None = None) -> str:
    w = WORDS.get(lang, WORDS["en"])
    out = ['<!DOCTYPE html>', f'<html lang="{_esc(lang)}"><head><meta charset="utf-8">',
           f"<title>{_esc(title)}</title>", f'<link rel="stylesheet" href="{_FONTS}">', f"<style>{_CSS}</style></head><body>"]
    dates = [s["date"] for s in sessions if s["date"]]
    span = " — ".join(dict.fromkeys([dates[0], dates[-1]])) if dates else ""
    count = w["session_1"] if len(sessions) == 1 else w["sessions_n"].format(n=len(sessions))
    out.append(f'<div class="cover"><h1>{_esc(title)}</h1><div class="rule"></div><p class="sub">{_esc(w["chronicle"])}</p>'
               f'<p class="who">{_esc(" · ".join(p["name"] for p in party))}</p>'
               f'<p class="span">{_esc(" · ".join(x for x in (count, span) if x))}</p></div>')

    if party:
        out.append(f'<section><h2>{_esc(w["party"])}</h2><div class="party">')
        for p in party:
            face = faces.get(p["slug"])
            what = " ".join(x for x in (p["race"], p["class"]) if x)
            if p["level"]:
                what += f' · {w["level"]} {p["level"]}'
            out.append('<div class="pc">' + (f'<img src="{_esc(face.resolve().as_uri())}" alt="">' if face else "")
                       + f'<div><b>{_esc(p["name"])}</b><span>{_esc(what.strip(" ·"))}</span></div></div>')
        out.append("</div></section>")
    if region:
        out.append(f'<section><h2>{_esc(w["region"])}</h2>{_figure(region, "", "full")}</section>')

    out.append(f'<section><h2>{_esc(w["contents"])}</h2><ul class="toc">')
    for s in sessions:
        out.append(f'<li><span>{_esc(w["session"])} {s["number"]}' + (f' — {_esc(s["title"])}' if s["title"] else "")
                   + f'</span><i>{_esc(s["date"])}</i></li>')
    if handouts:
        out.append(f'<li><span>{_esc(w["documents"])}</span><i></i></li>')
    out.append("</ul></section>")

    for s in sessions:
        sec = s["sections"]
        out.append(f'<section><h2>{_esc(w["session"])} {s["number"]}' + (f' — {_esc(s["title"])}' if s["title"] else "") + "</h2>")
        meta = [(w["played"], s["date"]), (w["where"], s["meta"].get("location", "")),
                (w["when"], s["meta"].get("in-world date", ""))]
        line = " · ".join(f"<b>{_esc(k)}:</b> {_esc(v)}" for k, v in meta if v)
        if line:
            out.append(f'<p class="meta">{line}</p>')
        pics = pictures.get(s["number"], [])
        scenes = [p for p in pics if p["kind"] == "scene"]
        maps = [p for p in pics if p["kind"] == "map"]
        if sec.get("recap"):
            out.append(f'<div class="lead">{_md(sec["recap"])}</div>')
        for p in scenes[:1]:
            out.append(_figure(p["file"], p["caption"]))
        if sec.get("events"):
            out.append(f'<h3>{_esc(w["events"])}</h3>{_md(sec["events"])}')
        for p in scenes[1:]:
            out.append(_figure(p["file"], p["caption"]))
        if sec.get("decisions"):
            out.append(f'<h3>{_esc(w["decisions"])}</h3>{_md(sec["decisions"])}')
        for p in maps:
            out.append(_figure(p["file"], p["caption"], "map"))
        if loot and sec.get("loot"):
            out.append(f'<h3>{_esc(w["loot"])}</h3>{_md(sec["loot"])}')
        if sec.get("closing"):
            out.append(f'<div class="closing">{_md(sec["closing"])}</div>')
        out.append("</section>")

    if handouts:
        out.append(f'<section><h2>{_esc(w["documents"])}</h2>')
        for h in handouts:
            out.append(f'<div class="doc"><h4>{_esc(h["title"])}</h4>')
            image = (media / str(h.get("image"))) if media and h.get("image") else None
            if image and image.is_file():
                out.append(_figure(image, ""))
            if h.get("body"):
                out.append(f'<pre>{_esc(h["body"])}</pre>')
            if h.get("from"):
                out.append(f'<p class="from">— {_esc(h["from"])}</p>')
            out.append("</div>")
        out.append("</section>")
    out.append("</body></html>")
    return "\n".join(out)


def _range(text: str) -> tuple:
    m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+))?\s*", text or "")
    if not m:
        raise BookError(f"--sessions wants 3 or 2-5, not {text!r}")
    lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
    return min(lo, hi), max(lo, hi)


def make(campaign: str, out_dir: str = "", title: str = "", sessions_range: str = "", loot: bool = False,
         portraits: bool = False, pdf: bool = True, lang: str = "", to_pdf=None) -> dict:
    """Write the book. Returns {"html": path, "pdf": path or None, "sessions": n, "pictures": n}."""
    if not campaign or campaign != pathlib.Path(campaign).name:
        raise BookError(f"not a campaign name: {campaign!r}")
    camp = paths.find_campaign(campaign)
    if not camp.is_dir():
        raise BookError(f"no campaign folder at {camp}")
    sessions = load_sessions(camp)
    if sessions_range:
        lo, hi = _range(sessions_range)
        sessions = [s for s in sessions if lo <= s["number"] <= hi]
    if not sessions:
        raise BookError("no session to print yet — the book is made from session-log.md, written at /dm:dnd save")
    all_pictures, faces = load_pictures(camp, portraits)
    by_session = assign_pictures(sessions, all_pictures)
    page = build_html(title or pretty_title(campaign), sessions, load_party(camp), by_session, faces,
                      load_handouts(camp), region_picture(camp), lang or paths.campaign_language(campaign),
                      loot, camp / "media")
    folder = pathlib.Path(out_dir).expanduser() if out_dir else camp / "book"
    folder.mkdir(parents=True, exist_ok=True)
    html_path = folder / f"{campaign}.html"
    html_path.write_text(page, encoding="utf-8")
    pdf_path = None
    if pdf:
        if to_pdf is None:
            sys.path.insert(0, str(paths.display_dir()))
            import sheet_print
            to_pdf = lambda text: sheet_print.to_pdf(text, timeout=120)
        data = to_pdf(page)
        if data:
            pdf_path = folder / f"{campaign}.pdf"
            tmp = pdf_path.with_suffix(".pdf.part")
            tmp.write_bytes(data)
            os.replace(tmp, pdf_path)
    return {"html": html_path, "pdf": pdf_path, "sessions": len(sessions),
            "pictures": sum(len(v) for v in by_session.values())}


def _announce() -> None:
    """Tell the display a new book can be downloaded. Silent when it is not running."""
    try:
        sys.path.insert(0, str(paths.display_dir()))
        import send
        send._post(f"{send.BASE_URL}/book", b"{}", send._read_token())
    except Exception:
        pass


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="The chronicle of a campaign as a book.")
    ap.add_argument("--campaign", "-c", required=True)
    ap.add_argument("--out", default="", help="folder to write to (default: <campaign>/book)")
    ap.add_argument("--title", default="", help="the title on the cover (default: the campaign's name)")
    ap.add_argument("--sessions", default="", help="only these: 3 or 2-5")
    ap.add_argument("--loot", action="store_true", help="add each session's Loot & Changes")
    ap.add_argument("--portraits", action="store_true", help="add the characters' portraits, if the campaign has them")
    ap.add_argument("--no-pdf", action="store_true", help="write the HTML only")
    ap.add_argument("--lang", default="", help="language of the headings (default: the campaign's)")
    args = ap.parse_args(argv)
    try:
        done = make(args.campaign, args.out, args.title, args.sessions, args.loot, args.portraits,
                    not args.no_pdf, args.lang)
    except BookError as e:
        print(f"campaign_book: {e}", file=sys.stderr)
        return 1
    print(f"{done['sessions']} session(s), {done['pictures']} picture(s)")
    print(f"HTML: {done['html']}")
    if done["pdf"]:
        print(f"PDF:  {done['pdf']}")
        if not args.out:
            _announce()         # the display serves <campaign>/book/<campaign>.pdf to the players
    elif not args.no_pdf:
        print("PDF:  not made — no Chrome, Edge or Chromium found (set DND_PDF_BROWSER), or it took too long. "
              "Open the HTML in a browser and print it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
