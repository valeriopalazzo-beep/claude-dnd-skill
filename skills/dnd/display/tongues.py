#!/usr/bin/env python3
"""tongues.py — who can read a document written in another language.

A handout (handout.py --language Elfico) is read only by the characters whose
sheet lists that language (`**Languages:** Comune, Elfico`); everyone else
gets the shapes of the script on the paper and not one word of it. This module
knows the 5e languages by their English and Italian names, reads the sheet's
Languages line, and turns a text into glyphs of the right script — the same
letters always give the same glyphs, so a word that comes back looks the same.
"""
from __future__ import annotations

import re
import unicodedata

# Canonical (English) name → other names a sheet or the DM may use for it.
ALIASES = {
    "common": ("comune", "lingua comune"),
    "dwarvish": ("nanico", "dwarven", "nano"),
    "elvish": ("elfico", "elven", "elfo"),
    "giant": ("gigante", "dei giganti"),
    "gnomish": ("gnomesco", "gnomico"),
    "goblin": ("goblinoide",),
    "halfling": ("halfling",),
    "orc": ("orchesco", "orchish", "orco"),
    "abyssal": ("abissale",),
    "celestial": ("celestiale",),
    "draconic": ("draconico",),
    "deep speech": ("linguaggio delle profondita", "lingua delle profondita", "gergo delle profondita"),
    "infernal": ("infernale",),
    "primordial": ("primordiale", "aquan", "auran", "ignan", "terran"),
    "sylvan": ("silvano", "silvestre"),
    "undercommon": ("sottocomune",),
    "thieves cant": ("gergo dei ladri", "gergo ladresco", "thieves' cant"),
    "druidic": ("druidico",),
}
_INDEX = {}
for _canon, _others in ALIASES.items():
    for _name in (_canon, *_others):
        _INDEX[re.sub(r"[^a-z ]", "", unicodedata.normalize("NFKD", _name).encode("ascii", "ignore").decode().lower()).strip()] = _canon

# The script each language is written in (PHB 2014 p. 123), drawn with Unicode
# letters every phone has: what matters is that it cannot be read.
SCRIPTS = {
    "elvish": "აბგდევზთიკლმნოპჟრსტუფქღყშჩ",         # Elvish script: flowing
    "dwarvish": "ᚠᚢᚦᚨᚱᚲᚷᚹᚺᚾᛁᛃᛇᛈᛉᛊᛏᛒᛖᛗᛚᛜᛞᛟᚫᚣ",       # Dethek: runes
    "infernal": "ሀለሐመሠረሰሸቀበቨተቸኀነኘአከኸወዐዘዠየደጀ",       # Infernal script
    "draconic": "ԱԲԳԴԵԶԷԸԹԺԻԼԽԾԿՀՁՂՃՄՅՆՇՈՉՊ",       # Iokharic
    "celestial": "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩϘϚ",       # Celestial script
}
_FAMILY = {"elvish": "elvish", "sylvan": "elvish", "dwarvish": "dwarvish", "giant": "dwarvish", "gnomish": "dwarvish",
           "goblin": "dwarvish", "orc": "dwarvish", "infernal": "infernal", "draconic": "draconic",
           "primordial": "dwarvish", "abyssal": "infernal", "celestial": "celestial", "deep speech": "draconic",
           "undercommon": "elvish", "druidic": "draconic", "thieves cant": "elvish", "halfling": "elvish",
           "common": "celestial"}
_LANG_LINE = re.compile(r"^[ \t]*(?:[-*][ \t]+)?\**\s*(?:Languages|Lingue)\s*:?\**\s*:?\s*(.+)$", re.M | re.I)


def _plain(name: str) -> str:
    return re.sub(r"[^a-z ]", "", unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()).strip()


def canonical(name: str) -> str:
    """'Elfico' / 'Elvish' / 'elven' → 'elvish'; a language the table invented stays itself (lowercase)."""
    p = " ".join(_plain(name).split())
    return _INDEX.get(p, p)


def sheet_languages(text: str) -> set:
    """The canonical names on a sheet's Languages line."""
    m = _LANG_LINE.search(text or "")
    if not m:
        return set()
    return {canonical(part) for part in re.split(r"[,;/]| e | and ", m.group(1)) if canonical(part)}


def knows(text: str, language: str) -> bool:
    return canonical(language) in sheet_languages(text)


def glyphs(text: str, language: str) -> str:
    """The text in the language's script: letters become glyphs, spaces and punctuation stay."""
    alphabet = SCRIPTS[_FAMILY.get(canonical(language), "elvish")]
    out = []
    for ch in text or "":
        base = unicodedata.normalize("NFKD", ch)[:1].lower()
        if "a" <= base <= "z":
            out.append(alphabet[(ord(base) - 97) % len(alphabet)])
        elif ch.isdigit():
            out.append(alphabet[(int(ch) * 7 + 3) % len(alphabet)])
        else:
            out.append(ch)
    return "".join(out)
