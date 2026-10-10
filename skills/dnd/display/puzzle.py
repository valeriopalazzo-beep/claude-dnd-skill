#!/usr/bin/env python3
"""puzzle.py — a puzzle the players solve with their own hands, on their phones.

Three kinds, one at a time on the table:

    # A combination lock: wheels to turn until the code is right
    puzzle.py lock "Il forziere di Brakka" --code 3-7-1 --attempts 5 --hint "Le date sulla lapide"
    puzzle.py lock "La porta delle rune" --code S-O-L --symbols letters --feedback
    puzzle.py lock "Il sigillo" --code 2-5-8 --symbols runes      # positions in ᚠᚢᚦᚨᚱᚲᚷᚹ

    # A riddle: any of the answers you accept opens it
    puzzle.py riddle "L'enigma della porta" --answer eco --answer "un'eco" << 'DNDEND'
    Parlo senza bocca e sento senza orecchie.
    Non ho corpo, ma prendo vita con il vento. Chi sono?
    DNDEND

    # A cipher: you write the message, the script scrambles it; the players
    # turn a wheel until it reads true
    puzzle.py cipher "Il biglietto del contrabbandiere" --plain "la chiave è sotto la terza pietra" --shift 7

    puzzle.py hint                  # show the players the hint you stored
    puzzle.py hint "Guardate le iniziali"       # or a new one
    puzzle.py status                # who tried what, how many attempts are left
    puzzle.py close                 # take it off the table

Every screen shows the puzzle; anyone may try, and everyone sees each try. The
answer never leaves the display server. `--attempts N` limits the tries of the
whole table (default: no limit); `--feedback` makes a lock say how many wheels
are right after a wrong try. When the puzzle is solved, or the attempts run
out, you are told with the next check_input.py / autorun_wait.py as a
`[[PUZZLE …]]` line — narrate what happens, then `close` it.

The display must be running: a puzzle lives on the screens.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import unicodedata
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from runtime_paths import rt          # writable runtime dir (update-safe)

KINDS = ("lock", "riddle", "cipher")
SYMBOLS = {"digits": list("0123456789"), "letters": list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"), "runes": list("ᚠᚢᚦᚨᚱᚲᚷᚹ")}
MAX_WHEELS = 8
MAX_TRIES_KEPT = 40
COOLDOWN = 1.5                 # seconds between two tries, so a lock is not brute-forced by a script
NEWS_FILE = rt("puzzle_news.json")     # outcomes the DM has not been told about yet
TOKEN_FILE = rt(".token")
TIMEOUT = 4.0
_ARTICLES = ("il ", "lo ", "la ", "i ", "gli ", "le ", "l'", "un ", "una ", "uno ", "un'", "the ", "a ", "an ")


class PuzzleError(Exception):
    """A request that cannot be applied; the puzzle on the table is left as it was."""


def _clean(text, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


# ─── Making a puzzle ──────────────────────────────────────────────────────────

def _base(kind: str, title: str, text: str, attempts: int, hint: str) -> dict:
    title = _clean(title, 80)
    if not title:
        raise PuzzleError("a puzzle needs a title")
    if attempts < 0 or attempts > 50:
        raise PuzzleError("--attempts is 0 (no limit) to 50")
    return {"kind": kind, "title": title, "text": str(text or "").strip()[:1200], "max_attempts": attempts,
            "hint": _clean(hint, 300), "hint_shown": False, "tries": [], "used": 0, "solved": False,
            "failed": False, "solved_by": "", "last_try": 0.0}


def make_lock(title: str, code: str, symbols: str = "", attempts: int = 0, text: str = "", hint: str = "",
              feedback: bool = False) -> dict:
    parts = [p for p in re.split(r"[\s,\-.]+", (code or "").strip()) if p]
    if len(parts) == 1 and len(parts[0]) > 1 and (symbols or "digits") != "runes":
        parts = list(parts[0])                               # "371" is three wheels
    name = (symbols or ("letters" if parts and all(p.isalpha() for p in parts) else "digits")).strip().lower()
    if name not in SYMBOLS:
        raise PuzzleError(f"--symbols is one of {', '.join(SYMBOLS)} — not {symbols!r}")
    alphabet = SYMBOLS[name]
    solution = []
    for p in parts:
        if name == "runes":
            if not p.isdigit() or not 1 <= int(p) <= len(alphabet):
                raise PuzzleError(f"a rune is written as its place, 1 to {len(alphabet)} — not {p!r}")
            solution.append(int(p) - 1)
        else:
            if p.upper() not in alphabet:
                raise PuzzleError(f"{p!r} is not on a wheel of {name}")
            solution.append(alphabet.index(p.upper()))
    if not 2 <= len(solution) <= MAX_WHEELS:
        raise PuzzleError(f"a lock has 2 to {MAX_WHEELS} wheels — write the code as 3-7-1")
    puzzle = _base("lock", title, text, attempts, hint)
    puzzle.update(symbols=name, wheels=len(solution), solution=solution, feedback=bool(feedback))
    return puzzle


def normalise_answer(text: str) -> str:
    """Case, accents, punctuation and a leading article do not make an answer wrong."""
    s = unicodedata.normalize("NFKD", str(text or "").casefold().replace("’", "'"))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = " ".join(s.split())
    for art in _ARTICLES:
        if s.startswith(art):
            s = s[len(art):]
            break
    return re.sub(r"[^a-z0-9]+", "", s)


def make_riddle(title: str, text: str, answers: list, attempts: int = 0, hint: str = "") -> dict:
    accepted = sorted({normalise_answer(a) for a in answers or [] if normalise_answer(a)})
    if not accepted:
        raise PuzzleError("a riddle needs at least one --answer")
    if not str(text or "").strip():
        raise PuzzleError("a riddle needs its words (stdin, or --text)")
    puzzle = _base("riddle", title, text, attempts, hint)
    puzzle.update(answers=accepted, answer_shown=_clean(answers[0], 80))
    return puzzle


def caesar(text: str, shift: int) -> str:
    """Move every unaccented letter `shift` places along the alphabet; the rest stays."""
    out = []
    for ch in text:
        if "a" <= ch <= "z":
            out.append(chr((ord(ch) - 97 + shift) % 26 + 97))
        elif "A" <= ch <= "Z":
            out.append(chr((ord(ch) - 65 + shift) % 26 + 65))
        else:
            out.append(ch)
    return "".join(out)


def make_cipher(title: str, plain: str, shift: int, attempts: int = 0, text: str = "", hint: str = "") -> dict:
    plain = str(plain or "").strip()[:600]
    if len([c for c in plain if c.isalpha()]) < 4:
        raise PuzzleError("a cipher needs a message to hide (--plain)")
    if not 1 <= shift <= 25:
        raise PuzzleError("--shift is 1 to 25")
    # Accents would stay readable in the scrambled text and give the words away.
    bare = "".join(ch for ch in unicodedata.normalize("NFKD", plain) if not unicodedata.combining(ch))
    puzzle = _base("cipher", title, text, attempts, hint)
    puzzle.update(cipher=caesar(bare, shift), plain=bare, solution=shift)
    return puzzle


# ─── Trying ───────────────────────────────────────────────────────────────────

def _lock_guess(puzzle: dict, guess) -> list:
    if not isinstance(guess, list) or len(guess) != puzzle["wheels"]:
        raise PuzzleError("turn every wheel")
    size = len(SYMBOLS[puzzle["symbols"]])
    try:
        values = [int(g) for g in guess]
    except (TypeError, ValueError):
        raise PuzzleError("turn every wheel")
    if any(not 0 <= v < size for v in values):
        raise PuzzleError("that is not on the wheel")
    return values


def attempt(puzzle: dict, who: str, guess, now: float) -> dict:
    """One try. Changes the puzzle and returns {"ok", "shown", "feedback"}."""
    if puzzle["solved"] or puzzle["failed"]:
        raise PuzzleError("this puzzle is over")
    if now - float(puzzle.get("last_try", 0)) < COOLDOWN:
        raise PuzzleError("one try at a time — wait a moment")
    feedback = ""
    if puzzle["kind"] == "lock":
        values = _lock_guess(puzzle, guess)
        alphabet = SYMBOLS[puzzle["symbols"]]
        shown = (" " if puzzle["symbols"] == "runes" else "-").join(alphabet[v] for v in values)
        ok = values == puzzle["solution"]
        if not ok and puzzle.get("feedback"):
            feedback = str(sum(1 for a, b in zip(values, puzzle["solution"]) if a == b))
    elif puzzle["kind"] == "riddle":
        shown = _clean(guess, 80)
        if not normalise_answer(shown):
            raise PuzzleError("write an answer")
        ok = normalise_answer(shown) in puzzle["answers"]
    else:
        try:
            shift = int(guess) % 26
        except (TypeError, ValueError):
            raise PuzzleError("turn the wheel")
        shown = caesar(puzzle["cipher"], -shift)[:80]
        ok = shift == puzzle["solution"]
    puzzle["last_try"] = now
    puzzle["used"] = int(puzzle.get("used", 0)) + 1
    puzzle["tries"] = (puzzle["tries"] + [{"who": _clean(who, 50) or "?", "guess": shown, "ok": ok,
                                           "feedback": feedback}])[-MAX_TRIES_KEPT:]
    if ok:
        puzzle.update(solved=True, solved_by=_clean(who, 50))
    elif puzzle["max_attempts"] and puzzle["used"] >= puzzle["max_attempts"]:
        puzzle["failed"] = True
    return {"ok": ok, "shown": shown, "feedback": feedback}


def public_view(puzzle: dict | None) -> dict | None:
    """What the screens get: never the code, the answers or the hidden message."""
    if not puzzle:
        return None
    out = {k: puzzle.get(k) for k in ("id", "kind", "title", "text", "max_attempts", "used", "solved", "failed",
                                      "solved_by", "tries")}
    out["hint"] = puzzle.get("hint", "") if puzzle.get("hint_shown") else ""
    if puzzle["kind"] == "lock":
        out.update(symbols=SYMBOLS[puzzle["symbols"]], wheels=puzzle["wheels"], feedback=bool(puzzle.get("feedback")))
    elif puzzle["kind"] == "cipher":
        out["cipher"] = puzzle["cipher"]
        if puzzle["solved"]:
            out["plain"] = puzzle["plain"]
    return out


def outcome_line(puzzle: dict) -> str:
    """The line that tells the DM a puzzle is over."""
    last = puzzle["tries"][-1] if puzzle["tries"] else {"who": "?", "guess": "?"}
    if puzzle["solved"]:
        return (f'[[PUZZLE "{puzzle["title"]}" SOLVED by {last["who"]} ({last["guess"]}) at try {puzzle["used"]}. '
                "Narrate what opens or happens now, then take it off the table with puzzle.py close.]]")
    return (f'[[PUZZLE "{puzzle["title"]}" FAILED — the {puzzle["max_attempts"]} attempts are spent '
            f'(last: {last["who"]}: {last["guess"]}). Narrate the consequence, then puzzle.py close.]]')


# ─── Outcomes waiting for the DM: written by the display server, drained by the DM's scripts ──

def news_add(line: str) -> None:
    try:
        with open(NEWS_FILE, encoding="utf-8") as f:
            news = json.load(f)
        if not isinstance(news, list):
            news = []
    except (OSError, ValueError):
        news = []
    news.append(line)
    tmp = NEWS_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(news, f, ensure_ascii=False)
        os.replace(tmp, NEWS_FILE)
    except OSError:
        pass


def news_pending() -> bool:
    return os.path.exists(NEWS_FILE)


def drain_news() -> list:
    try:
        with open(NEWS_FILE, encoding="utf-8") as f:
            news = json.load(f)
        os.remove(NEWS_FILE)
    except (OSError, ValueError):
        return []
    return [str(n) for n in news] if isinstance(news, list) else []


# ─── Talking to the display ───────────────────────────────────────────────────

def _base_url() -> str:
    override = os.environ.get("DND_DISPLAY_URL", "").strip().rstrip("/")
    if override:
        return override
    scheme_file = os.path.join(_HERE, ".scheme")
    scheme = "http"
    if os.path.exists(scheme_file):
        scheme = open(scheme_file, encoding="utf-8").read().strip() or "http"
    return f"{scheme}://localhost:5001"


def _request(path: str, body: dict | None = None, method: str | None = None):
    url = _base_url() + path
    headers = {"Content-Type": "application/json"}
    try:
        token = open(TOKEN_FILE, encoding="utf-8").read().strip()
    except OSError:
        token = ""
    if token:
        headers["X-DND-Token"] = token
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method or ("POST" if data else "GET"))
    ctx = None
    if url.startswith("https"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except ValueError:
            return e.code, {}
    except (OSError, ValueError):
        return 0, {}


def describe(state: dict) -> str:
    if not state:
        return "no puzzle on the table"
    left = f"{state['max_attempts'] - state['used']} attempt(s) left" if state.get("max_attempts") else "no limit"
    status = "SOLVED by " + state.get("solved_by", "?") if state.get("solved") else "FAILED" if state.get("failed") else "open"
    lines = [f'{state["kind"]} "{state["title"]}" — {status}, {state.get("used", 0)} tried, {left}']
    lines += [f'  {"✓" if t["ok"] else "✗"} {t["who"]}: {t["guess"]}' + (f'  ({t["feedback"]} right)' if t.get("feedback") else "")
              for t in state.get("tries", [])]
    return "\n".join(lines)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="A puzzle the players solve on their phones.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for kind in KINDS:
        p = sub.add_parser(kind)
        p.add_argument("title")
        p.add_argument("--attempts", type=int, default=0, help="tries the whole table gets (0 = no limit)")
        p.add_argument("--hint", default="", help="kept until you run `puzzle.py hint`")
        p.add_argument("--text", default="", help="the words on the puzzle (or stdin)")
        if kind == "lock":
            p.add_argument("--code", required=True, help="3-7-1, S-O-L, or rune places 2-5-8")
            p.add_argument("--symbols", default="", help="digits, letters or runes (guessed from the code)")
            p.add_argument("--feedback", action="store_true", help="after a wrong try, say how many wheels are right")
        elif kind == "riddle":
            p.add_argument("--answer", action="append", default=[], help="an accepted answer (repeatable)")
        else:
            p.add_argument("--plain", required=True, help="the message to hide")
            p.add_argument("--shift", type=int, required=True, help="how far to turn the alphabet, 1-25")
    p = sub.add_parser("hint", help="show the players the hint")
    p.add_argument("text", nargs="?", default="")
    sub.add_parser("status")
    sub.add_parser("close")
    args = ap.parse_args(argv)

    try:
        if args.cmd in KINDS:
            text = args.text or ("" if sys.stdin.isatty() else sys.stdin.read())
            if args.cmd == "lock":
                puzzle = make_lock(args.title, args.code, args.symbols, args.attempts, text, args.hint, args.feedback)
            elif args.cmd == "riddle":
                puzzle = make_riddle(args.title, text, args.answer, args.attempts, args.hint)
            else:
                puzzle = make_cipher(args.title, args.plain, args.shift, args.attempts, text, args.hint)
            status, state = _request("/puzzle", {"puzzle": puzzle})
        elif args.cmd == "hint":
            status, state = _request("/puzzle/hint", {"text": args.text})
        elif args.cmd == "status":
            status, state = _request("/puzzle/status")
        else:
            status, state = _request("/puzzle", method="DELETE")
        if status == 0:
            raise PuzzleError("the display is not running — a puzzle lives on the screens")
        if status >= 400:
            raise PuzzleError(state.get("error") or f"the display answered {status}")
        if args.cmd == "close":
            print("puzzle closed")
        else:
            print(describe(state))
            if args.cmd == "cipher":
                print(f'  on the screens: {puzzle["cipher"]}')
    except PuzzleError as e:
        print(f"puzzle: {e}. Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
