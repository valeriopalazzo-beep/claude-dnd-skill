"""
app.py — DnD DM display server

Receives text chunks from wrapper.py, detects scene context from keywords,
and pushes both to the browser via Server-Sent Events.

Endpoints:
    GET  /                   → serves index.html
    POST /chunk              → receives text chunk from wrapper.py
    POST /stats              → receives character/combat stat updates (merged, persisted)
    GET  /stream             → SSE stream to browser (text + scene + stats events)
    GET  /ping               → health check
    POST /clear              → wipe text log and broadcast clear event
    POST /player-input         → legacy queue endpoint (check_input.py compat)
    POST /player-input/drain   → drain legacy queue (check_input.py compat)
    POST /player-input/stage   → stage an action for review before firing
    POST /player-input/ready   → mark a staged action as ready
    POST /player-input/unstage → remove a staged action
    POST /player-input/skip    → skip a character's turn (stages + readies a skip entry)
    GET  /srd-lookup           → look up a spell/item/feature/condition by name
    GET  /login/options        → (LAN) characters that can log in
    POST /login, /logout       → (LAN) PIN login for players / DM — see accounts.py
"""

import hashlib
import hmac
import json
import os
import queue
import random
import re
import secrets
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse
from flask import Flask, Response, g, redirect, request, render_template, jsonify, send_from_directory, send_file
from flask_cors import CORS

# This file lives at <code-root>/display/ — resolve dirs from its location so
# paths work in any install mode (plugin, standalone skill, or dev clone).
# Writable runtime state goes to rt() (the update-safe runtime dir), NOT here.
_HERE         = os.path.dirname(os.path.abspath(__file__))
_ROOT         = os.path.dirname(_HERE)
SCRIPTS_DIR   = os.path.join(_ROOT, "scripts")
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from runtime_paths import rt          # resolves <data-root>/.runtime
LOG_FILE      = rt("text_log.json")

# SRD lookup module — degrades silently if dataset not built
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
try:
    import lookup as _lookup
    _SRD_AVAILABLE = True
except Exception:
    _lookup = None          # type: ignore
    _SRD_AVAILABLE = False

import functools as _functools
import script_ranges

from paths import find_campaign as _find_campaign
from utf8io import read_text as _read_text

# Audio module — degrades silently if numpy not installed
_AUDIO_DIR = os.path.dirname(os.path.abspath(__file__))
import sys as _sys
if _AUDIO_DIR not in _sys.path:
    _sys.path.insert(0, _AUDIO_DIR)
try:
    import audio as _audio
    _audio.init()
except Exception:
    _audio = None   # type: ignore

# TTS module — degrades silently if Gemini API key not configured
try:
    import tts as _tts
except Exception:
    _tts = None   # type: ignore

# Background music catalog (files built by build_music.py into <data-root>/music)
try:
    import music as _music
except Exception:
    _music = None   # type: ignore


def _display_sfx_languages() -> list:
    """The display's own languages (display/i18n/<code>.json) that have an SFX
    trigger pack, English last — so narration in any language the table can
    pick in Settings triggers sounds without configuring anything."""
    try:
        codes = sorted(fn[:-5] for fn in os.listdir(os.path.join(_HERE, "i18n"))
                       if fn.endswith(".json"))
    except OSError:
        codes = []
    return [c for c in codes if c != "en" and c in _audio.available_languages()] + ["en"]


def _apply_campaign_sfx_languages() -> None:
    """Choose the SFX trigger languages for the active campaign.

    The campaign's state.md Session Flag wins (`sfx_languages: en,zh,es`),
    then the DND_SFX_LANGUAGES env var, then the display's own languages.
    Runs at startup and whenever the DM switches campaign.
    """
    if _audio is None:
        return
    langs = []
    try:
        camp = re.sub(r"[^A-Za-z0-9_-]", "", open(rt(".campaign"), encoding="utf-8").read().strip())
        state_md = _find_campaign(camp) / "state.md" if camp else None
        if state_md is not None and state_md.exists():
            text = state_md.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"^\s*sfx_languages:\s*([\w,\s\-]+)$", text, re.MULTILINE)
            if m:
                langs = [l.strip() for l in m.group(1).split(",") if l.strip()]
    except (OSError, ValueError):
        pass
    if not langs:
        langs = [l.strip() for l in os.environ.get("DND_SFX_LANGUAGES", "").split(",") if l.strip()]
    valid = [l for l in langs if l in _audio.available_languages()]
    _audio.set_sfx_languages(valid or _display_sfx_languages())


_apply_campaign_sfx_languages()

HELP_LOCK     = rt(".help-lock")
CAMP_FILE     = rt(".campaign")
STATS_FILE    = rt("stats.json")
TOKEN_FILE    = rt(".token")
INPUT_FILE    = rt("player_input.json")
TRIGGER_FILE  = rt(".input_trigger")
QUEUE_FILE    = rt(".input_queue")
DEVICES_FILE         = rt(".approved_devices.json")
PENDING_DEVICES_FILE = rt(".pending_devices.json")

# ─── LAN / TLS mode ───────────────────────────────────────────────────────────
# Pass --lan to bind on 0.0.0.0 and protect write endpoints with a token.
# Pass --tls (requires --lan) to enable HTTPS with a self-signed cert.
# Without --lan the server binds to localhost only; no token is required.

_LAN_MODE: bool = "--lan" in sys.argv
_TLS_MODE: bool = "--tls" in sys.argv
if _LAN_MODE:
    sys.argv.remove("--lan")   # prevent Flask from seeing an unknown flag
if _TLS_MODE:
    sys.argv.remove("--tls")


def _get_or_create_token() -> str:
    """Load or generate the LAN token. Upgrades short legacy tokens to 64-char."""
    try:
        token = open(TOKEN_FILE, encoding="utf-8").read().strip()
        if len(token) >= 48:   # 48+ chars = already long enough
            return token
    except FileNotFoundError:
        pass
    token = secrets.token_hex(32)   # 64-char hex — brute force infeasible
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token)
    os.chmod(TOKEN_FILE, 0o600)
    return token


_lan_token: Optional[str] = _get_or_create_token() if _LAN_MODE else None


# ─── Rate limiting ────────────────────────────────────────────────────────────
# Simple in-process sliding window: max 20 write requests per IP per minute.
# Prevents spam injection and brute-force token guessing on write endpoints.

import time as _time

_rate_buckets: dict[str, list] = {}
_rate_lock = threading.Lock()
_RATE_WINDOW = 60    # seconds
_RATE_MAX    = 20    # requests per window per IP


def _rate_ok(ip: str) -> bool:
    now = _time.time()
    with _rate_lock:
        bucket = [t for t in _rate_buckets.get(ip, []) if now - t < _RATE_WINDOW]
        if len(bucket) >= _RATE_MAX:
            return False
        bucket.append(now)
        _rate_buckets[ip] = bucket
    return True


# ─── Input validation helpers ─────────────────────────────────────────────────

_PRINTABLE    = re.compile(
    "[^"
    "\x20-\x7E"                 # ASCII printable
    " -ɏ"             # Latin-1 + Latin Extended A/B (é ñ ö ć ş ž etc.)
    "Ͱ-Ͽ"             # Greek
    "Ѐ-ӿ"             # Cyrillic (Russian, Ukrainian)
    "֐-׿"             # Hebrew
    "؀-ۿ"             # Arabic
    "ݐ-ݿ"             # Arabic Supplement
    "ऀ-ॿ"             # Devanagari (Hindi, Marathi)
    "ঀ-৿"             # Bengali
    "஀-௿"             # Tamil
    "ఀ-౿"             # Telugu
    "฀-๿"             # Thai
    "Ḁ-ỿ"             # Latin Extended Additional (Vietnamese diacritics)
    "　-〿"             # CJK Symbols and Punctuation
    "぀-ゟ"             # Hiragana
    "゠-ヿ"             # Katakana
    "㐀-䶿"             # CJK Extension A
    "一-鿿"             # CJK Unified Ideographs
    "가-힯"             # Hangul Syllables
    "＀-￯"             # Halfwidth / Fullwidth
    "]"
)
_SHELL_CHARS  = re.compile(r'[$`\\;|&><()\[\]{}!]')
# Unicode \w covers letters from all scripts above. Allow space, apostrophe, hyphen
# inside the name; trim 1-2 char names to a separate branch.
_CHAR_NAME_RE = re.compile(r"^\w[\w '\-]{0,48}\w$|^\w{1,2}$", re.UNICODE)


def _sanitize_input(text: str) -> str:
    """Strip control chars and shell metacharacters from player input text."""
    text = _SHELL_CHARS.sub("", text)
    text = _PRINTABLE.sub("", text)
    return text[:500].strip()


def _char_ok(name: str, known: set) -> bool:
    """Return True if character name is syntactically valid and in the party."""
    if not _CHAR_NAME_RE.match(name):
        return False
    if known and name not in known and name != "Everybody":
        return False
    return True


# ─── Device approval system ───────────────────────────────────────────────────
# Each browser generates a UUID device ID (localStorage). On first input attempt
# from an unseen LAN device, the request is held and the DM sees an Approve/Deny
# card on the display. Localhost is auto-approved. Denied devices are blocked for
# the session.

_approved_devices: set[str]       = set()
_denied_devices:   set[str]       = set()
_pending_devices:  dict[str, dict] = {}  # device_id -> {ip, first_seen}
_devices_lock = threading.Lock()


def _persist_approved_devices() -> None:
    """Persist approved devices to disk. Must be called WITHOUT _devices_lock held."""
    try:
        with _devices_lock:
            data = list(_approved_devices)
        with open(DEVICES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.chmod(DEVICES_FILE, 0o600)
    except Exception:
        pass


def _load_approved_devices() -> None:
    try:
        with open(DEVICES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        with _devices_lock:
            for d in data:
                _approved_devices.add(str(d))
    except Exception:
        pass


def _persist_pending_devices() -> None:
    """Persist pending devices to disk so they survive app restarts. Must be called WITHOUT _devices_lock held."""
    try:
        with _devices_lock:
            data = list(_pending_devices.values())
        with open(PENDING_DEVICES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.chmod(PENDING_DEVICES_FILE, 0o600)
    except Exception:
        pass


def _load_pending_devices() -> None:
    try:
        with open(PENDING_DEVICES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        with _devices_lock:
            for d in data:
                if isinstance(d, dict) and d.get("id"):
                    # Skip if already approved/denied during this run
                    if d["id"] not in _approved_devices and d["id"] not in _denied_devices:
                        _pending_devices[d["id"]] = d
    except Exception:
        pass


_load_approved_devices()
_load_pending_devices()


# A casual home-LAN game doesn't need a per-device approval gate — it's friction
# (every phone sits on "Awaiting approval" until the DM taps a card). Default:
# trust any device that can already reach the server. Set DND_REQUIRE_APPROVAL=1
# to restore the approve/deny gate (e.g. on an untrusted/shared network).
_REQUIRE_APPROVAL = os.environ.get("DND_REQUIRE_APPROVAL", "").strip().lower() in ("1", "true", "yes", "on")


def _device_ok(device_id: str, ip: str) -> str:
    """Return 'approved', 'pending', or 'denied' for a given device."""
    if not device_id:
        return "denied"
    _need_persist_approved = False
    _need_persist_pending  = False
    with _devices_lock:
        if device_id in _approved_devices:
            return "approved"
        if device_id in _denied_devices:
            return "denied"
        # Auto-approve localhost always, and every reachable device unless the
        # approval gate is explicitly required.
        if not _REQUIRE_APPROVAL or ip in ("127.0.0.1", "::1"):
            _approved_devices.add(device_id)
            _need_persist_approved = True
        # New LAN device with the gate on — hold and notify DM
        elif device_id not in _pending_devices:
            _pending_devices[device_id] = {
                "id":         device_id,
                "ip":         ip,
                "first_seen": _time.time(),
            }
            _need_persist_pending = True
            _broadcast({"device_request": {"id": device_id, "ip": ip}})
    # Persist outside the lock to avoid deadlock (Lock is not reentrant)
    if _need_persist_approved:
        _persist_approved_devices()
        return "approved"
    if _need_persist_pending:
        _persist_pending_devices()
    return "pending"


# ─── Staged input system ──────────────────────────────────────────────────────
# Players stage their actions from the display companion UI. When all expected
# players mark ready, the combined action is written to TRIGGER_FILE for
# wrapper.py to inject into Claude's PTY stdin.

_staged: dict[str, dict] = {}   # {char_name: {text, ready, timestamp}}
_staged_lock = threading.Lock()
_expected_count = 1             # updated when stats arrive; min 1
_autorun_threshold: Optional[int] = None  # overrides _expected_count when set via push_stats --autorun-threshold

# Tracks which character names are currently sitting in .input_queue waiting
# for the DM to press Enter. Set when queue is written, cleared when wrapper
# POSTs /queue/consumed after injection. Persists through page reloads via SSE
# initial data and is broadcast to all connected clients on change.
_queue_status: list = []
_queue_status_lock = threading.Lock()

# Last autorun cycle broadcast — replayed on SSE reconnect so late-joining
# clients start the countdown from the correct elapsed position.
# Cleared when autorun_waiting=false (turn resolved or autorun disabled).
_autorun_cycle: Optional[dict] = None
_autorun_cycle_lock = threading.Lock()


def _normalize_slot(slot: dict) -> None:
    """Coerce a spell-slot entry to the canonical {used, max} shape in place.

    Tolerates legacy/alt payloads that use `remaining` instead of `used`.
    Without this, _slot_use/_slot_restore raise KeyError on a slot stored
    under the alt schema (e.g. after a long-rest --spell-slots full-replace).
    """
    if "used" in slot:
        return
    mx = slot.get("max", 0)
    if "remaining" in slot:
        slot["used"] = max(mx - int(slot.get("remaining", 0)), 0)
    else:
        slot["used"] = 0


def _staged_snapshot() -> dict:
    """Return a serialisable copy of the staged dict (no IP field)."""
    return {k: {"text": v["text"], "ready": v["ready"]} for k, v in _staged.items()}


def _check_auto_trigger() -> None:
    """Move staged-and-ready actions into the DM-gated queue file (.input_queue).

    .input_queue is NOT injected immediately — wrapper.py picks it up the next
    time the DM presses Enter (or Claude explicitly triggers via .input_trigger).
    This gives the DM control over when player actions enter Claude's context.
    """
    with _staged_lock:
        if not _staged:
            return
        everybody_ready = "Everybody" in _staged and _staged["Everybody"]["ready"]
        all_ready       = all(v["ready"] for v in _staged.values())
        threshold       = _autorun_threshold if _autorun_threshold is not None else _expected_count
        enough          = len(_staged) >= threshold or everybody_ready
        if not (all_ready and enough):
            return
        char_names = list(_staged.keys())
        lines      = [f'[{c}]: {e["text"]}' for c, e in _staged.items()]
        content    = "\n".join(lines)
        _staged.clear()

    try:
        with open(QUEUE_FILE, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        char_names = []

    if char_names:
        with _queue_status_lock:
            _queue_status.clear()
            _queue_status.extend(char_names)
    _broadcast({"staged_inputs": {}, "queue_status": list(char_names)})


def _token_ok() -> bool:
    """Return True if the request is authenticated (or we're in localhost mode).

    In LAN mode the before_request gate (_auth_gate) has already resolved who
    is calling — the DM (token header or this PC) or a logged-in player — and
    refused DM-only endpoints to players, so this only checks that someone
    is logged in.
    """
    if _lan_token is None:
        return True   # localhost mode — no token required
    return getattr(g, "role", None) is not None


app = Flask(__name__)

app.config['TEMPLATES_AUTO_RELOAD'] = True
CORS(app)


# ─── Login (LAN mode) ─────────────────────────────────────────────────────────
# In LAN mode nothing is served to a browser that isn't logged in, except the
# login page itself. Three ways to be authenticated:
#   - the X-DND-Token header (send.py / push_stats.py / wrapper.py)  → DM
#   - a request from this PC (loopback)                              → DM
#   - a session cookie from POST /login                              → DM or player
# This PC can also sit down as a player without giving up the DM's screen: in
# LAN mode a second listener on 127.0.0.1 (_PLAYER_PORT, "the players' door")
# serves the same app, and a request that comes in through it is never "this
# PC, so the DM" - it logs in with a PIN like any other device. A browser's
# cookies ignore the port, so the display's own port keeps ignoring sessions
# from this PC: one tab is the DM, another is the character.
# A player session is bound to one character: the server acts as that
# character whatever name the browser sends, and refuses DM-only endpoints.
# PINs are stored hashed by accounts.py (see its docstring).

import accounts as _accounts

SESSIONS_FILE   = rt("sessions.json")
_SESSION_COOKIE = "dnd_session"
_SESSION_TTL    = 30 * 86400   # a phone stays logged in for 30 days

# sha256(session id) → {"role", "character", "campaign", "created"}. Only the
# hash is persisted, so the sessions file can't be replayed as cookies.
_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()

# Failed-PIN throttling: 5 misses per IP in 15 min, 10 per character in 1 h.
_LOGIN_IP_MAX,   _LOGIN_IP_WINDOW   = 5, 15 * 60
_LOGIN_CHAR_MAX, _LOGIN_CHAR_WINDOW = 10, 60 * 60
_login_fails_ip:   dict[str, list] = {}
_login_fails_char: dict[str, list] = {}
_login_lock = threading.Lock()

# Reachable without logging in. Every other endpoint needs a login.
_PUBLIC_ENDPOINTS = {"ping", "serve_icon", "favicon", "login", "login_options", "logout", "static",
                     # The installable display: manifest and service worker, no game data.
                     "pwa_manifest", "pwa_sw",
                     "crea_page", "crea_start", "crea_conv", "crea_msg", "crea_finish",
                     # /campagna checks its own password cookie (camp_create.py).
                     "campagna_page", "campagna_login", "campagna_logout", "campagna_start",
                     "campagna_conv", "campagna_msg", "campagna_generate"}
# Only the DM may call these (the narration feed, stats, dice requests, queue
# plumbing, device approval, wiping the log).
_DM_ENDPOINTS = {
    "chunk", "image", "map_clear", "stats", "clear", "health", "tts_voice",
    "dice_request", "dice_request_status", "dice_request_cancel",
    "region_changed",   # region_map.py redrew the region map
    "device_approve", "device_deny",
    "queue_consumed", "submit_now", "drain_player_input",
    # Table-wide settings and the DM hint — the DM's, not a player's.
    "help_request", "narration_pref", "audio_toggle",
    # Opening and closing a shop; buying from it is the players'.
    "shop_set",
    # Opening and closing a rest; resting is the players'.
    "rest_set",
    # Asking the table a question; answering is the players'.
    "vote_set",
    # A chase: chase.py starts it and plays the creatures; running is the players'.
    "chase_set",
    # The recap at the start of a session.
    "recap_set",
    # A player's own "while you were away" recap: missed.py writes it, the player reads it.
    "missed_changed",
    # The ambient sounds under the music.
    "ambience_set",
    # Where the party is — sets everyone's background and music.
    "set_scene",
    # Documents the party finds: the DM hands them out, everyone may read them.
    "handout", "handout_delete",
    # Progress clocks: clock.py moves them, everyone watches.
    "clocks_changed",
    # The bestiary: bestiary.py writes it, everyone reads it.
    "bestiary_changed",
    # Quests: quests.py keeps them with the campaign, everyone reads them.
    "quests_changed",
    # Faction reputations: factions.py keeps them with the campaign, everyone reads them.
    "factions_changed",
    # The party's supplies: supplies.py spends them, everyone sees them.
    "supplies_changed",
    # Downtime: downtime.py opens and closes it; choosing is the players'.
    "downtime_changed",
    # A journey: travel.py sets the pace and finds the way; the roles on the road are the players'.
    "travel_changed",
    # Diseases, poisons, madness: afflictions.py and the clock change them; the saves are the players'.
    "afflictions_apply", "afflictions_ask",
    # The party's common fund: treasury.py changes it; putting in and taking out is the players'.
    "treasury_changed",
    # The party's stronghold: stronghold.py and the calendar change it, everyone reads it.
    "stronghold_changed",
    # The DM's screen: everything the players do not see, and hidden rolls.
    "dm_screen", "dm_screen_data", "dm_screen_roll",
    # People met: people.py writes the notebook, everyone reads it.
    "people_changed",
    # The timeline: timeline.py writes it, everyone reads what is not secret.
    "timeline_changed",
    # Who is with whom when the party splits: split.py sets it.
    "split_changed",
    # Companions: companion.py keeps them, everyone sees them.
    "companions_changed",
    # Vehicles: vehicles.py keeps them, everyone sees them.
    "vehicles_changed",
    # Rumors: rumors.py writes and hands them out; the players see what they heard.
    "rumors_changed",
    # Wild Shape and Polymorph: shapes.py puts a character in a beast's body.
    "shapes_changed",
    # The campaign book: campaign_book.py makes it, everyone may download it.
    "book_changed",
    # A puzzle: the DM sets it, hints and closes it; trying it is the players'.
    "puzzle_open", "puzzle_close", "puzzle_hint", "puzzle_status",
    # Opening an initiative roll; rolling in it is the players'.
    "initiative_open", "initiative_cancel",
}


def _sid_key(sid: str) -> str:
    return hashlib.sha256(sid.encode("utf-8")).hexdigest()


def _load_sessions() -> None:
    try:
        with open(SESSIONS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    now = _time.time()
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict) and now - float(v.get("created", 0)) < _SESSION_TTL:
                _sessions[k] = v


def _persist_sessions() -> None:
    with _sessions_lock:
        data = dict(_sessions)
    try:
        tmp = SESSIONS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, SESSIONS_FILE)
        os.chmod(SESSIONS_FILE, 0o600)
    except OSError:
        pass


_load_sessions()


def _is_loopback(ip: Optional[str]) -> bool:
    return ip in ("127.0.0.1", "::1") or (ip or "").startswith("::ffff:127.")


_DISPLAY_PORT = 5001
# The players' door on this PC: set at startup when its listener is up.
_PLAYER_PORT: Optional[int] = None


def _on_players_door() -> bool:
    """The request came in through the players' door (this PC, second port)."""
    return _PLAYER_PORT is not None and str(request.environ.get("SERVER_PORT", "")) == str(_PLAYER_PORT)


def _this_pc_is_dm() -> bool:
    """This PC on the display's own port: the DM, with no login."""
    return _is_loopback(request.remote_addr) and not _on_players_door()


def _door_url(port: int) -> str:
    """The address of another door of this display, on the host this browser used."""
    host = request.host if request.host.endswith("]") else request.host.rsplit(":", 1)[0]
    return f"{request.scheme}://{host}:{port}/"


def _player_url() -> str:
    """Where the DM's own browser opens the display as a player ("" if it cannot)."""
    if _lan_token is None or _PLAYER_PORT is None or not _this_pc_is_dm():
        return ""
    return _door_url(_PLAYER_PORT)


def _dm_url() -> str:
    """The DM's screen, for a browser on this PC that came in as a player ("" elsewhere)."""
    if _lan_token is None or not (_is_loopback(request.remote_addr) and _on_players_door()):
        return ""
    return _door_url(_DISPLAY_PORT)


def _login_campaign() -> str:
    """Active campaign name, sanitised the same way accounts.py does."""
    try:
        camp = open(CAMP_FILE, encoding="utf-8").read().strip()
    except OSError:
        camp = ""
    return re.sub(r"[^A-Za-z0-9_-]", "", camp)[:50]


def _session_from_request() -> Optional[dict]:
    """The caller's session if its cookie is valid right now, else None.

    A session dies when it expires, when the active campaign is not the one it
    was created for, when its PIN was changed after it was created, or after
    `accounts.py logout-all`.
    """
    sid = request.cookies.get(_SESSION_COOKIE, "")
    if not sid:
        return None
    key = _sid_key(sid)
    with _sessions_lock:
        s = _sessions.get(key)
    if not s:
        return None
    created = float(s.get("created", 0))
    if _time.time() - created > _SESSION_TTL:
        with _sessions_lock:
            _sessions.pop(key, None)
        _persist_sessions()
        return None
    if s.get("role") == "dm":
        rec = _accounts.dm_record()
        return s if rec and created >= float(rec.get("changed", 0)) else None
    camp = _login_campaign()
    if not camp or s.get("campaign") != camp:
        return None
    data = _accounts.load(camp)
    stored = _accounts.find_character(data, s.get("character", ""))
    if not stored:
        return None
    if created < float(data["characters"][stored].get("changed", 0)):
        return None
    if created < float(data.get("logout_before", 0) or 0):
        return None
    return s


def _login_wait(ip: str, char_key: str) -> int:
    """Seconds this IP / character must wait before another PIN try (0 = free)."""
    now = _time.time()
    wait = 0
    with _login_lock:
        for table, key, mx, win in (
            (_login_fails_ip, ip, _LOGIN_IP_MAX, _LOGIN_IP_WINDOW),
            (_login_fails_char, char_key, _LOGIN_CHAR_MAX, _LOGIN_CHAR_WINDOW),
        ):
            fails = [t for t in table.get(key, []) if now - t < win]
            table[key] = fails
            if len(fails) >= mx:
                wait = max(wait, int(fails[0] + win - now) + 1)
    return wait


def _login_failed(ip: str, char_key: str) -> None:
    now = _time.time()
    with _login_lock:
        _login_fails_ip.setdefault(ip, []).append(now)
        _login_fails_char.setdefault(char_key, []).append(now)


def _acting_character(requested: str) -> str:
    """The character this request may act as.

    A player always acts as their own character, whatever name the browser
    sent (returned with the party's spelling when the party lists it). The
    DM may act as anyone.
    """
    if getattr(g, "role", None) != "player":
        return requested
    own = g.character or ""
    with _stats_lock:
        names = [p.get("name", "") for p in _current_stats.get("players", [])]
    return next((n for n in names if n.lower() == own.lower()), own)


def _is_other_character(name: str) -> bool:
    """True when a logged-in player names a character that isn't theirs."""
    return (getattr(g, "role", None) == "player"
            and (name or "").strip().lower() != (g.character or "").lower())


def _render_login():
    return render_template("login.html", i18n=_load_i18n_json())


@app.before_request
def _auth_gate():
    g.role = None
    g.character = None
    if _lan_token is None:
        g.role = "dm"          # localhost-only mode: no login at all
        return None

    provided = request.headers.get("X-DND-Token", "")
    if not provided and request.endpoint == "media_file":
        provided = request.args.get("t", "")   # <img> can't send headers
    if provided and hmac.compare_digest(provided, _lan_token):
        g.role = "dm"
    elif _this_pc_is_dm():
        g.role = "dm"          # whatever cookie this browser holds from the players' door
    else:
        s = _session_from_request()
        if s:
            # Cookie-authenticated writes must come from our own pages.
            origin = request.headers.get("Origin", "")
            if (request.method not in ("GET", "HEAD", "OPTIONS") and origin
                    and urlparse(origin).netloc != request.host):
                return "Forbidden", 403
            g.role = s.get("role")
            g.character = s.get("character")

    ep = request.endpoint
    if ep is None or ep in _PUBLIC_ENDPOINTS:
        return None
    if g.role is None:
        if ep == "index":
            return _render_login()
        return "Login required", 401
    if ep in _DM_ENDPOINTS and g.role != "dm":
        return "Forbidden", 403
    return None


@app.route("/login/options")
def login_options():
    """What the login screen offers: characters with a PIN, and the DM entry.

    `me` is the caller's current login ("dm" / "player" / null), so a page whose
    session died can notice and go back to the login screen. `dm_url` is set
    for this PC at the players' door: the way back to the DM's screen.
    """
    if _lan_token is None:
        return "Not found", 404
    camp = _login_campaign()
    names = sorted(_accounts.load(camp)["characters"]) if camp else []
    return jsonify({"characters": names, "dm": _accounts.dm_record() is not None,
                    "me": g.role, "dm_url": _dm_url()})


@app.route("/login", methods=["POST"])
def login():
    """Body: {"character": "Mira", "pin": "123456"} or {"dm": true, "pin": "..."}."""
    if _lan_token is None:
        return "Not found", 404
    ip = request.remote_addr or "?"
    if not _rate_ok(ip):
        return jsonify({"error": "rate"}), 429
    data = request.get_json(silent=True) or {}
    pin = str(data.get("pin", "")).strip()
    as_dm = bool(data.get("dm"))
    camp = _login_campaign()

    if as_dm:
        char_key, rec, stored = "__dm__", _accounts.dm_record(), None
    else:
        acc = _accounts.load(camp) if camp else {"characters": {}}
        stored = _accounts.find_character(acc, str(data.get("character", ""))[:50])
        if not stored:
            return jsonify({"error": "unknown"}), 404
        char_key, rec = f"{camp}/{stored.lower()}", acc["characters"][stored]

    wait = _login_wait(ip, char_key)
    if wait:
        return jsonify({"error": "locked", "retry_after": wait}), 429
    if not _accounts.verify_pin(pin, rec):
        _login_failed(ip, char_key)
        print(f"[login] wrong PIN for {stored or 'DM'} from {ip}", flush=True)
        return jsonify({"error": "pin"}), 403

    sid = secrets.token_urlsafe(32)
    sess = {"role": "dm" if as_dm else "player", "character": stored,
            "campaign": camp, "created": _time.time()}
    with _sessions_lock:
        _sessions[_sid_key(sid)] = sess
    _persist_sessions()
    print(f"[login] {stored or 'DM'} logged in from {ip}", flush=True)

    target = "/" if as_dm else "/?char=" + quote(stored)
    resp = jsonify({"ok": True, "redirect": target})
    resp.set_cookie(_SESSION_COOKIE, sid, max_age=_SESSION_TTL, httponly=True,
                    samesite="Lax", secure=_TLS_MODE)
    return resp


@app.route("/logout", methods=["POST"])
def logout():
    sid = request.cookies.get(_SESSION_COOKIE, "")
    if sid:
        with _sessions_lock:
            _sessions.pop(_sid_key(sid), None)
        _persist_sessions()
    resp = jsonify({"ok": True})
    resp.delete_cookie(_SESSION_COOKIE)
    return resp


# ─── Character creation from the web (/crea) ─────────────────────────────────
# Public, no login: anyone can build a level-1 character with Claude asking the
# questions. char_create.py holds the conversation, the Claude call (no tools)
# and the sheet; these routes only add the HTTP edges and the rate limits.

import char_create as _create


def _same_origin() -> bool:
    origin = request.headers.get("Origin", "")
    return not origin or urlparse(origin).netloc == request.host


@app.route("/crea")
def crea_page():
    return render_template("crea.html", i18n=_load_i18n_json())


@app.route("/crea/start", methods=["POST"])
def crea_start():
    if not _same_origin():
        return "Forbidden", 403
    ip = request.remote_addr or "?"
    if _create.allow("convs", ip):
        return jsonify({"error": "limit"}), 429
    return jsonify(_create.public_view(_create.new_conv(ip)))


@app.route("/crea/conv/<cid>")
def crea_conv(cid):
    conv = _create.load_conv(cid)
    if not conv:
        return jsonify({"error": "unknown"}), 404
    return jsonify(_create.public_view(conv))


@app.route("/crea/msg", methods=["POST"])
def crea_msg():
    if not _same_origin():
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    conv = _create.load_conv(str(data.get("id", "")))
    if not conv or conv.get("finished"):
        return jsonify({"error": "unknown"}), 404
    text = str(data.get("text", "")).strip()
    if not text:
        return jsonify({"error": "empty"}), 400
    if len(text) > _create.MAX_MSG_LEN:
        return jsonify({"error": "long", "max": _create.MAX_MSG_LEN}), 400
    if sum(1 for m in conv["history"] if m["role"] == "player") >= _create.MAX_TURNS:
        return jsonify({"error": "turns"}), 429
    with _create._usage_lock:
        if conv["id"] in _create._busy:
            return jsonify({"error": "busy"}), 409
        _create._busy.add(conv["id"])
    try:
        if _create.allow("msgs", request.remote_addr or "?"):
            return jsonify({"error": "limit"}), 429
        try:
            out = _create.handle_message(conv, text)
        except Exception as e:
            print(f"[crea] {conv['id'][:8]}: {e}", file=sys.stderr)
            return jsonify({"error": "claude"}), 502
        return jsonify(out | {"view": _create.public_view(conv)})
    finally:
        with _create._usage_lock:
            _create._busy.discard(conv["id"])


@app.route("/crea/finish", methods=["POST"])
def crea_finish():
    if not _same_origin():
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    conv = _create.load_conv(str(data.get("id", "")))
    if not conv or not conv.get("sheet") or conv.get("finished"):
        return jsonify({"error": "unknown"}), 404
    pin = str(data.get("pin", "")).strip()
    player = re.sub(r"[^\w' -]", "", str(data.get("player", "")), flags=re.U).strip()[:40]
    try:
        _create.save_character(conv["sheet"], pin, player)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    conv["finished"] = True
    conv["player"] = player
    _create.save_conv(conv)
    print(f"[crea] new character from the web: {conv['sheet']['name']} — pending DM approval", file=sys.stderr)
    return jsonify({"ok": True, "name": conv["sheet"]["name"]})


# ─── Campaign creation from the web (/campagna) ──────────────────────────────
# DM only, behind its own password (camp_create.py set-password), on every
# device — this PC included. Claude asks the `/dm:dnd new` questions, then Opus
# writes the campaign files in a background thread; the page polls for it.

import camp_create as _camp


def _camp_authed() -> bool:
    return _camp.session_ok(request.cookies.get(_camp.COOKIE, ""))


def _camp_guard():
    """None when the caller may use /campagna, else the refusal to return."""
    if not _same_origin():
        return "Forbidden", 403
    if not _camp_authed():
        return jsonify({"error": "auth"}), 401
    return None


@app.route("/campagna")
def campagna_page():
    return render_template("campagna.html", i18n=_load_i18n_json(),
                           authed=_camp_authed(), configured=bool(_camp.password_record()))


@app.route("/campagna/login", methods=["POST"])
def campagna_login():
    if not _same_origin():
        return "Forbidden", 403
    ip = request.remote_addr or "?"
    wait = _camp.login_wait(ip)
    if wait:
        return jsonify({"error": "locked", "retry_after": wait}), 429
    pw = str((request.get_json(silent=True) or {}).get("password", ""))
    if not _camp.check_password(pw):
        _camp.login_failed(ip)
        print(f"[campagna] wrong password from {ip}", file=sys.stderr)
        return jsonify({"error": "password"}), 403
    resp = jsonify({"ok": True})
    resp.set_cookie(_camp.COOKIE, _camp.new_session(), max_age=_camp.SESSION_TTL, httponly=True,
                    samesite="Strict", secure=_TLS_MODE, path="/campagna")
    return resp


@app.route("/campagna/logout", methods=["POST"])
def campagna_logout():
    if not _same_origin():
        return "Forbidden", 403
    _camp.end_session(request.cookies.get(_camp.COOKIE, ""))
    resp = jsonify({"ok": True})
    resp.delete_cookie(_camp.COOKIE, path="/campagna")
    return resp


@app.route("/campagna/start", methods=["POST"])
def campagna_start():
    refused = _camp_guard()
    if refused:
        return refused
    return jsonify(_camp.public_view(_camp.new_conv()))


@app.route("/campagna/conv/<cid>")
def campagna_conv(cid):
    if not _camp_authed():
        return jsonify({"error": "auth"}), 401
    conv = _camp.load_conv(cid)
    if not conv:
        return jsonify({"error": "unknown"}), 404
    return jsonify(_camp.public_view(conv))


@app.route("/campagna/msg", methods=["POST"])
def campagna_msg():
    refused = _camp_guard()
    if refused:
        return refused
    data = request.get_json(silent=True) or {}
    conv = _camp.load_conv(str(data.get("id", "")))
    if not conv or conv.get("finished"):
        return jsonify({"error": "unknown"}), 404
    text = str(data.get("text", "")).strip()
    if not text:
        return jsonify({"error": "empty"}), 400
    if len(text) > _camp.MAX_MSG_LEN:
        return jsonify({"error": "long", "max": _camp.MAX_MSG_LEN}), 400
    if sum(1 for m in conv["history"] if m["role"] == "player") >= _camp.MAX_TURNS:
        return jsonify({"error": "turns"}), 429
    with _camp._gen_lock:
        if conv["id"] in _camp._busy or conv["id"] in _camp._generating:
            return jsonify({"error": "busy"}), 409
        _camp._busy.add(conv["id"])
    try:
        try:
            out = _camp.handle_message(conv, text)
        except Exception as e:
            print(f"[campagna] {conv['id'][:8]}: {e}", file=sys.stderr)
            return jsonify({"error": "claude"}), 502
        return jsonify(out | {"view": _camp.public_view(conv)})
    finally:
        with _camp._gen_lock:
            _camp._busy.discard(conv["id"])


@app.route("/campagna/generate", methods=["POST"])
def campagna_generate():
    refused = _camp_guard()
    if refused:
        return refused
    conv = _camp.load_conv(str((request.get_json(silent=True) or {}).get("id", "")))
    if not conv or not conv.get("brief") or conv.get("finished"):
        return jsonify({"error": "unknown"}), 404
    if conv["id"] in _camp._busy:
        return jsonify({"error": "busy"}), 409
    if _camp.folder_taken(conv["brief"]["folder"]):
        return jsonify({"error": "exists", "folder": conv["brief"]["folder"]}), 409
    if not _camp.start_generation(conv):
        return jsonify({"error": "busy"}), 409
    return jsonify({"view": _camp.public_view(conv)})


# Wire audio broadcast after _broadcast is defined (see bottom of file)
# — done lazily via set_broadcast() called after app is created.

# ─── Scene definitions ────────────────────────────────────────────────────────
# Each scene: keywords (weighted — more = higher priority hit),
# gradient colors [top, bottom], accent color, particle type, display label.

SCENES: dict[str, dict] = {
    "tavern": {
        "keywords": [
            "tavern", "inn", "guttered", "common room", "hearth",
            "fireplace", "ale", "mead", "barkeep", "innkeeper",
            "candle", "tallow", "flagon", "stool", "bar",
        ],
        "keywords_it": ['taverna', 'locanda', 'oste', 'ostessa', 'boccale', 'boccali', 'birra', 'idromele', 'bancone', 'focolare', 'avventori'],
        "colors": ["#1a0800", "#2e1400"],
        "accent": "#c8601a",
        "particles": "embers",
        "label": "The Inn",
    },
    "dungeon": {
        "keywords": [
            "dungeon", "corridor", "stone floor", "torch", "iron gate",
            "portcullis", "cell", "shackle", "pit", "dank",
        ],
        "keywords_it": ['segrete', 'corridoio', 'corridoi', 'cella', 'celle', 'catene', 'grata', 'saracinesca', 'prigione'],
        "colors": ["#080818", "#12082e"],
        "accent": "#6a3aaa",
        "particles": "dust",
        "label": "The Dungeon",
    },
    "mine": {
        "keywords": [
            "mine", "seam", "shaft", "tunnel", "ore", "pickaxe",
            "foreman", "deep seam", "ashstone", "cart", "vein",
        ],
        "keywords_it": ['miniera', 'minatore', 'minatori', 'galleria', 'filone', 'piccone', 'carrello'],
        "colors": ["#0a0a0a", "#1a1008"],
        "accent": "#806040",
        "particles": "dust",
        "label": "The Mine",
    },
    "cave": {
        "keywords": [
            "cave", "cavern", "stalactite", "stalagmite", "underground",
            "grotto", "dripping", "echo", "subterranean",
        ],
        "keywords_it": ['caverna', 'caverne', 'grotta', 'grotte', 'stalattiti', 'stalagmiti', 'antro', 'sotterraneo', 'sotterranei'],
        "colors": ["#0a1520", "#0a1030"],
        "accent": "#2060a0",
        "particles": "mist",
        "label": "The Cavern",
    },
    "forest": {
        "keywords": [
            "forest", "wood", "tree", "branch", "leaves", "undergrowth",
            "hollow wood", "canopy", "root", "bark", "moss", "fern",
            "thicket", "grove",
        ],
        "keywords_it": ['foresta', 'bosco', 'boschi', 'alberi', 'sottobosco', 'radura', 'querce'],
        "colors": ["#041008", "#081a04"],
        "accent": "#40a040",
        "particles": "leaves",
        "label": "The Forest",
    },
    "castle": {
        "keywords": [
            "castle", "rampart", "battlement", "keep", "parapet",
            "drawbridge", "moat", "throne", "great hall", "manor",
        ],
        "keywords_it": ['castello', 'bastioni', 'torrione', 'mastio', 'sala del trono', 'ponte levatoio', 'merlature'],
        "colors": ["#0e0e1a", "#1a1a2e"],
        "accent": "#8080c0",
        "particles": "dust",
        "label": "The Castle",
    },
    "mountain": {
        "keywords": [
            "mountain", "snow", "peak", "blizzard", "frost", "glacier",
            "avalanche", "ridge", "cliff", "altitude", "wind",
        ],
        "keywords_it": ['montagna', 'montagne', 'vetta', 'picco', 'neve', 'ghiacciaio', 'valico', 'tormenta'],
        "colors": ["#0a1020", "#1a2040"],
        "accent": "#a0c0e0",
        "particles": "snow",
        "label": "The Mountains",
    },
    "ocean": {
        "keywords": [
            "ocean", "sea", "ship", "wave", "sailor", "port", "harbour",
            "dock", "tide", "storm", "mast", "hull", "water",
        ],
        "keywords_it": ['mare', 'oceano', 'nave', 'onde', 'porto', 'molo', 'marinai', 'ciurma', 'vele'],
        "colors": ["#000d1a", "#001a33"],
        "accent": "#0060a0",
        "particles": "ripples",
        "label": "The Sea",
    },
    "desert": {
        "keywords": [
            "desert", "sand", "dune", "oasis", "scorching", "arid",
            "mirage", "camel", "sphinx",
        ],
        "keywords_it": ['deserto', 'sabbia', 'dune', 'oasi', 'arido'],
        "colors": ["#1a0f00", "#2e1a00"],
        "accent": "#c08030",
        "particles": "sand",
        "label": "The Desert",
    },
    "ruins": {
        "keywords": [
            "ruins", "ruin", "crumble", "crumbling", "rubble", "ancient",
            "overgrown", "collapsed", "forgotten", "desolate", "remnant",
        ],
        "keywords_it": ['rovine', 'ruderi', 'macerie'],
        "colors": ["#100e04", "#1e1a08"],
        "accent": "#806830",
        "particles": "dust",
        "label": "The Ruins",
    },
    "swamp": {
        "keywords": [
            "swamp", "marsh", "bog", "mud", "murky", "fetid", "reed",
            "mire", "sludge", "stagnant",
        ],
        "keywords_it": ['palude', 'paludi', 'acquitrino', 'fango', 'melma', 'canneti', 'stagno'],
        "colors": ["#080e04", "#0e1808"],
        "accent": "#406020",
        "particles": "mist",
        "label": "The Swamp",
    },
    "crypt": {
        "keywords": [
            "crypt", "tomb", "grave", "coffin", "undead", "bones",
            "skeleton", "lich", "mausoleum", "burial", "sarcophagus",
            "dead", "death",
        ],
        "keywords_it": ['cripta', 'tomba', 'tombe', 'sepolcro', 'bara', 'sarcofago', 'non morti', 'ossario'],
        "colors": ["#08000a", "#140014"],
        "accent": "#602060",
        "particles": "smoke",
        "label": "The Crypt",
    },
    "fire": {
        "keywords": [
            "fire", "flame", "burn", "blaze", "inferno", "conflagration",
            "ember", "char", "smoke", "ash cloud",
        ],
        "keywords_it": ['incendio', 'rogo', 'inferno', 'lava'],
        "colors": ["#1a0500", "#2e0800"],
        "accent": "#ff4400",
        "particles": "embers",
        "label": "The Fire",
    },
    "arcane": {
        "keywords": [
            "arcane", "magic", "spell", "enchant", "rune", "glyph",
            "mystical", "ritual", "incantation", "ward", "sigil",
            "thaumaturgy", "sorcery",
        ],
        "keywords_it": ['arcano', 'arcana', 'rune', 'glifi', 'portale', 'cerchio magico'],
        "colors": ["#080020", "#12003a"],
        "accent": "#8040ff",
        "particles": "sparks",
        "label": "The Arcane",
    },
    "city": {
        "keywords": [
            "city", "market", "street", "crowd", "village", "town",
            "square", "cobble", "district", "quarter", "merchant",
            "ashenveil",
        ],
        "keywords_it": ['città', 'mercato', 'strade', 'vicoli', 'piazza', 'villaggio', 'folla', 'bancarelle', 'quartiere'],
        "colors": ["#0a0f1a", "#15202e"],
        "accent": "#6080a0",
        "particles": "rain",
        "label": "The Town",
    },
    "night": {
        "keywords": [
            "night", "midnight", "moon", "star", "dark sky",
            "constellation", "celestial", "dusk", "twilight",
        ],
        "keywords_it": ['notte', 'mezzanotte', 'luna', 'stelle', 'crepuscolo'],
        "colors": ["#000008", "#04000f"],
        "accent": "#4060a0",
        "particles": "stars",
        "label": "The Night",
    },
    "temple": {
        # `lantern` and `cleric` REMOVED. Every other word here names a place
        # or a fixture of one -- a nave, an altar, incense. Those two named a
        # thing a character CARRIES and a thing a character IS, so they scored
        # wherever the party went: measured, three mentions of a lantern in a
        # room with no other keyword present rendered the room as THE TEMPLE,
        # and so did a cleric walking down a road.
        #
        # `prayer` is the weakest survivor and is kept: people pray in
        # foxholes, but it at least describes an act performed AT the place.
        "keywords": [
            "temple", "shrine", "altar", "holy", "sacred", "chapel",
            "prayer", "incense", "pew", "nave",
            "pale flame",
        ],
        "keywords_it": ['tempio', 'santuario', 'altare', 'cappella', 'preghiera', 'sacerdote', 'incenso', 'navata'],
        "colors": ["#0e0c18", "#1a1428"],
        "accent": "#c0a060",
        "particles": "smoke",
        "label": "The Temple",
    },
}

# Priority order — checked in sequence; first match wins per chunk
SCENE_PRIORITY = [
    "mine", "crypt", "arcane", "fire", "temple", "dungeon", "cave",
    "forest", "swamp", "castle", "ocean", "mountain", "desert", "ruins",
    "tavern", "city", "night",
]

# ─── ANSI / TUI chrome stripping ─────────────────────────────────────────────

class _ANSIState:
    """Character-by-character ANSI escape-sequence state machine.

    Regex approaches fail when the PTY delivers bytes one at a time, splitting
    sequences like \\x1b[4;2m across chunk boundaries.  This state machine
    carries its state across calls so cross-chunk splits are handled correctly.

    States
    ------
    normal   → emitting regular characters
    esc      → saw ESC (0x1B), waiting to see what kind of sequence follows
    csi      → inside CSI sequence (ESC [ … letter)
    osc      → inside OSC sequence (ESC ] … BEL or ST)
    osc_esc  → inside OSC, just saw ESC — might be the ST terminator (ESC \\)
    """

    __slots__ = ("_s",)

    def __init__(self) -> None:
        self._s: str = "normal"

    def feed(self, text: str) -> str:
        out: list[str] = []
        s = self._s
        for ch in text:
            c = ord(ch)
            if s == "normal":
                if c == 0x1B:
                    s = "esc"
                elif c >= 0x20 or c in (0x09, 0x0A):   # printable / tab / newline
                    out.append(ch)
                # else: other control char (bell, etc.) — discard
            elif s == "esc":
                if ch == "[":
                    s = "csi"
                elif ch == "]":
                    s = "osc"
                else:
                    s = "normal"    # 2-char ESC sequence; discard both bytes
            elif s == "csi":
                if 0x40 <= c <= 0x7E:   # final byte of CSI
                    s = "normal"
                elif c == 0x1B:         # unexpected ESC — start fresh
                    s = "esc"
                # else: parameter / intermediate byte, keep consuming
            elif s == "osc":
                if c == 0x07:           # BEL terminates OSC
                    s = "normal"
                elif c == 0x1B:
                    s = "osc_esc"
                # else: OSC payload, keep consuming
            elif s == "osc_esc":
                s = "normal" if ch == "\\" else "osc"
        self._s = s
        return "".join(out)


_ansi = _ANSIState()
_ansi_lock = threading.Lock()

_BOX_CHARS = set("╭╮╰╯│─┌┐└┘├┤┬┴┼━═║╔╗╚╝")
_BOX_CHAR_STRIP = "╭╮╰╯│─┌┐└┘├┤┬┴┼━═║╔╗╚╝"  # same set as string for str.strip()

# Characters used by Claude CLI spinner / prompt / UI
_SPINNER_CHARS = set("✽⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏◐◓◑◒◌◎●")
_PROMPT_STARTS = ("❯", ">", "·", "▸", "ℹ", "✓", "⚠", "✗", "⟳", "↳")


def _handle_cr(text: str) -> str:
    """Handle carriage returns the way a real terminal would.

    Two distinct cases:
      \\r\\n  — a real newline (\\r\\n line ending).  Normalise to \\n first
                so the content is preserved.
      bare \\r — cursor-to-column-0 for in-place token updates.  Claude CLI
                streams each token by rewriting the current line:
                  "The" → \\r"The Gut" → \\r"The Gutte" → …
                Keep only the last segment (= the final written state).
    """
    # Step 1: treat \\r\\n as a real newline — must come before bare-\\r logic
    text = text.replace("\r\n", "\n")

    # Step 2: handle remaining bare \\r (in-place rewrites)
    lines = text.split("\n")
    result = []
    for line in lines:
        if "\r" in line:
            parts = line.split("\r")
            result.append(parts[-1])   # last segment = final state of the line
        else:
            result.append(line)
    return "\n".join(result)


def _strip_ansi(text: str) -> str:
    text = _handle_cr(text)
    with _ansi_lock:
        text = _ansi.feed(text)
    return text


def _is_chrome(line: str) -> bool:
    """Return True for lines that are TUI chrome, not DM narration.

    The Claude CLI wraps responses in a box:
        ╭──────────────────╮
        │ narration text   │
        ╰──────────────────╯
    We strip the border characters from line edges first so that content
    lines like "│ The tavern smells of ale │" are NOT filtered — only pure
    border rows (all box chars, no letters) are treated as chrome.
    """
    stripped = line.strip()

    if not stripped:
        return False   # keep blank lines — they separate paragraphs

    # Strip leading/trailing box-drawing border chars to expose the real content.
    # "│ The tavern smells of ale │" → "The tavern smells of ale"
    content = stripped.strip(_BOX_CHAR_STRIP + " ")

    # If nothing remains, the line was entirely box-drawing chrome (a border row).
    if not content:
        return True

    # All remaining checks operate on content (without box border decoration).
    c = content

    # CLI prompt / spinner lines
    if c[0] in _SPINNER_CHARS:
        return True
    if c.startswith(_PROMPT_STARTS):
        return True

    # Common spinner word patterns (e.g. "Thinking…")
    if re.match(r"^[A-Z][a-z]+ing…?$", c):
        return True

    # Claude branding / metadata
    if "claude.ai" in c.lower():
        return True

    # Session-resume instructions emitted at end of response
    if c.startswith("Resume this session with:") or re.match(r"^claude\s+--resume\s+", c):
        return True

    # Status-bar patterns: cost, token counts, rate-limit bars
    # Note: "Tokens300/0" has no space — use \s* not \s+
    if re.search(r"Tokens\s*\d|5hr:|7d:|Session:|Total:\s*\$", c):
        return True

    # Model/plan header lines ("Sonnet 4.6", "Claude Pro", "Professional", etc.)
    if re.search(r"Sonnet|Haiku|Opus|Claude\s*(Pro|Max|Team|Code)\b|Professional\b|claude-\d", c, re.I):
        return True

    # Tool-use labels emitted by Claude CLI ("Bash command", "Read command", etc.)
    if re.match(r"^(Bash|Read|Write|Edit|Glob|Grep|WebFetch|WebSearch|TaskCreate|TaskUpdate|TaskGet|TaskList|NotebookEdit|Agent|ToolSearch|ExitPlanMode|EnterPlanMode|ScheduleWakeup|Monitor|RemoteTrigger|CronCreate|CronDelete|CronList|AskUserQuestion)(\s+(command|tool|result|call))?$", c, re.I):
        return True

    # Timestamp-prefixed lines ("3ts ago …", "2m ago …") — UI timestamps concatenated with content
    if re.match(r"^\d+\s*[smhdt]+s?\s*(ago\s*)?[A-Z]", c):
        return True

    # Bare numbers (token counts, cursor column positions, etc.)
    if re.match(r"^\d+$", c):
        return True

    # Single stray characters that are ANSI/escape remnants, not real words
    if len(c) == 1 and not c.isalpha():
        return True

    # Very short non-alpha fragments (≤3 chars with no letters = not narration)
    if len(c) <= 3 and not re.search(r"[a-zA-Z]{2}", c):
        return True

    return False


def _clean(text: str) -> str:
    text = _strip_ansi(text)
    lines = text.split("\n")
    kept = []
    for line in lines:
        if _is_chrome(line):
            continue
        # Strip box-border chars from edges so content reaches the browser clean.
        s = line.strip().strip(_BOX_CHAR_STRIP + " ")
        # Blank line → preserve as paragraph separator
        kept.append(s if s else "")
    # Collapse runs of more than two consecutive blank lines
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(kept))
    return result


# ─── Scene detection ──────────────────────────────────────────────────────────

_current_scene_name: str = "tavern"   # default — we start in the inn
_scene_buffer: list[str] = []
_BUFFER_WINDOW = 20   # analyse last N cleaned chunks together

# The DM names the scene (push_stats.py --scene) — that wins over guessing
# from keywords, which then stays off until the display restarts. Persisted
# so a restart keeps the music of where the party is.
_SCENE_FILE = rt("scene.txt")
_scene_pinned = False
try:
    _saved_scene = open(_SCENE_FILE, encoding="utf-8").read().strip()
    if _saved_scene in SCENES:
        _current_scene_name, _scene_pinned = _saved_scene, True
except OSError:
    pass


def _scene_payload(name: str) -> dict:
    return {k: v for k, v in SCENES[name].items() if not k.startswith("keywords")} | {"name": name}


# How much evidence a scene change needs. A bare `max()` over keyword counts
# meant ONE stray keyword could move the background, and two plausible scenes
# traded it back and forth on a one-point edge.
#
# MARGIN is the gap a challenger must open over the scene already on screen.
# The INCUMBENT BONUS lets the current scene defend itself even after its own
# keywords have scrolled out of the window, which is the usual case for a long
# scene: a fight in a crypt stops saying "crypt" after the first line.
#
# The bonus is 1 rather than 2 by measurement against this repo's own keyword
# lists, not by feel. A genuine transition narrated over three chunks --
# arriving at a city -- scores 4; incidental noise, a couple of night words
# while at sea, scores 2. With MARGIN 2 an aged-out incumbent needs the
# challenger at 3, which admits the real change and blocks the noise. At 2 the
# bar becomes 4 and a genuine arrival stops registering.
#
# There is deliberately NO separate minimum-hits floor. One was written here
# first and it could never fire: the test below requires
# `best >= scores.get(current, 0) + BONUS + MARGIN`, which is at least 3, so a
# floor of 2 is unreachable for every input. A constant that decides nothing
# reads as protection and is not, so it is gone rather than documented.
_SCENE_SWITCH_MARGIN = 2    # a challenger must beat the scene on screen by this
_SCENE_INCUMBENT_BONUS = 1


def _keyword_pattern(keyword: str) -> "re.Pattern":
    r"""Match `keyword` as a whole word where the script has word delimiters.

    Scored with `window.count(kw)` before this, which is a SUBSTRING count:
    the arcane keyword `ward` matched inside "deputy warden", so a prison
    fight scored as arcane. It also matches "wardrobe", "forward", "steward".
    In Italian it matched "ore" (mine) inside "signore", "port" (ocean) inside
    "porta" and "ale" (tavern) inside "quale".

    Anchors are applied only for keywords in a script that separates words. A
    campaign may override a scene's keyword list in its own language (the lists
    shipped here are English), and `\b` is an impossible condition for Han,
    Kana, Hangul or Thai -- see scripts/script_ranges.py.
    """
    return script_ranges.whole_word_pattern(keyword)


@_functools.lru_cache(maxsize=512)
def _cached_keyword_pattern(keyword: str) -> "re.Pattern":
    return _keyword_pattern(keyword)


def _detect_scene(text: str) -> Optional[dict]:
    global _current_scene_name, _scene_buffer

    if _scene_pinned:
        return None

    _scene_buffer.append(text.lower())
    if len(_scene_buffer) > _BUFFER_WINDOW:
        _scene_buffer.pop(0)

    window = " ".join(_scene_buffer)

    scores: dict[str, int] = {}
    for scene_name in SCENE_PRIORITY:
        scene = SCENES[scene_name]
        score = sum(len(_cached_keyword_pattern(kw).findall(window))
                    for kw in scene["keywords"] + scene.get("keywords_it", []))
        if score > 0:
            scores[scene_name] = score

    if not scores:
        return None

    best = max(scores, key=lambda k: scores[k])
    if best == _current_scene_name:
        return None   # no change
    # `_current_scene_name` may not be in `scores` at all once its keywords
    # have scrolled out of the window -- that is the case the bonus is for.
    held = scores.get(_current_scene_name, 0) + _SCENE_INCUMBENT_BONUS
    if scores[best] - held < _SCENE_SWITCH_MARGIN:
        return None

    _current_scene_name = best
    return _scene_payload(best)


# ─── SSE client registry ─────────────────────────────────────────────────────

_clients: list[queue.Queue] = []
_clients_lock = threading.Lock()
# Maps a connected SSE client (queue) → the character it's bound to, if any.
# Phones connect to /stream?character=<name>; the main display has no character.
# Lets a dice-request know whether a target PC has a live phone (→ route there)
# or not (→ open the on-screen roller). Guarded by _clients_lock.
_client_chars: "dict[queue.Queue, str]" = {}


def _phone_present(char: str) -> bool:
    """True if some connected phone is bound to this character (case-insensitive)."""
    c = (char or "").strip().lower()
    if not c:
        return False
    with _clients_lock:
        return c in _client_chars.values()

# ─── Text replay log ──────────────────────────────────────────────────────────
# Stores cleaned text chunks so late-connecting browsers can catch up.
# Persisted to LOG_FILE so it survives Flask restarts (Chromecast reconnects, new sessions).
# Full body retention (player requirement, 2026-08-18): cap raised 60 → 2000 with
# full reload at startup (_load_log), so cross-session body text stays recoverable
# and archivable in both formats. On plugin updates, re-check these three spots.
_text_log: deque = deque(maxlen=2000)
_text_log_lock = threading.Lock()

# ─── Session tail buffer ──────────────────────────────────────────────────────
# Rolling buffer of the last 30 text events — written to session_tail.json after
# every /chunk POST so it survives crashes. Read at /dnd load for display replay.
# Path is campaign-specific so tails from different campaigns don't overwrite each other.
#
# ROBUSTNESS GUARANTEES (after the 2026-05-01 wipe-bug fix):
#   1. _load_tail is NON-DESTRUCTIVE: it never wipes the in-memory buffer based
#      on an empty/filtered-out load. If the file is empty, missing, or every
#      entry is filtered out by campaign mismatch, the existing buffer stays.
#   2. _persist_tail SKIPS ON EMPTY: it never overwrites an existing non-empty
#      file with an empty buffer. This breaks the "filter zeros buffer →
#      persist writes [] → file lost" failure chain.
#   3. _persist_tail uses ATOMIC WRITES: writes to a tempfile and atomically
#      renames into place, so a partial/crashed write can never produce a
#      truncated or zero-byte file.
#   4. The legacy fallback path is GONE. Tails only ever land in the campaign-
#      specific file. If CAMP_FILE is missing/empty when persist would fire,
#      we keep the buffer in memory and skip the write rather than dropping
#      events into a shared file that bleeds across campaigns.
_tail_buffer: deque = deque(maxlen=30)
_tail_lock   = threading.Lock()


def _get_tail_file() -> "str | None":
    """Return the campaign-specific tail path, or None if no campaign is set.

    Previously this fell back to a process-local path on the skill side. That
    fallback caused tail bleed across campaigns and made the wipe-on-load bug
    much harder to diagnose. The new contract: campaign-specific or nothing.
    """
    try:
        camp = open(CAMP_FILE, encoding="utf-8").read().strip()
        if camp:
            return str(_find_campaign(camp) / "session_tail.json")
    except Exception:
        pass
    return None


def _persist_tail() -> None:
    """Write _tail_buffer to disk. Refuses to overwrite content with empty.

    Atomic-write guarantee: writes to <path>.tmp then renames, so observers
    (the next /dnd load reading the file) never see a partial or zero-byte
    state.
    """
    path = _get_tail_file()
    if not path:
        # No active campaign — keep the buffer in memory, skip disk.
        return
    try:
        with _tail_lock:
            data = list(_tail_buffer)
        # Skip-on-empty guard: never blank a file that currently has content.
        if not data and os.path.exists(path):
            try:
                if os.path.getsize(path) > 2:  # 2 bytes = "[]"
                    print(f"_persist_tail: skipping empty write — {path} has content",
                          file=sys.stderr)
                    return
            except OSError:
                pass
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    except Exception as e:
        print(f"_persist_tail: write failed: {e}", file=sys.stderr)


def _load_tail() -> None:
    """Load tail from disk into the buffer. NON-DESTRUCTIVE on empty/mismatch.

    Old behavior: cleared the buffer first, then re-appended filtered entries.
    This created the wipe bug: if every entry was filtered out (campaign
    mismatch) the buffer ended up empty and the next persist wrote [] to disk.

    New behavior: build the candidate buffer first, then ONLY swap it into
    place if at least one entry survived filtering. If nothing survives, the
    in-memory buffer is left alone — preserves whatever was already loaded.
    """
    path = _get_tail_file()
    if not path:
        return
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return  # No file yet — keep in-memory state
    except (OSError, json.JSONDecodeError) as e:
        print(f"_load_tail: read failed for {path}: {e}", file=sys.stderr)
        return
    if not isinstance(data, list):
        print(f"_load_tail: file content is not a list — leaving buffer alone",
              file=sys.stderr)
        return

    try:
        current_camp = open(CAMP_FILE, encoding="utf-8").read().strip()
    except Exception:
        current_camp = ""

    candidate: list = []
    for item in data[-30:]:
        if not isinstance(item, dict):
            continue
        item_camp = item.get("_camp", "")
        # If we know the campaign and the entry stamps a different campaign,
        # skip it. Entries with no stamp are kept (legacy data + tolerance).
        if current_camp and item_camp and item_camp != current_camp:
            continue
        candidate.append(item)

    if not candidate:
        # Loaded data filtered down to nothing. DO NOT replace the buffer —
        # this is the wipe-bug guard.
        return

    with _tail_lock:
        _tail_buffer.clear()
        for item in candidate:
            _tail_buffer.append(item)


_load_tail()


def _persist_log() -> None:
    """Write the current text log to disk. Called after every chunk."""
    try:
        with _text_log_lock:
            data = list(_text_log)
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def _load_log() -> None:
    """Load a previously persisted text log on startup.
    Handles both old string format and new dict format."""
    try:
        with open(LOG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        with _text_log_lock:
            _text_log.clear()
            for item in data[:]:
                # Migrate old plain-string entries to dict format
                if isinstance(item, str):
                    item = {"text": item}
                _text_log.append(item)
    except Exception:
        pass


_load_log()


# ─── Character / combat stats ─────────────────────────────────────────────────
# Stored as {"players": [...], "turn_order": {...}|null}
# Players are merged by name so partial updates (just HP, just XP) work.

_current_stats: dict = {}
_stats_lock = threading.Lock()


def _persist_stats() -> None:
    try:
        with _stats_lock:
            data = dict(_current_stats)
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def _load_stats() -> None:
    global _expected_count
    try:
        with open(STATS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        with _stats_lock:
            _current_stats.update(data)
        # Initialise expected player count from persisted stats so solo-mode
        # detection is correct immediately after restart, without waiting for
        # the next /stats POST.
        loaded_players = data.get("players", [])
        if loaded_players:
            _expected_count = max(1, len(loaded_players))
    except Exception:
        pass


_load_stats()


# ─── Player input queue ───────────────────────────────────────────────────────
# Stores actions submitted from the display companion (iPad etc.) until the DM
# triggers the next turn. Drained by check_input.py via /player-input/drain.

_input_queue: list[dict] = []
_input_lock = threading.Lock()

# Pending DM-issued dice requests: request_id → {chars: set[str], meta: {...}, started_at: float}
# A request is "complete" when its chars set is empty (every prescribed player rolled).
# send.py --wait polls GET /dice-request/<id> to know when the DM can move on.
_dice_pending: dict = {}
_dice_pending_lock = threading.Lock()
# What each prescribed character rolled, by request: request_id → {label, dc,
# results: [...]}, kept after the request is complete so send.py --wait can
# print the numbers. Bounded: the oldest requests are forgotten first.
_dice_results: dict = {}
_DICE_RESULTS_MAX = 100


def _dice_results_entry(request_id: str):
    """The results gathered for a request, or None. Call with _dice_pending_lock held."""
    entry = _dice_results.get(request_id)
    if entry is None:
        return None
    out = {"label": entry["label"], "dc": entry["dc"], "results": [dict(r) for r in entry["results"]]}
    if entry.get("helping"):
        out["helping"] = list(entry["helping"])     # asked for this check, helped someone instead (the Help action)
    return out


def _dice_pending_snapshot() -> list:
    with _dice_pending_lock:
        return [
            {"request_id": rid, "pending": sorted(e["chars"]), "label": e["meta"].get("label", ""),
             "helped": sorted((e["meta"].get("help") or {}).keys())}
            for rid, e in _dice_pending.items() if e["chars"]
        ]


def _load_input_queue() -> None:
    global _input_queue
    try:
        with open(INPUT_FILE, encoding="utf-8") as f:
            _input_queue = json.load(f)
    except Exception:
        _input_queue = []


def _persist_input_queue() -> None:
    try:
        with open(INPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(_input_queue, f)
    except Exception:
        pass


_load_input_queue()


def _broadcast(payload: dict) -> None:
    with _clients_lock:
        dead = []
        for q in _clients:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _clients.remove(q)
            _client_chars.pop(q, None)


# ─── Routes ──────────────────────────────────────────────────────────────────

I18N_DIR = os.path.join(_HERE, "i18n")


def _load_i18n_json() -> str:
    """All display translations as one JSON object, {lang_code: {key: text}}.

    One file per language in display/i18n/<code>.json — adding a language is
    dropping a file there. Read on every page load, so wording edits show up on
    reload without restarting the server. A malformed file is skipped (the page
    falls back to English, then to the raw key) instead of breaking the display.
    The result is embedded in a <script>, so "</" is escaped.
    """
    langs = {}
    try:
        names = sorted(os.listdir(I18N_DIR))
    except OSError:
        names = []
    for name in names:
        code, ext = os.path.splitext(name)
        if ext != ".json":
            continue
        try:
            data = json.loads(_read_text(Path(I18N_DIR, name)))
        except (OSError, ValueError) as exc:
            print(f"[i18n] skipping {name}: {exc}", file=sys.stderr)
            continue
        if isinstance(data, dict):
            langs[code.lower()] = {k: v for k, v in data.items() if isinstance(v, str)}
    return json.dumps(langs, ensure_ascii=False).replace("</", "<\\/")


@app.route("/")
def index():
    # A player's phone always opens bound to their own character.
    if g.role == "player":
        asked = request.args.get("char") or request.args.get("character")
        wants_input = request.args.get("view") == "input" or asked is not None
        if wants_input and _is_other_character(asked or ""):
            return redirect("/?char=" + quote(g.character or ""))
    # The token is never written into the page: in LAN mode browsers
    # authenticate as this PC (loopback) or with their login cookie.
    return render_template(
        "index.html",
        lan_token="",
        login_role=(g.role or "") if (_lan_token is not None and not _this_pc_is_dm()) else "",
        login_char=(g.character or "") if g.role == "player" else "",
        # This PC: the DM's page links to the players' door, and back.
        player_url=_player_url(),
        dm_url=_dm_url(),
        narrator_voice=_read_narrator_voice(),
        tts_available=(_tts is not None),
        tts_voices=(_tts.voices() if _tts else {"male": [], "female": []}),
        tts_engine=(_tts.engine() if _tts else ""),
        music_catalog=(_music.catalog() if _music else {"scenes": {}, "ambience": {}}),
        i18n=_load_i18n_json(),
    )


@app.route("/icons/<path:filename>")
def serve_icon(filename):
    """Serve icons, favicon, and brand assets from display/icons/."""
    _icons_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
    return send_from_directory(_icons_dir, filename)


@app.route("/favicon.ico")
def favicon():
    _icons_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
    return send_from_directory(_icons_dir, "favicon.ico",
                               mimetype="image/vnd.microsoft.icon")


# ─── Installable display (PWA) ───────────────────────────────────────────────
# A phone adds the display to its home screen and opens it full screen, like an
# app. The manifest and the service worker are public (the login page links
# them too) and carry no game data. The worker caches nothing: it only answers
# a page load that cannot reach the display with a small "the table is not
# answering" page that retries by itself. Over plain-HTTP LAN addresses browsers
# refuse service workers; the display still works, it just cannot be installed.
PWA_THEME = "#0f0c16"
_PWA_KEYS = ("pwa.name", "pwa.shortName", "pwa.offlineTitle", "pwa.offlineText", "pwa.retry")


def _pwa_strings() -> dict:
    """The manifest's and the offline page's words, in the browser's language."""
    try:
        langs = json.loads(_load_i18n_json().replace("<\\/", "</"))
    except ValueError:
        langs = {}
    lang = request.accept_languages.best_match(sorted(langs)) or "en"
    out = {k: (langs.get(lang) or {}).get(k) or (langs.get("en") or {}).get(k) or k for k in _PWA_KEYS}
    out["lang"] = lang
    return out


@app.route("/manifest.webmanifest")
def pwa_manifest():
    s = _pwa_strings()
    manifest = {
        "name": s["pwa.name"], "short_name": s["pwa.shortName"], "lang": s["lang"],
        "id": "/", "start_url": "/", "scope": "/", "display": "standalone", "orientation": "any",
        "background_color": PWA_THEME, "theme_color": PWA_THEME,
        "icons": [
            {"src": "/icons/app_icon_192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/icons/app_icon_512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/icons/app_icon_maskable_512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    resp = Response(json.dumps(manifest, ensure_ascii=False), mimetype="application/manifest+json")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


_PWA_WORKER = """'use strict';
// The display's service worker: nothing is cached; a page that cannot reach the
// display gets a small offline page that tries again by itself.
const OFFLINE = %s;
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', e => {
  if (e.request.mode !== 'navigate') return;
  e.respondWith(fetch(e.request).catch(() =>
    new Response(OFFLINE, { headers: { 'Content-Type': 'text/html; charset=utf-8' } })));
});
"""

_PWA_OFFLINE = """<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="{theme}"><title>{title}</title>
<style>html,body{{margin:0;height:100%;background:{theme};color:#ECE4D2;font-family:system-ui,sans-serif}}
main{{min-height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;padding:24px;box-sizing:border-box;text-align:center}}
img{{width:120px;height:120px}}h1{{margin:0;font-size:24px}}p{{margin:0;max-width:30em;color:#b9ab92;font-size:17px;line-height:1.4}}
button{{margin-top:8px;padding:12px 26px;border-radius:999px;border:0;background:#f2b544;color:#1a140c;font-size:17px;font-weight:700}}</style>
</head><body><main><img src="/icons/app_icon_192.png" alt=""><h1>{title}</h1><p>{text}</p>
<button type="button" onclick="location.reload()">{retry}</button></main>
<script>setInterval(function(){{fetch('/ping',{{cache:'no-store'}}).then(function(r){{if(r.ok)location.reload();}}).catch(function(){{}});}},8000);</script>
</body></html>"""


@app.route("/sw.js")
def pwa_sw():
    from html import escape
    s = _pwa_strings()
    page = _PWA_OFFLINE.format(lang=escape(s["lang"]), theme=PWA_THEME, title=escape(s["pwa.offlineTitle"]),
                               text=escape(s["pwa.offlineText"]), retry=escape(s["pwa.retry"]))
    resp = Response(_PWA_WORKER % json.dumps(page, ensure_ascii=False).replace("</", "<\\/"),
                    mimetype="text/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@app.route("/srd-lookup")
def srd_lookup():
    """Look up a spell, item, condition, feature, or monster by name.

    Query params:
        name      — the name to look up (required)
        category  — spell | item | equipment | magic_item | condition | monster | feature (optional)
        level     — character level (1–20); collapses scale progressions to the matching entry
        lang      — display language (e.g. "it"); adds the translated card when one exists

    Returns JSON: {"found": bool, "name": str, "category": str, "text": str,
                   "translation": {"lang": str, "text": str}}  (translation only when available)
    """
    name     = request.args.get("name", "").strip()[:120]
    category = request.args.get("category", "").strip().lower() or None
    lang     = request.args.get("lang", "").strip().lower()[:8]
    level_s  = request.args.get("level", "").strip()
    level    = int(level_s) if level_s.isdigit() and 1 <= int(level_s) <= 20 else None
    if not name:
        return jsonify({"found": False, "error": "name required"}), 400
    if not _SRD_AVAILABLE or _lookup is None:
        return jsonify({"found": False, "error": "SRD dataset not loaded"}), 503

    text = _lookup.lookup_with_level(name, category=category, level=level)
    if text:
        rec = _lookup.lookup_record(name, category=category)
        resolved_cat = (rec or {}).get("_cat", category or "")
        out = {"found": True, "name": name, "category": resolved_cat, "text": text}
        try:
            tr = _lookup.lookup_translated(name, lang, category=category, level=level)
        except Exception:
            tr = None  # a broken translation must never hide the original
        if tr:
            out["translation"] = {"lang": lang, "text": tr}
        return jsonify(out)
    # Not found — offer near-miss "did you mean?" suggestions (typo recovery)
    # plus the wikidot fallback URL so the frontend can still link out.
    # `ref` is {} when there is no VERIFIED destination for this category, and
    # the frontend renders no link at all in that case: a guessed URL reads as
    # an answer and dead-ends, which is worse than saying nothing.
    ref = _lookup.reference_url(name, category=category)
    suggestions = []
    try:
        for sg_name, sg_cat in _lookup.suggest(name, category=category, n=3):
            suggestions.append({"name": sg_name, "category": sg_cat})
    except Exception:
        pass  # suggestion is best-effort; never fail the lookup over it
    return jsonify({"found": False, "name": name,
                    "reference_url": ref.get("url", ""),
                    "reference_label": ref.get("label", ""),
                    # kept so an older cached frontend still gets a link
                    "wikidot_url": ref.get("url", ""),
                    "suggestions": suggestions})


@app.route("/ping")
def ping():
    return "ok", 200


@app.route("/health")
def health():
    """Server-side integrity probe used by send.py --verify and external monitors.

    Returns the live counts the send-side cares about:
      - alive: always True if the route runs
      - tail_buffer: number of entries currently in the rolling tail
      - tail_file_size: size in bytes of the on-disk session_tail.json
      - text_log: number of entries in the replay log
      - campaign: the active campaign name (empty if none set)
      - clients: connected SSE clients

    No auth required — this is a liveness/monitoring endpoint, no PII or game
    content is exposed.
    """
    try:
        camp = open(CAMP_FILE, encoding="utf-8").read().strip()
    except Exception:
        camp = ""
    tail_path = _get_tail_file()
    try:
        tail_size = os.path.getsize(tail_path) if tail_path and os.path.exists(tail_path) else 0
    except OSError:
        tail_size = 0
    with _tail_lock:
        tail_count = len(_tail_buffer)
    with _text_log_lock:
        log_count = len(_text_log)
    with _clients_lock:
        client_count = len(_clients)
    return {
        "alive": True,
        "tail_buffer": tail_count,
        "tail_file_size": tail_size,
        "tail_path": tail_path or "",
        "text_log": log_count,
        "campaign": camp,
        "clients": client_count,
    }, 200


def _only_fields(data: dict) -> dict:
    """{"only": [names], "only_label": group} when a piece is addressed to some characters, else {}."""
    only = data.get("only")
    if not isinstance(only, list):
        return {}
    names = [str(n).strip()[:48] for n in only if str(n).strip()][:12]
    if not names:
        return {}
    out = {"only": names}
    label = str(data.get("only_label") or "").strip()[:40]
    if label:
        out["only_label"] = label
    return out


@app.route("/chunk", methods=["POST"])
def chunk():
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    raw = data.get("text", "")
    if not raw:
        return "", 204

    is_action      = bool(data.get("action"))
    is_player      = bool(data.get("player"))
    is_npc         = bool(data.get("npc"))
    is_dice        = bool(data.get("dice"))
    is_tutor       = bool(data.get("tutor"))
    is_inspiration = bool(data.get("inspiration_award"))
    is_milestone_award = bool(data.get("milestone_award"))
    is_milestone_spend = bool(data.get("milestone_spend"))
    is_xp_award    = bool(data.get("xp_award"))

    # ── Milestone award/spend (stack-based reward, distinct from binary Inspiration) ──
    if is_milestone_award or is_milestone_spend:
        name = str(data.get("milestone_award") or data.get("milestone_spend") or "").strip()[:80]
        label = str(data.get("label") or "Milestone").strip()[:40]
        if not name:
            return "", 204
        payload: dict = {
            "milestone_award" if is_milestone_award else "milestone_spend": name,
            "label": label,
            "text": name,
        }
        log_entry: dict = dict(payload)
        if is_milestone_award and data.get("reason"):
            payload["reason"] = str(data["reason"]).strip()[:240]
            log_entry["reason"] = payload["reason"]
        with _text_log_lock:
            _text_log.append(log_entry)
        with _tail_lock:
            _tail_buffer.append(log_entry)
        _persist_log()
        _persist_tail()
        _broadcast(payload)
        return "", 204

    # Inspiration and XP awards carry no text from stdin — build synthetic text.
    if is_inspiration:
        name = str(data.get("inspiration_award", "")).strip()[:80]
        if not name:
            return "", 204
        reason = str(data.get("reason", "")).strip()[:240]
        payload: dict = {"inspiration_award": name, "text": name}
        log_entry: dict = {"inspiration_award": name, "text": name}
        if reason:
            payload["reason"] = reason
            log_entry["reason"] = reason
        with _text_log_lock:
            _text_log.append(log_entry)
        with _tail_lock:
            _tail_buffer.append(log_entry)
        _persist_log()
        _persist_tail()
        _broadcast(payload)
        # Also update player inspiration state in stats.
        # NOTE: _persist_stats() acquires _stats_lock internally — capture the snapshot
        # inside the lock, then call persist/broadcast OUTSIDE to avoid deadlock.
        stats_snapshot = None
        with _stats_lock:
            players = _current_stats.setdefault("players", [])
            match = next((p for p in players if p.get("name", "").lower() == name.lower()), None)
            if match:
                match["inspiration"] = True
                stats_snapshot = dict(_current_stats)
        if stats_snapshot is not None:
            _persist_stats()
            _broadcast({"stats": stats_snapshot})
        return "", 204

    if is_xp_award:
        xp_data = data.get("xp_award", {})
        if not isinstance(xp_data, dict):
            return "", 204
        payload = {"xp_award": xp_data, "text": xp_data.get("summary", "")}
        log_entry = {"xp_award": xp_data, "text": xp_data.get("summary", "")}
        with _text_log_lock:
            _text_log.append(log_entry)
        with _tail_lock:
            _tail_buffer.append(log_entry)
        _persist_log()
        _persist_tail()
        _broadcast(payload)
        return "", 204

    # Player/npc/dice/tutor/action text comes from send.py (no ANSI/chrome) — light clean only.
    # DM narration may come from wrapper.py — full clean.
    cleaned = raw.strip() if (is_action or is_player or is_npc or is_dice or is_tutor) else _clean(raw)
    if not cleaned.strip():
        return "", 204

    payload: dict = {"text": cleaned}

    if is_action:
        payload["action"] = data["action"]
    elif is_player:
        payload["player"] = data["player"]
    elif is_npc:
        payload["npc"] = data["npc"]
    elif is_dice:
        payload["dice"] = True
    elif is_tutor:
        payload["tutor"] = True
    else:
        # Scene detection only on DM narration
        scene = _detect_scene(cleaned)
        if scene:
            payload["scene"] = scene
            if _audio:
                _audio.on_scene_change(scene["name"])
        # SFX scan on all non-player text (combat effects need the tracker on)
        if _audio:
            _audio.on_text(cleaned, combat=_in_combat())

    # Store full typed payload so replay preserves action/player/npc/dice/tutor context
    log_entry: dict = {"text": cleaned}
    if is_action:
        log_entry["action"] = data["action"]
    elif is_player:
        log_entry["player"] = data["player"]
    elif is_npc:
        log_entry["npc"] = data["npc"]
    elif is_dice:
        log_entry["dice"] = True
    elif is_tutor:
        log_entry["tutor"] = True

    # A split party: this piece is for some characters only (send.py --group / --only).
    audience = _only_fields(data)
    payload.update(audience)
    log_entry.update(audience)

    # Stamp campaign on tail entries to prevent bleed when switching campaigns
    try:
        _camp_stamp = open(CAMP_FILE, encoding="utf-8").read().strip()
        if _camp_stamp:
            log_entry["_camp"] = _camp_stamp
    except Exception:
        pass

    with _text_log_lock:
        _text_log.append(log_entry)
    with _tail_lock:
        _tail_buffer.append(log_entry)

    _persist_log()
    _persist_tail()
    _broadcast(payload)
    return "", 204


# ─── Campaign images (image_gen.py / map_render.py) ──────────────────────────
# Files live in <campaign>/media/. Only the ACTIVE campaign's folder is served,
# and names must match the same allowlist media.py writes with.
_MEDIA_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.(?:png|jpe?g|webp|svg)$")
_IMAGE_KINDS = {"portrait", "monster", "scene", "action", "item", "map", "region"}


def _active_campaign() -> str:
    try:
        with open(CAMP_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _media_dir() -> "str | None":
    camp = _active_campaign()
    camp = re.sub(r"[^A-Za-z0-9_-]", "", camp)[:50]
    return str(_find_campaign(camp) / "media") if camp else None


@app.route("/media/<name>")
def media_file(name):
    # <img> tags cannot send headers: LAN mode authenticates them by login
    # cookie, or by ?t=<token> (checked in _auth_gate).
    if not _token_ok():
        return "Forbidden", 403
    d = _media_dir()
    if not d or not _MEDIA_NAME_RE.match(name):
        return "Not found", 404
    return send_from_directory(d, name, max_age=86400)


def _clear_map() -> None:
    """The battle maps sent so far leave the Map panel (they stay in the story).

    A line of the log, so a screen that loads later, or the replay of the
    session tail, puts the maps away at the same point."""
    log_entry: dict = {"map_clear": True}
    if _active_campaign():
        log_entry["_camp"] = _active_campaign()
    with _text_log_lock:
        _text_log.append(log_entry)
    with _tail_lock:
        _tail_buffer.append(log_entry)
    _persist_log()
    _persist_tail()
    _broadcast({"map_clear": True})


@app.route("/map-clear", methods=["POST"])
def map_clear():
    """map_render.py --clear: no battle map in the Map panel until the next one."""
    if not _token_ok():
        return "Forbidden", 403
    _clear_map()
    return "", 204


@app.route("/image", methods=["POST"])
def image():
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    name = str(data.get("file", "")).strip()
    d = _media_dir()
    if not d or not _MEDIA_NAME_RE.match(name) or not os.path.isfile(os.path.join(d, name)):
        return "Unknown media file", 400
    kind = str(data.get("kind", "")).strip().lower()
    img = {
        "file": name,
        "kind": kind if kind in _IMAGE_KINDS else "scene",
        "caption": str(data.get("caption", "")).strip()[:240],
        "subject": str(data.get("subject", "")).strip()[:80],
    }
    audience = _only_fields(data)
    log_entry: dict = {"image": img, **audience}
    if _active_campaign():
        log_entry["_camp"] = _active_campaign()
    with _text_log_lock:
        _text_log.append(log_entry)
    with _tail_lock:
        _tail_buffer.append(log_entry)
    _persist_log()
    _persist_tail()
    _broadcast({"image": img, **audience})
    return "", 204


# ─── Handouts (handout.py) ───────────────────────────────────────────────────
# Documents the party finds — a letter, a notice, a journal page, a scroll, a
# scrap of paper, or an image from <campaign>/media/. Shown in the story as a
# sheet of paper and kept in <campaign>/handouts.json so the players can read
# them again from the Documents list, this session and the next.

_HANDOUT_KINDS = ("letter", "notice", "journal", "scroll", "note")
_HANDOUT_TITLE_MAX, _HANDOUT_BODY_MAX, _HANDOUT_KEEP = 120, 6000, 300
_handouts_lock = threading.Lock()


def _handouts_file() -> str:
    """The active campaign's handouts.json; the runtime dir when no campaign is loaded."""
    camp = re.sub(r"[^A-Za-z0-9_-]", "", _active_campaign())[:50]
    if camp:
        try:
            return str(_find_campaign(camp) / "handouts.json")
        except Exception:
            pass
    return rt("handouts.json")


def _read_handouts() -> list:
    try:
        with open(_handouts_file(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return [h for h in data if isinstance(h, dict) and h.get("id")] if isinstance(data, list) else []


def _write_handouts(items: list) -> None:
    path = _handouts_file()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items[-_HANDOUT_KEEP:], f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _show_handout(entry: dict) -> None:
    """Put a handout in the story (and its replay) and on every screen."""
    log_entry: dict = {"handout": entry}
    if _active_campaign():
        log_entry["_camp"] = _active_campaign()
    with _text_log_lock:
        _text_log.append(log_entry)
    with _tail_lock:
        _tail_buffer.append(log_entry)
    _persist_log()
    _persist_tail()
    _broadcast({"handout": entry})


import tongues as _tongues


def _handout_readers(language: str) -> list:
    """The characters at the table whose sheet lists the language."""
    with _stats_lock:
        party = [str(p.get("name", "")) for p in _current_stats.get("players", []) if p.get("name")]
    readers = []
    for name in party:
        try:
            text = _inventory._read(_inventory.find_sheet(name, _active_campaign()))
        except Exception:
            continue
        if _tongues.knows(text, language):
            readers.append(name)
    return readers


def _handout_view(entry: dict, me: str) -> dict:
    """What `me` (a character, lowercase; "" for the DM and the table screen) can read of a handout.

    In a language `me` does not know, the words become the shapes of its script —
    the server never sends them the text."""
    if not isinstance(entry, dict) or not entry.get("language") or not me:
        return entry
    if me in {str(r).lower() for r in entry.get("readers") or []}:
        return entry
    out = dict(entry)
    out["body"] = _tongues.glyphs(entry.get("body", ""), entry["language"])
    out["from"] = _tongues.glyphs(entry.get("from", ""), entry["language"])
    out["unreadable"] = True
    return out


def _handout_for(payload: Optional[dict], viewer: Optional[str], bound: str) -> Optional[dict]:
    """A handout in a language: each screen gets only what its character can read."""
    me = viewer if viewer is not None else (bound or "")
    if not isinstance(payload, dict) or not me:
        return payload
    if isinstance(payload.get("handout"), dict) and payload["handout"].get("language"):
        payload = dict(payload, handout=_handout_view(payload["handout"], me))
    batch = payload.get("replay_batch")
    if isinstance(batch, list) and any(isinstance(e, dict) and isinstance(e.get("handout"), dict) for e in batch):
        payload = dict(payload, replay_batch=[dict(e, handout=_handout_view(e["handout"], me))
                                              if isinstance(e, dict) and isinstance(e.get("handout"), dict) else e
                                              for e in batch])
    return payload


def _clean_handout_text(value, limit: int) -> str:
    # The DM's own words: keep punctuation and line breaks, drop control characters.
    return re.sub(r"[\x00-\x09\x0b-\x1f\x7f]", "", str(value or ""))[:limit].strip()


@app.route("/handout", methods=["POST"])
def handout():
    """Hand the party a document, or show an old one again.

    New:     {"title": "...", "body": "...", "kind": "letter", "from": "...", "image": "file.png",
              "language": "Elfico"}
    Again:   {"id": "a1b2c3d4"}
    Reader:  {"id": "a1b2c3d4", "reader": "Hernio"} — someone can read it now (comprehend languages,
             a translation): their screen gets the text
    """
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(force=True, silent=True) or {}

    again = str(data.get("id", "")).strip()
    if again:
        reader = " ".join(str(data.get("reader", "")).split())[:50]
        with _handouts_lock:
            items = _read_handouts()
            entry = next((h for h in items if h["id"] == again), None)
            if entry is None:
                return jsonify({"error": "unknown handout"}), 404
            if reader:
                if not entry.get("language"):
                    return jsonify({"error": "that handout is in no particular language: everyone reads it"}), 400
                if reader.lower() not in {r.lower() for r in entry.get("readers") or []}:
                    entry["readers"] = list(entry.get("readers") or []) + [reader]
                try:
                    _write_handouts(items)
                except OSError as e:
                    return jsonify({"error": f"could not save: {e}"}), 500
        _show_handout(entry)
        return jsonify(entry), 200

    title = " ".join(_clean_handout_text(data.get("title"), _HANDOUT_TITLE_MAX).split())
    body = _clean_handout_text(data.get("body"), _HANDOUT_BODY_MAX)
    image = str(data.get("image", "")).strip()
    kind = str(data.get("kind", "")).strip().lower() or "letter"
    if kind not in _HANDOUT_KINDS:
        return jsonify({"error": "unknown kind", "kinds": list(_HANDOUT_KINDS)}), 400
    if not title or not (body or image):
        return jsonify({"error": "a handout needs a title and a body or an image"}), 400
    if image:
        d = _media_dir()
        if not d or not _MEDIA_NAME_RE.match(image) or not os.path.isfile(os.path.join(d, image)):
            return jsonify({"error": "unknown media file"}), 400

    entry = {
        "id": secrets.token_hex(4),
        "title": title,
        "kind": kind,
        "body": body,
        "from": " ".join(_clean_handout_text(data.get("from"), 80).split()),
        "image": image,
        "ts": _time.time(),
    }
    language = " ".join(_clean_handout_text(data.get("language"), 40).split())
    if language:
        entry["language"] = language
        entry["readers"] = _handout_readers(language)
    with _handouts_lock:
        items = _read_handouts()
        items.append(entry)
        try:
            _write_handouts(items)
        except OSError as e:
            return jsonify({"error": f"could not save the handout: {e}"}), 500
    _show_handout(entry)
    return jsonify(entry), 200


@app.route("/handout/<handout_id>", methods=["DELETE"])
def handout_delete(handout_id):
    """Take a handout out of the Documents list (the story keeps what was shown)."""
    if not _token_ok():
        return "Forbidden", 403
    with _handouts_lock:
        items = _read_handouts()
        kept = [h for h in items if h["id"] != handout_id]
        if len(kept) == len(items):
            return jsonify({"error": "unknown handout"}), 404
        try:
            _write_handouts(kept)
        except OSError as e:
            return jsonify({"error": f"could not save: {e}"}), 500
    _broadcast({"handout_removed": handout_id})
    return "", 204


@app.route("/handouts")
def handouts_list():
    """Every handout of the active campaign, oldest first. Players may read them."""
    if not _token_ok():
        return "Forbidden", 403
    if getattr(g, "role", None) == "player":
        me = (g.character or "").lower()
    else:
        me = str(request.args.get("character") or "").strip().lower()[:50]
    with _handouts_lock:
        return jsonify([_handout_view(h, me) for h in _read_handouts()]), 200


def _pc_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _known_pc_slugs(extra_campaign: str = "") -> set:
    """Slugs of every character with a sheet: the active campaign's characters/
    (plus the one this request switches to) and the global roster. Empty when
    none of those folders exist — then nothing is filtered."""
    dirs = []
    for camp in (_active_campaign(), extra_campaign):
        camp = re.sub(r"[^A-Za-z0-9_-]", "", camp or "")[:50]
        if camp:
            try:
                dirs.append(_find_campaign(camp) / "characters")
            except Exception:
                pass
    try:
        from paths import characters_dir as _characters_dir
        dirs.append(_characters_dir())
    except Exception:
        pass
    slugs: set = set()
    for d in dirs:
        try:
            files = list(d.glob("*.md"))
        except Exception:
            continue
        for f in files:
            slugs.add(_pc_slug(f.stem))
            try:
                with open(f, encoding="utf-8") as fh:
                    first = fh.readline()
            except Exception:
                continue
            if first.startswith("# "):
                slugs.add(_pc_slug(first[2:].strip()))
    return slugs


# ─── Shop (shop.py) ──────────────────────────────────────────────────────────
# The DM opens a shop with a price list; it shows on the screens of the
# characters inside it ("buyers"; none listed = the whole table). A player
# buys from their own phone and the purchase is applied here, at once, with
# scripts/inventory.py: coins off the character sheet (with change), the item
# on it, one less in stock. shop.py hands the DM what was sold.

import inventory as _inventory
import shop as _shop_cli

SHOP_FILE = rt("shop.json")
_shop: Optional[dict] = None
_shop_lock = threading.Lock()
_SHOP_MAX_ITEMS = 80


def _load_shop() -> None:
    global _shop
    try:
        with open(SHOP_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        _shop = data


def _persist_shop() -> None:
    try:
        if _shop is None:
            if os.path.exists(SHOP_FILE):
                os.remove(SHOP_FILE)
            return
        tmp = SHOP_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_shop, f, ensure_ascii=False)
        os.replace(tmp, SHOP_FILE)
    except OSError:
        pass


_load_shop()


def _open_shop_now() -> Optional[dict]:
    """The open shop, if it belongs to the campaign being played."""
    if _shop is None or _shop.get("campaign", "") != _active_campaign():
        return None
    return _shop


def _shop_for(shop: Optional[dict], character: str) -> bool:
    """Whether this character is inside the shop (and so sees it and buys from it)."""
    buyers = (shop or {}).get("buyers") or []
    return not buyers or any(_sheet_key(b) == _sheet_key(character) for b in buyers)


def _shop_purse(character: str) -> Optional[dict]:
    """Coins on the character's sheet, or None when there is no sheet to read."""
    if not character:
        return None
    try:
        purse = _inventory.purse_of(character, _active_campaign())
    except _inventory.InventoryError:
        return None
    return {"coins": purse, "cp": _inventory.coins_cp(purse)}


@app.route("/shop", methods=["GET"])
def shop_get():
    """The open shop, and the asker's purse.

    A logged-in player gets their own purse; this PC names a character with
    ?character= (its phone view) or gets the price list alone.
    """
    if not _token_ok():
        return "Forbidden", 403
    if getattr(g, "role", None) == "player":
        who = g.character or ""
    else:
        who = request.args.get("character", "").strip()[:50]
    with _shop_lock:
        shop = _open_shop_now()
    if shop and who and not _shop_for(shop, who):
        shop = None                              # not in the shop: nothing to see
    fund = None
    if shop and who:
        with _treasury_lock:
            view = _treasury.public_view(_treasury_now())
        fund = {"coins": view["coins"], "cp": view["cp"]} if view else None
    return jsonify({"shop": shop, "purse": _shop_purse(who) if shop else None, "fund": fund}), 200


@app.route("/shop", methods=["POST"])
def shop_set():
    """Open a shop (replacing the one open) or close it.

    {"name": "...", "keeper": "...", "note": "...",
     "items": [{"name": "...", "price": "5gp", "stock": 3, "note": "...", "section": "gear"}]}
    {"close": true}
    """
    global _shop
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(force=True, silent=True) or {}
    if data.get("close"):
        with _shop_lock:
            _shop = None
            _persist_shop()
        _broadcast({"shop": None})
        return jsonify({"shop": None}), 200

    name = " ".join(str(data.get("name", "")).split())[:80]
    raw_items = data.get("items")
    if not name or not isinstance(raw_items, list) or not raw_items:
        return jsonify({"error": "a shop needs a name and at least one item"}), 400
    items = []
    for i, raw in enumerate(raw_items[:_SHOP_MAX_ITEMS]):
        if not isinstance(raw, dict):
            return jsonify({"error": f"item {i + 1} is not an object"}), 400
        item_name = " ".join(str(raw.get("name", "")).split())[:120]
        try:
            price_cp = _inventory.coins_cp(_inventory.parse_coins(str(raw.get("price", ""))))
            section = _inventory._section_key(str(raw.get("section", "") or "gear"))
        except _inventory.InventoryError as e:
            return jsonify({"error": f"item {i + 1} ({item_name or '?'}): {e}"}), 400
        stock = raw.get("stock")
        if stock is not None and (isinstance(stock, bool) or not isinstance(stock, int) or stock < 0):
            return jsonify({"error": f"item {i + 1} ({item_name}): stock must be a whole number"}), 400
        if not item_name:
            return jsonify({"error": f"item {i + 1} has no name"}), 400
        items.append({
            "id": f"i{i + 1}",
            "name": item_name,
            "price_cp": price_cp,
            "price": _inventory.format_cp(price_cp),
            "stock": stock,
            "note": " ".join(str(raw.get("note", "")).split())[:160],
            "section": section,
        })
    with _stats_lock:
        party = [str(p.get("name", "")) for p in _current_stats.get("players", []) if p.get("name")]
    buyers = []
    for n in (str(x).strip() for x in (data.get("buyers") or [])):
        hit = next((p for p in party if _sheet_key(p) == _sheet_key(n)), None) if n else None
        if n and not hit:
            return jsonify({"error": f"not at the table: {n}"}), 400
        if hit and hit not in buyers:
            buyers.append(hit)
    shop = {
        "name": name,
        "buyers": buyers,
        "group": " ".join(str(data.get("group", "")).split())[:60],
        "keeper": " ".join(str(data.get("keeper", "")).split())[:60],
        "note": " ".join(str(data.get("note", "")).split())[:200],
        "campaign": _active_campaign(),
        "opened": _time.time(),
        "items": items,
    }
    with _shop_lock:
        _shop = shop
        _persist_shop()
    _broadcast({"shop": shop, "shop_opened": True})
    return jsonify({"shop": shop}), 200


@app.route("/shop/buy", methods=["POST"])
def shop_buy():
    """A player buys from the open shop: {"character": "Aldric", "item": "i2", "qty": 1}.

    The purchase is applied to the character sheet before this returns, or not
    at all: 409 says why (not enough coins, sold out, no sheet).
    """
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr):
        return "Too Many Requests", 429
    data = request.get_json(force=True, silent=True) or {}
    character = _acting_character(str(data.get("character", ""))[:50].strip())
    with _stats_lock:
        known = {p.get("name", "") for p in _current_stats.get("players", [])}
    character = next((n for n in known if n.lower() == character.lower()), character)   # the party's spelling
    if not character or character == "Everybody" or not _char_ok(character, known):
        return jsonify({"error": "unknown character", "code": "character"}), 403
    try:
        qty = int(data.get("qty", 1))
    except (TypeError, ValueError):
        qty = 0
    if not 1 <= qty <= 99:
        return jsonify({"error": "quantity must be between 1 and 99", "code": "qty"}), 400

    with _shop_lock:
        shop = _open_shop_now()
        if shop is None:
            return jsonify({"error": "the shop is closed", "code": "closed"}), 409
        if not _shop_for(shop, character):
            return jsonify({"error": "not in the shop", "code": "away"}), 403
        item = next((it for it in shop["items"] if it["id"] == str(data.get("item", ""))), None)
        if item is None:
            return jsonify({"error": "the shop does not sell that", "code": "item"}), 404
        if item["stock"] is not None and item["stock"] < qty:
            return jsonify({"error": "not enough in stock", "code": "stock", "shop": shop}), 409
        purse = fund_paid = fund_paid_view = None
        if data.get("pay") == "fund":            # the party's common fund pays (treasury.py)
            with _treasury_lock:
                fund, fund_path = _treasury_now(), _treasury_file()
                if fund is None or not fund_path:
                    return jsonify({"error": "the party has no common fund", "code": "nofund"}), 409
                try:
                    _treasury.shop_buy(fund, character, item["name"], item["price_cp"], qty,
                                               item["section"], shop["name"], _active_campaign())
                except (_treasury.TreasuryError, _inventory.InventoryError) as e:
                    code = "fund" if "the fund holds" in str(e) else "sheet"
                    return jsonify({"error": str(e), "code": code}), 409
                _treasury.save(fund_path, fund)
                fund_paid_view = _treasury.public_view(fund)
                fund_paid = fund_paid_view["text"]     # the DM hears of it with the sale ([[SHOP: …]])
        else:
            try:
                purse = _inventory.op_buy(character, item["name"], item["price_cp"], qty,
                                          item["section"], _active_campaign(), push=False)
            except _inventory.InventoryError as e:
                code = "funds" if "not enough coins" in str(e) else "sheet"
                return jsonify({"error": str(e), "code": code}), 409
        if item["stock"] is not None:
            item["stock"] -= qty
        _persist_shop()
        shop = json.loads(json.dumps(shop))      # a snapshot to send outside the lock

    label = item["name"] if qty == 1 else f'{item["name"]} ×{qty}'
    total = _inventory.format_cp(item["price_cp"] * qty)
    # The display's own copy of the sheet, so the bag is right without a reload,
    # and what it weighs now (encumbrance.py).
    try:
        import encumbrance as _encumbrance
        load = _encumbrance.display_load(_encumbrance.report(
            _inventory._read(_inventory.find_sheet(character, _active_campaign())), _active_campaign()))
    except Exception:
        load = None
    snapshot = None
    with _stats_lock:
        for p in _current_stats.get("players", []):
            if p.get("name", "").lower() == character.lower():
                p.setdefault("sheet", {}).setdefault("inventory", []).append(label)
                if load:
                    p["load"] = load
                snapshot = dict(_current_stats)
    if snapshot is not None:
        _persist_stats()
        _broadcast({"stats": snapshot})
    sale = {"character": character, "item": item["name"], "qty": qty, "total": total,
            "shop": shop["name"], "left": _inventory.format_coins(purse) if purse is not None else ""}
    if fund_paid:
        sale["fund"] = fund_paid
    _shop_cli.sales_add(sale)
    _broadcast({"shop": shop, "shop_sale": {"character": character, "item": label, "total": total, "fund": bool(fund_paid)}})
    if fund_paid:
        _broadcast({"treasury": fund_paid_view})
    return jsonify({"shop": shop, "bought": label, "total": total, "fund": fund_paid_view,
                    "purse": _shop_purse(character)}), 200


# ─── The party's common fund (treasury.py) ───────────────────────────────────
# Coins that belong to the group, in <campaign>/treasury.json. Every screen can
# read it; a player puts coins in or takes them out from their own phone (the
# coins move between their sheet and the fund) and the DM is told; the shop can
# be paid from it. POST /treasury is treasury.py saying it changed.
import treasury as _treasury

_treasury_lock = threading.Lock()


def _treasury_file() -> Optional[str]:
    camp = re.sub(r"[^A-Za-z0-9_-]", "", _active_campaign())[:50]
    if not camp:
        return None
    try:
        return str(_find_campaign(camp) / "treasury.json")
    except Exception:
        return None


def _treasury_now() -> Optional[dict]:
    path = _treasury_file()
    try:
        return _treasury.load(path) if path else None
    except _treasury.TreasuryError:
        return None


def _refresh_load(character: str) -> None:
    """What the character carries now (coins weigh too), on the display's copy of the sheet."""
    try:
        import encumbrance as _encumbrance
        load = _encumbrance.display_load(_encumbrance.report(
            _inventory._read(_inventory.find_sheet(character, _active_campaign())), _active_campaign()))
    except Exception:
        return
    snapshot = None
    with _stats_lock:
        for p in _current_stats.get("players", []):
            if p.get("name", "").lower() == character.lower() and load:
                p["load"] = load
                snapshot = dict(_current_stats)
    if snapshot is not None:
        _persist_stats()
        _broadcast({"stats": snapshot})


@app.route("/treasury", methods=["GET"])
def treasury_get():
    if not _token_ok():
        return "Forbidden", 403
    who = g.character if getattr(g, "role", None) == "player" else request.args.get("character", "").strip()[:50]
    with _treasury_lock:
        view = _treasury.public_view(_treasury_now())
    return jsonify({"treasury": view, "purse": _shop_purse(who or "") if view else None}), 200


@app.route("/treasury", methods=["POST"])
def treasury_changed():
    """treasury.py changed the fund: every screen gets it, with what happened."""
    data = request.get_json(silent=True) or {}
    with _treasury_lock:
        view = _treasury.public_view(_treasury_now())
    payload = {"treasury": view}
    if data.get("news"):
        payload["treasury_news"] = str(data["news"])[:200]
    _broadcast(payload)
    return "", 204


@app.route("/treasury/move", methods=["POST"])
def treasury_move():
    """A player moves coins: {"character", "dir": "in"|"out", "coins": "10gp"}.

    "in" takes them from the character's sheet into the fund, "out" the other
    way; both or neither. 409 says why (not enough coins, no fund, no sheet).
    """
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr):
        return "Too Many Requests", 429
    body = request.get_json(silent=True) or {}
    character = _acting_character(str(body.get("character", ""))[:50].strip())
    with _stats_lock:
        known = {p.get("name", "") for p in _current_stats.get("players", [])}
    character = next((n for n in known if n.lower() == character.lower()), character)
    if not character or character == "Everybody" or not _char_ok(character, known):
        return jsonify({"error": "unknown character", "code": "character"}), 403
    direction = body.get("dir")
    if direction not in ("in", "out"):
        return jsonify({"error": "dir is in or out", "code": "dir"}), 400
    try:
        coins = _inventory.parse_coins(str(body.get("coins", ""))[:60])
    except _inventory.InventoryError as e:
        return jsonify({"error": str(e), "code": "coins"}), 400
    path = _treasury_file()
    with _treasury_lock:
        data = _treasury_now()
        if data is None or not path:
            return jsonify({"error": "the party has no common fund", "code": "nofund"}), 409
        try:
            move = (_treasury.deposit if direction == "in" else _treasury.withdraw)(data, character, coins, _active_campaign())
        except (_treasury.TreasuryError, _inventory.InventoryError) as e:
            code = "fund" if "the fund holds" in str(e) else "funds" if "not enough coins" in str(e) else "sheet"
            return jsonify({"error": str(e), "code": code}), 409
        _treasury.save(path, data)
        view = _treasury.public_view(data)
    _treasury.notes_add(_treasury.format_note(move["entry"], view["text"]))
    _refresh_load(character)
    _broadcast({"treasury": view, "treasury_news": _treasury.describe(move["entry"]),
                "treasury_by": move["character"]})
    return jsonify({"treasury": view, "purse": {"coins": move["purse"], "cp": _inventory.coins_cp(move["purse"])}}), 200


# ─── The party's stronghold (stronghold.py) ──────────────────────────────────
# The party's own place, in <campaign>/stronghold.json: works under way, upkeep,
# staff, improvements. stronghold.py and the calendar change it; every screen
# reads it; POST /stronghold says it changed, with what happened.
import stronghold as _stronghold


def _public_stronghold() -> Optional[dict]:
    camp = re.sub(r"[^A-Za-z0-9_-]", "", _active_campaign())[:50]
    if not camp:
        return None
    try:
        return _stronghold.public_view(_stronghold.load(str(_find_campaign(camp) / "stronghold.json")))
    except Exception:
        return None


@app.route("/stronghold", methods=["GET"])
def stronghold_get():
    if not _token_ok():
        return "Forbidden", 403
    return jsonify({"stronghold": _public_stronghold()}), 200


@app.route("/stronghold", methods=["POST"])
def stronghold_changed():
    data = request.get_json(silent=True) or {}
    payload = {"stronghold": _public_stronghold()}
    news = data.get("news")
    if isinstance(news, list) and news:
        payload["stronghold_news"] = [str(n)[:240] for n in news[:4]]
    _broadcast(payload)
    return "", 204


@app.route("/scene", methods=["POST"])
def set_scene():
    """The DM sets where the party is: background, particles and music.

    Body: {"scene": name}  — a key of SCENES (tavern, city, castle, crypt, …)
    """
    global _current_scene_name, _scene_pinned
    if not _token_ok():
        return "Forbidden", 403
    name = str((request.get_json(silent=True) or {}).get("scene") or "").strip().lower()
    if name not in SCENES:
        return jsonify({"error": "unknown scene", "scenes": sorted(SCENES)}), 400
    _current_scene_name, _scene_pinned = name, True
    _scene_buffer.clear()
    try:
        with open(_SCENE_FILE, "w", encoding="utf-8") as f:
            f.write(name)
    except OSError:
        pass
    _broadcast({"scene": _scene_payload(name)})
    return jsonify({"scene": name}), 200


# ─── Region map (region_map.py) ──────────────────────────────────────────────
# The places the party knows and the roads between them live in
# <campaign>/region.json; region_map.py edits it and draws the picture into
# media/. The display only reads: where each place sits on the picture and how
# many days it is from the party, for the map every screen can open.

import region_map as _region


@app.route("/region", methods=["GET"])
def region_get():
    if not _token_ok():
        return "Forbidden", 403
    data = _region.empty()
    camp = re.sub(r"[^A-Za-z0-9_-]", "", _active_campaign())[:50]
    if camp:
        try:
            data = _region.load(str(_find_campaign(camp) / "region.json"))
        except Exception:
            data = _region.empty()
    return jsonify(_region.public_view(data)), 200


@app.route("/region", methods=["POST"])
def region_changed():
    """region_map.py redrew the map: tell every screen to fetch it again."""
    if not _token_ok():
        return "Forbidden", 403
    _broadcast({"region_changed": _time.time()})
    return "", 204


# ─── Progress clocks (clock.py) ──────────────────────────────────────────────
# clock.py keeps the clocks in <campaign>/clocks.json. Every screen shows the
# ones that are not secret; POST /clocks says they changed.
import clock as _clocks


def _public_clocks() -> list:
    camp = re.sub(r"[^A-Za-z0-9_-]", "", _active_campaign())[:50]
    if not camp:
        return []
    try:
        return _clocks.public_view(_clocks.load(str(_find_campaign(camp) / "clocks.json")))
    except Exception:
        return []


@app.route("/clocks", methods=["GET"])
def clocks_get():
    return jsonify(_public_clocks()), 200


@app.route("/clocks", methods=["POST"])
def clocks_changed():
    """clock.py moved a clock: send every screen the new list, and what moved."""
    data = request.get_json(silent=True) or {}
    payload = {"clocks": _public_clocks()}
    event = data.get("event")
    if isinstance(event, dict) and event.get("name"):
        payload["clock_event"] = {k: event.get(k) for k in ("id", "name", "filled", "segments", "delta", "full", "new")}
    _broadcast(payload)
    return "", 204


# ─── Bestiary (bestiary.py) ──────────────────────────────────────────────────
# bestiary.py keeps what the party has learned in <campaign>/bestiary.json.
# Every screen can read it; POST /bestiary says a page was added or grew.
import bestiary as _bestiary


def _public_bestiary() -> list:
    camp = re.sub(r"[^A-Za-z0-9_-]", "", _active_campaign())[:50]
    if not camp:
        return []
    try:
        return _bestiary.public_view(_bestiary.load(str(_find_campaign(camp) / "bestiary.json")))
    except Exception:
        return []


@app.route("/bestiary", methods=["GET"])
def bestiary_get():
    return jsonify(_public_bestiary()), 200


@app.route("/bestiary", methods=["POST"])
def bestiary_changed():
    """bestiary.py wrote in the notebook: send every screen the pages, and which one changed."""
    data = request.get_json(silent=True) or {}
    payload = {"bestiary": _public_bestiary()}
    event = data.get("event")
    if isinstance(event, dict) and event.get("name"):
        payload["bestiary_event"] = {"name": str(event["name"])[:60], "new": bool(event.get("new"))}
    _broadcast(payload)
    return "", 204


# ─── Quests (quests.py) ──────────────────────────────────────────────────────
# quests.py keeps the party's quests in <campaign>/quests.json. They reach the
# screens inside the stats, like the ones pushed by hand; POST /quests says the
# file changed, and loading a campaign that has the file brings them back.
import quests as _quests


def _campaign_quests() -> Optional[list]:
    """The active campaign's quests as the panel wants them; None when it keeps no quests.json."""
    camp = re.sub(r"[^A-Za-z0-9_-]", "", _active_campaign())[:50]
    if not camp:
        return None
    try:
        path = _find_campaign(camp) / "quests.json"
        return _quests.public_view(_quests.load(str(path))) if path.is_file() else None
    except Exception:
        return None


def _adopt_campaign_quests() -> Optional[dict]:
    """Put the campaign's quests in the stats. Returns the stats to broadcast, or None."""
    rows = _campaign_quests()
    if rows is None:
        return None
    with _stats_lock:
        _current_stats["quests"] = rows
        snapshot = dict(_current_stats)
    _persist_stats()
    return snapshot


@app.route("/quests", methods=["POST"])
def quests_changed():
    """quests.py changed a quest: send every screen the list, and what happened."""
    data = request.get_json(silent=True) or {}
    payload = {}
    snapshot = _adopt_campaign_quests()
    if snapshot is not None:
        payload["stats"] = snapshot
    event = data.get("event")
    if isinstance(event, dict) and event.get("name") and event.get("what") in ("new", "updated", "resolved", "failed"):
        payload["quest_event"] = {"name": str(event["name"])[:80], "what": event["what"]}
    if payload:
        _broadcast(payload)
    return "", 204


# ─── Faction reputations (factions.py) ───────────────────────────────────────
# factions.py keeps a score from -10 to +10 for each faction in
# <campaign>/factions.json. Like the quests, they reach the screens inside the
# stats; POST /factions says the file changed, and loading a campaign that has
# the file brings them back.
import factions as _factions


def _campaign_factions() -> Optional[list]:
    """The active campaign's factions as the panel wants them; None when it keeps no factions.json."""
    camp = re.sub(r"[^A-Za-z0-9_-]", "", _active_campaign())[:50]
    if not camp:
        return None
    try:
        path = _find_campaign(camp) / "factions.json"
        return _factions.public_view(_factions.load(str(path))) if path.is_file() else None
    except Exception:
        return None


def _adopt_campaign_factions() -> Optional[dict]:
    """Put the campaign's factions in the stats. Returns the stats to broadcast, or None."""
    rows = _campaign_factions()
    if rows is None:
        return None
    with _stats_lock:
        _current_stats["factions"] = rows
        snapshot = dict(_current_stats)
    _persist_stats()
    return snapshot


@app.route("/factions", methods=["POST"])
def factions_changed():
    """factions.py moved a faction: send every screen the list, and what moved."""
    data = request.get_json(silent=True) or {}
    payload = {}
    snapshot = _adopt_campaign_factions()
    if snapshot is not None:
        payload["stats"] = snapshot
    event = data.get("event")
    if isinstance(event, dict) and event.get("name"):
        try:
            delta, score = int(event.get("delta") or 0), int(event.get("score") or 0)
        except (TypeError, ValueError):
            delta, score = 0, 0
        payload["faction_event"] = {"name": str(event["name"])[:80], "delta": delta, "score": score,
                                    "standing": _factions.standing(max(-10, min(10, score))),
                                    "new": bool(event.get("new")), "reason": str(event.get("reason") or "")[:240]}
    if payload:
        _broadcast(payload)
    return "", 204


# ─── Supplies (supplies.py) ──────────────────────────────────────────────────
# supplies.py keeps the party's food, water and light in
# <campaign>/supplies.json and spends them as the calendar moves. Every screen
# can read them; POST /supplies says they changed, with what happened.
import supplies as _supplies


def _public_supplies():
    camp = re.sub(r"[^A-Za-z0-9_-]", "", _active_campaign())[:50]
    if not camp:
        return None
    try:
        path = _find_campaign(camp) / "supplies.json"
        return _supplies.public_view(_supplies.load(str(path))) if path.is_file() else None
    except Exception:
        return None


@app.route("/supplies", methods=["GET"])
def supplies_get():
    return jsonify(_public_supplies()), 200


@app.route("/supplies", methods=["POST"])
def supplies_changed():
    """supplies.py spent or counted something: send every screen the stock, and the news."""
    data = request.get_json(silent=True) or {}
    payload = {"supplies": _public_supplies()}
    news = data.get("news")
    if isinstance(news, list):
        payload["supplies_news"] = [str(n)[:300] for n in news[:6]]
    _broadcast(payload)
    return "", 204


# ─── People met (people.py) ──────────────────────────────────────────────────
# people.py keeps what the party knows of the people it met in
# <campaign>/people.json. Every screen can read it; POST /people says a page
# was added or grew.
import people as _people


def _public_people() -> list:
    camp = re.sub(r"[^A-Za-z0-9_-]", "", _active_campaign())[:50]
    if not camp:
        return []
    try:
        return _people.public_view(_people.load(str(_find_campaign(camp) / "people.json")))
    except Exception:
        return []


@app.route("/people", methods=["GET"])
def people_get():
    return jsonify(_public_people()), 200


@app.route("/people", methods=["POST"])
def people_changed():
    """people.py wrote in the notebook: send every screen the pages, and which one changed."""
    data = request.get_json(silent=True) or {}
    payload = {"people": _public_people()}
    event = data.get("event")
    if isinstance(event, dict) and event.get("name"):
        payload["people_event"] = {"name": str(event["name"])[:60], "new": bool(event.get("new"))}
        if isinstance(event.get("card"), dict):          # the card every screen shows (who, what they look like, where)
            payload["people_event"]["card"] = {k: str(event["card"].get(k) or "")[:160] for k in ("role", "look", "where")}
    _broadcast(payload)
    return "", 204


# ─── Timeline (timeline.py) ──────────────────────────────────────────────────
# timeline.py keeps what happened, by the world's dates, in
# <campaign>/timeline.json. Every screen reads the events that are not secret;
# POST /timeline says the list changed.
import timeline as _timeline


def _public_timeline() -> list:
    camp = re.sub(r"[^A-Za-z0-9_-]", "", _active_campaign())[:50]
    if not camp:
        return []
    try:
        return _timeline.public_view(_timeline.load(str(_find_campaign(camp) / "timeline.json")))
    except Exception:
        return []


@app.route("/timeline", methods=["GET"])
def timeline_get():
    return jsonify(_public_timeline()), 200


@app.route("/timeline", methods=["POST"])
def timeline_changed():
    """timeline.py changed the timeline: send every screen the events the players may see."""
    _broadcast({"timeline": _public_timeline()})
    return "", 204


# ─── A split party (split.py) ────────────────────────────────────────────────
# split.py keeps who is with whom in <campaign>/split.json. The DM's screen
# gets every group, a player only their own; what send.py addresses to a
# group reaches only its players (_scene_for, on the way out of /stream).
import split as _split_lib


def _party_split() -> list:
    camp = re.sub(r"[^A-Za-z0-9_-]", "", _active_campaign())[:50]
    if not camp:
        return []
    try:
        return _split_lib.public_view(_split_lib.load(str(_find_campaign(camp) / "split.json")))
    except Exception:
        return []


def _own_group(groups: list, me: str) -> list:
    return [grp for grp in groups if isinstance(grp, dict)
            and me in [str(m).lower() for m in grp.get("members") or []]]


@app.route("/split", methods=["GET"])
def split_get():
    groups = _party_split()
    if g.role == "player":
        groups = _own_group(groups, (g.character or "").lower())
    return jsonify(groups), 200


@app.route("/split", methods=["POST"])
def split_changed():
    """split.py changed the groups: tell every screen who is with whom now."""
    event = str((request.get_json(silent=True) or {}).get("event") or "")
    _broadcast({"split": _party_split(), "split_event": event if event in ("split", "moved", "joined") else ""})
    return "", 204


# ─── Spellbook (scripts/spellbook.py) ────────────────────────────────────────
# A caster's spells and slots, read from the character sheet. A player opens
# their own; casting from it marks the slot on the sheet and on the display at
# once and leaves a line for the DM (check_input.py). The display's live slot
# count wins over the sheet's when the DM has pushed one.
import spellbook as _spellbook

_spell_lock = threading.Lock()


def _spell_lang(value: str = "") -> str:
    lang = re.sub(r"[^a-z-]", "", str(value or "").lower())[:8]
    if lang:
        return lang
    try:
        from paths import campaign_language as _campaign_language
        return _campaign_language(re.sub(r"[^A-Za-z0-9_-]", "", _active_campaign())[:50]) or "en"
    except Exception:
        return "en"


def _spell_ruleset() -> str:
    with _stats_lock:
        rs = str(_current_stats.get("ruleset") or "")
    return rs if rs in ("2014", "2024") else "2014"


def _live_slots(player: Optional[dict]) -> dict:
    """{level: {"max", "used"}} as the display holds it — {} when it holds none."""
    out = {}
    for lvl, slot in ((player or {}).get("spell_slots") or {}).items():
        if not str(lvl).isdigit() or not isinstance(slot, dict):
            continue
        slot = dict(slot)
        _normalize_slot(slot)
        try:
            mx = int(slot.get("max", 0))
            out[int(lvl)] = {"max": mx, "used": max(0, min(int(slot.get("used", 0)), mx))}
        except (TypeError, ValueError):
            continue
    return out


def _spell_view(character: str, text: str, lang: str) -> dict:
    data = _spellbook.book(text, _spell_ruleset(), lang)
    live = _live_player(character) or {}
    slots = {int(k): v for k, v in data["slots"].items()}
    slots.update(_live_slots(live))
    data["slots"] = {str(k): slots[k] for k in sorted(slots)}
    data["concentration"] = str(live.get("concentration") or "")
    data["character"] = live.get("name") or character
    return data


def _spell_sheet(character: str):
    """(path, text) of the character's sheet, or (None, an error response)."""
    if not _token_ok() or _is_other_character(character):
        return None, ("Forbidden", 403)
    path, safe, _camp = _find_sheet_file(character)
    if not safe:
        return None, ("Bad character name", 400)
    if not path:
        return None, (jsonify({"error": "no sheet"}), 404)
    try:
        with open(path, encoding="utf-8", newline="") as f:
            return path, f.read()
    except OSError as e:
        return None, (jsonify({"error": str(e)}), 500)


@app.route("/spellbook", methods=["GET"])
def spellbook_list():
    """The casters this screen may open: every one at the table for the DM, a player's own."""
    if not _token_ok():
        return "Forbidden", 403
    with _stats_lock:
        names = [str(p.get("name", "")) for p in _current_stats.get("players", []) if p.get("name")]
    if g.role == "player":
        names = [n for n in names if n.lower() == (g.character or "").lower()] or [g.character or ""]
    out = []
    for name in names:
        path, _safe, _camp = _find_sheet_file(name)
        if not path:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                count = len(_spellbook.read_spells(f.read()))
        except OSError:
            continue
        if count:
            out.append({"character": name, "spells": count})
    return jsonify(out), 200


@app.route("/spellbook/<character>", methods=["GET"])
def spellbook_get(character):
    path, text = _spell_sheet(character)
    if path is None:
        return text
    return jsonify(_spell_view(character, text, _spell_lang(request.args.get("lang", "")))), 200


@app.route("/spellbook/<character>/cast", methods=["POST"])
def spellbook_cast(character):
    """Cast a spell from the panel: mark the slot, set concentration, tell the DM."""
    data = request.get_json(silent=True) or {}
    lang = _spell_lang(data.get("lang", ""))
    with _spell_lock:
        path, text = _spell_sheet(character)
        if path is None:
            return text
        live = _live_player(character) or {}
        slots = {**_spellbook.read_slots(text), **_live_slots(live)}
        try:
            new_text, done = _spellbook.cast(text, str(data.get("spell", ""))[:80], data.get("level"),
                                             _spell_ruleset(), lang, slots)
        except _spellbook.SpellError as e:
            return jsonify({"error": str(e), "code": "slots" if "slot left" in str(e) else "spell"}), 409
        if new_text != text:
            try:
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8", newline="") as f:
                    f.write(new_text)
                os.replace(tmp, path)
            except OSError as e:
                return jsonify({"error": str(e), "code": "sheet"}), 500
        name = live.get("name") or character
        snapshot = None
        with _stats_lock:
            for p in _current_stats.get("players", []):
                if _sheet_key(p.get("name", "")) != _sheet_key(character):
                    continue
                if done["slot"]:
                    p.setdefault("spell_slots", {})[str(done["slot"])] = {"used": done["max"] - done["left"], "max": done["max"]}
                if done["concentration"]:
                    p["concentration"] = done["spell"]
                snapshot = dict(_current_stats)
        if snapshot is not None:
            _persist_stats()
            _broadcast({"stats": snapshot})
    _spellbook.casts_add({"character": name, **done})
    _broadcast({"spell_cast": {"character": name, "spell": done["spell"], "slot": done["slot"]}})
    return jsonify({**_spell_view(character, new_text, lang), "cast": done}), 200


@app.route("/spellbook/<character>/concentration", methods=["POST"])
def spellbook_drop(character):
    """The player lets go of the spell they are concentrating on."""
    lang = _spell_lang((request.get_json(silent=True) or {}).get("lang", ""))
    path, text = _spell_sheet(character)
    if path is None:
        return text
    dropped, name, snapshot = "", character, None
    with _stats_lock:
        for p in _current_stats.get("players", []):
            if _sheet_key(p.get("name", "")) == _sheet_key(character) and p.get("concentration"):
                dropped, name = str(p["concentration"]), p.get("name") or character
                p["concentration"] = None
                p["effects"] = [e for e in p.get("effects", []) if not (isinstance(e, dict) and e.get("concentration")
                                                                        and str(e.get("name", "")).lower() == dropped.lower())]
                snapshot = dict(_current_stats)
    if not dropped:
        return jsonify({"error": "not concentrating", "code": "none"}), 409
    _persist_stats()
    _broadcast({"stats": snapshot})
    _spellbook.casts_add({"character": name, "spell": dropped, "dropped": True})
    return jsonify(_spell_view(character, text, lang)), 200


# ─── Concentration saves ─────────────────────────────────────────────────────
# A character concentrating on a spell who takes damage makes a Constitution
# save, DC 10 or half the damage, whichever is higher; at 0 HP the
# concentration simply ends (PHB 2014 p. 203). The /stats handler notices the
# damage when an HP push lowers current or temporary HP; the save goes to the
# player's screen as a dice request (the table screen rolls it for a player
# with no phone) and the result is applied here: a failure ends the spell on
# the display, and the DM gets a [[CONCENTRATION: …]] line from check_input.py.

_conc_checks: dict = {}          # request_id → {"character", "spell", "dc", "damage"}
_conc_lock = threading.Lock()
_WAR_CASTER = re.compile(r"war caster|mago da guerra|incantatore da guerra", re.I)


def _concentration_hit(p: dict, hp_before: dict) -> Optional[dict]:
    """Damage just taken by a concentrating character: {name, spell, damage, down}. Call with _stats_lock held.

    At 0 HP the concentration ends here and now (the character is unconscious)."""
    spell = str(p.get("concentration") or "").strip()
    after = p.get("hp") if isinstance(p.get("hp"), dict) else {}
    if not spell or not isinstance(hp_before, dict) or "current" not in hp_before:
        return None
    try:
        lost = max(0, int(hp_before.get("current", 0)) - int(after.get("current", 0)))
        lost += max(0, int(hp_before.get("temp", 0) or 0) - int(after.get("temp", 0) or 0))
        down = int(after.get("current", 1)) <= 0
    except (TypeError, ValueError):
        return None
    if not lost:
        return None
    if down:
        p["concentration"] = None
        p["effects"] = [e for e in p.get("effects", []) if not (isinstance(e, dict) and e.get("concentration")
                                                                and str(e.get("name", "")).lower() == spell.lower())]
    return {"name": p.get("name", ""), "spell": spell, "damage": lost, "down": down}


def _con_save(character: str) -> tuple:
    """(Constitution save modifier, advantage) read from the sheet. War Caster gives advantage."""
    path, _safe, _camp = _find_sheet_file(character)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, TypeError):
        return 0, "normal"
    adv = "advantage" if _WAR_CASTER.search(text) else "normal"
    for heading in (r"##\s+Saving Throws", r"##\s+Ability Scores"):
        m = re.search(heading + r"[^\n]*\n(.*?)(?=\n##\s|\Z)", text, re.S)
        if not m:
            continue
        rows = [r for r in m.group(1).splitlines() if r.strip().startswith("|")]
        if len(rows) < 3:
            continue
        cols = [c.strip().upper() for c in rows[0].strip().strip("|").split("|")]
        cells = [c.strip() for c in rows[2].strip().strip("|").split("|")]
        if "CON" not in cols or cols.index("CON") >= len(cells):
            continue
        cell = cells[cols.index("CON")]
        found = (re.search(r"\(([+\-\u2212]?\d+)\)", cell) if "Ability" in heading
                 else re.search(r"([+\-\u2212]?\d+)", cell))
        if found:
            return int(found.group(1).replace("\u2212", "-")), adv
    return 0, adv


def _concentration_follow(hit: dict) -> None:
    """After the stats went out: open the save, or tell the DM the spell ended at 0 HP."""
    name, spell, damage = hit["name"], hit["spell"], hit["damage"]
    if hit.get("down"):
        _spellbook.casts_add({"character": name, "spell": spell, "concentration_save": "down", "damage": damage})
        _broadcast({"concentration_result": {"character": name, "spell": spell, "kept": False, "down": True}})
        return
    dc = max(10, damage // 2)
    modifier, adv = _con_save(name)
    it = _spell_lang().startswith("it")
    label = (f"Concentrazione: {spell}" if it else f"Concentration: {spell}")[:60]
    request_id, _waiting = _open_dice_request([name], "1d20", modifier, adv, label, dc)
    with _conc_lock:
        _conc_checks[request_id] = {"character": name, "spell": spell, "dc": dc, "damage": damage}
        while len(_conc_checks) > 50:
            _conc_checks.pop(next(iter(_conc_checks)))


def _concentration_result(request_id: str, character: str, total: int) -> None:
    """A concentration save came back: below the DC, the spell ends."""
    with _conc_lock:
        check = _conc_checks.get(request_id)
        if not check or check["character"].lower() != str(character).lower():
            return
        _conc_checks.pop(request_id, None)
    kept = total >= check["dc"]
    snapshot = None
    if not kept:
        with _stats_lock:
            for p in _current_stats.get("players", []):
                if p.get("name", "").lower() != check["character"].lower():
                    continue
                if str(p.get("concentration") or "").lower() == check["spell"].lower():
                    p["concentration"] = None
                    p["effects"] = [e for e in p.get("effects", []) if not (
                        isinstance(e, dict) and e.get("concentration")
                        and str(e.get("name", "")).lower() == check["spell"].lower())]
                    snapshot = dict(_current_stats)
        if snapshot is not None:
            _persist_stats()
            _broadcast({"stats": snapshot})
    _spellbook.casts_add({"character": check["character"], "spell": check["spell"],
                          "concentration_save": "kept" if kept else "lost",
                          "damage": check["damage"], "dc": check["dc"], "total": total})
    _broadcast({"concentration_result": {"character": check["character"], "spell": check["spell"], "kept": kept,
                                         "total": total, "dc": check["dc"]}})


# ─── Diseases, poisons and madness (afflictions.py) ──────────────────────────
# afflictions.py keeps them in <campaign>/afflictions.json and the calendar
# runs their course: at dawn a disease's save is opened on its character's
# phone (POST /afflictions/ask) and settled here when the roll comes back —
# exhaustion up or down, a DC that drops, damage — then the next save still
# due is asked. Conditions and damage reach the display through
# _afflictions_apply; the DM gets an [[AFFLICTION: …]] line from check_input.py.
import afflictions as _afflictions

_afflictions_lock = threading.Lock()


def _afflictions_file() -> Optional[str]:
    camp = re.sub(r"[^A-Za-z0-9_-]", "", _active_campaign())[:50]
    if not camp:
        return None
    try:
        return str(_find_campaign(camp) / "afflictions.json")
    except Exception:
        return None


def _afflictions_apply(character: str, add: list, remove: list, damage: int) -> None:
    """Conditions on and off, damage taken (temporary hit points first), as a push would do it."""
    snapshot, hit = None, None
    with _stats_lock:
        for p in _current_stats.get("players", []):
            if _sheet_key(p.get("name", "")) != _sheet_key(character):
                continue
            conds = [c for c in p.get("conditions", []) if c.lower() not in {str(r).lower() for r in remove}]
            timers = p.setdefault("condition_timers", {})
            for r in remove:
                timers.pop(str(r).lower(), None)
            for one in add:
                name = str(one.get("name", "") if isinstance(one, dict) else one).strip()
                if not name:
                    continue
                if name.lower() not in [c.lower() for c in conds]:
                    conds.append(name)
                timer = _condition_timer(one) if isinstance(one, dict) else None
                if timer:
                    timers[name.lower()] = timer
            p["conditions"] = conds
            if damage and isinstance(p.get("hp"), dict):
                before = dict(p["hp"])
                temp = int(p["hp"].get("temp", 0) or 0)
                soak = min(temp, damage)
                p["hp"]["temp"] = temp - soak
                p["hp"]["current"] = max(0, int(p["hp"].get("current", 0) or 0) - (damage - soak))
                hit = _concentration_hit(p, before)
            snapshot = dict(_current_stats)
            break
    if snapshot is not None:
        _persist_stats()
        _broadcast({"stats": snapshot})
    if hit:
        _concentration_follow(hit)


def _afflictions_open_due(path: str, again: bool = False) -> list:
    """Open on the phones the course saves that are due and not already waiting. Call with _afflictions_lock held."""
    data = _afflictions.load(path)
    with _dice_pending_lock:
        live = set(_dice_pending)
    lang = "it" if _spell_lang().startswith("it") else "en"
    asked = []
    seen = set()
    for e in data["list"]:
        if not e.get("due") or e.get("status") != "active":
            continue
        if e.get("pending") and (e["pending"] in live or not again):
            seen.add(_sheet_key(e["who"]))
            continue
        if _sheet_key(e["who"]) in seen:            # one save at a time per character: the next when this one is back
            continue
        path_s, _safe, _camp = _find_sheet_file(e["who"])
        try:
            with open(path_s, encoding="utf-8") as f:
                text = f.read()
        except (OSError, TypeError):
            text = ""
        mod = _afflictions.save_bonus(text, e.get("save", "CON")) if text else 0
        request_id, _waiting = _open_dice_request([e["who"]], "1d20", mod, "normal", _afflictions.label(e, lang), int(e["dc"]))
        e["pending"] = request_id
        seen.add(_sheet_key(e["who"]))
        asked.append(e["who"])
    _afflictions.save(path, data)
    return asked


def _affliction_result(request_id: str, character: str, total: int) -> None:
    """A course save came back: settle it, put the change on the display, ask the next one due."""
    path = _afflictions_file()
    if not path or not os.path.isfile(path):
        return
    with _afflictions_lock:
        try:
            data = _afflictions.load(path)
        except _afflictions.AfflictionError:
            return
        entry = next((e for e in data["list"] if e.get("pending") == request_id
                      and _sheet_key(e["who"]) == _sheet_key(character)), None)
        if entry is None:
            return
        try:
            ch = _afflictions.settle(data, entry, int(total), random.Random())
        except _afflictions.AfflictionError:
            return
        _afflictions.save(path, data)
        _afflictions_open_due(path)
    _afflictions_apply(ch["who"], ch["add"], ch["remove"], ch["damage"])
    _afflictions.notes_add(_afflictions.note_line(ch))
    _broadcast({"afflictions_event": {"character": ch["who"], "what": ch["event"], "name": ch["name"]}})


@app.route("/afflictions", methods=["GET"])
def afflictions_get():
    if not _token_ok():
        return "Forbidden", 403
    path = _afflictions_file()
    if not path:
        return jsonify({"afflictions": []}), 200
    me = g.character if getattr(g, "role", None) == "player" else str(request.args.get("character") or "")[:50]
    with _afflictions_lock:
        try:
            data = _afflictions.load(path)
        except _afflictions.AfflictionError:
            return jsonify({"afflictions": []}), 200
    camp = re.sub(r"[^A-Za-z0-9_-]", "", _active_campaign())[:50]
    view = _afflictions.public_view(data, me or "", dm=getattr(g, "role", None) != "player" and not me,
                                    now_h=_afflictions.now_hours(camp) if camp else None)
    return jsonify({"afflictions": view}), 200


@app.route("/afflictions/apply", methods=["POST"])
def afflictions_apply():
    """afflictions.py changed a character: {"character", "add", "remove", "damage", "event", "name"}."""
    body = request.get_json(silent=True) or {}
    character = str(body.get("character", ""))[:50].strip()
    add = [a for a in (body.get("add") or []) if isinstance(a, (str, dict))][:12]
    remove = [str(r)[:40] for r in (body.get("remove") or [])][:12]
    try:
        damage = max(0, min(500, int(body.get("damage") or 0)))
    except (TypeError, ValueError):
        damage = 0
    if character and (add or remove or damage):
        _afflictions_apply(character, add, remove, damage)
    what = str(body.get("event") or "changed")[:20]
    if what != "secret":
        _broadcast({"afflictions_event": {"character": character, "what": what, "name": str(body.get("name") or "")[:60]}})
    return "", 204


@app.route("/afflictions/ask", methods=["POST"])
def afflictions_ask():
    """The clock reached a dawn (or midnight): open the saves due on the phones. {"again": true} re-asks lost ones."""
    path = _afflictions_file()
    if not path or not os.path.isfile(path):
        return jsonify({"asked": []}), 200
    body = request.get_json(silent=True) or {}
    with _afflictions_lock:
        try:
            asked = _afflictions_open_due(path, again=bool(body.get("again")))
        except _afflictions.AfflictionError:
            asked = []
    return jsonify({"asked": asked}), 200


# ─── "Previously on…" (recap.py) ─────────────────────────────────────────────
# The DM's recap at the start of a session, as a full-screen card on every
# screen; the narrator reads it where narration is on. Kept until cleared so a
# player who joins later finds it too.
RECAP_FILE = rt("recap.json")


def _recap_now() -> Optional[dict]:
    try:
        with open(RECAP_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("text") or data.get("campaign", "") != _active_campaign():
        return None
    return data


@app.route("/recap", methods=["GET"])
def recap_get():
    if not _token_ok():
        return "Forbidden", 403
    return jsonify({"recap": _recap_now()}), 200


@app.route("/recap", methods=["POST"])
def recap_set():
    """Show a recap — {"text", "title"} — or {"clear": true}."""
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    if data.get("clear"):
        try:
            os.remove(RECAP_FILE)
        except OSError:
            pass
        _broadcast({"recap": None})
        return jsonify({"recap": None}), 200
    text = re.sub(r"[`\\$<>]", "", str(data.get("text", ""))).strip()[:4000]
    title = re.sub(r"[`\\$<>]", "", str(data.get("title", ""))).strip()[:120]
    if not text:
        return jsonify({"error": "no text"}), 400
    recap = {"id": secrets.token_hex(4), "title": title, "text": text, "campaign": _active_campaign(), "at": _time.time()}
    try:
        with open(RECAP_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(recap, f, ensure_ascii=False)
        os.replace(RECAP_FILE + ".tmp", RECAP_FILE)
    except OSError as e:
        return jsonify({"error": str(e)}), 500
    _broadcast({"recap": recap, "recap_new": True})
    return jsonify({"recap": recap}), 200


# ─── "While you were away…" (missed.py) ──────────────────────────────────────
# A personal recap for a player who missed a session, kept with the campaign
# in <campaign>/missed.json. Only that player gets it (a logged-in player
# their own; a phone view on this PC names its character), and they mark it
# read. A broadcast only says whose recaps changed: each screen asks again.
import missed as _missed


def _missed_path() -> Optional[str]:
    camp = re.sub(r"[^A-Za-z0-9_-]", "", _active_campaign())[:50]
    if not camp:
        return None
    try:
        return str(_find_campaign(camp) / "missed.json")
    except Exception:
        return None


def _missed_viewer() -> str:
    """Whose recaps this request may read: a player's own, or the character the DM's browser names."""
    if getattr(g, "role", None) == "player":
        return g.character or ""
    return str(request.args.get("character") or (request.get_json(silent=True) or {}).get("character") or "")[:60].strip()


@app.route("/missed", methods=["GET"])
def missed_get():
    if not _token_ok():
        return "Forbidden", 403
    path = _missed_path()
    try:
        recaps = _missed.load(path) if path else []
    except _missed.MissedError:
        recaps = []
    if getattr(g, "role", None) != "player" and request.args.get("all"):
        return jsonify({"recaps": recaps}), 200
    who = _missed_viewer()
    return jsonify({"recaps": _missed.for_character(recaps, who) if who else []}), 200


@app.route("/missed/read", methods=["POST"])
def missed_read():
    """{"id"} — the player has read it (the DM's browser adds "character")."""
    if not _token_ok():
        return "Forbidden", 403
    path, who = _missed_path(), _missed_viewer()
    rid = str((request.get_json(silent=True) or {}).get("id") or "")[:16]
    if not path or not who or not rid:
        return jsonify({"error": "nothing to mark"}), 400
    try:
        recaps = _missed.load(path)
        entry = _missed.mark_read(recaps, who, rid)
        if entry is None:
            return jsonify({"error": "not yours"}), 404
        _missed.save(path, recaps)
    except (OSError, _missed.MissedError) as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"recap": entry}), 200


@app.route("/missed-changed", methods=["POST"])
def missed_changed():
    """missed.py wrote or removed a recap: tell the screens whose it is."""
    who = str((request.get_json(silent=True) or {}).get("character") or "")[:60].strip()
    _broadcast({"missed_changed": {"character": who}})
    return jsonify({"ok": True}), 200


# ─── Votes (vote.py) ─────────────────────────────────────────────────────────
# The DM asks the table (or one group of a split party) a question; each voter
# taps an answer on their own screen and may change it while the vote is open.
# When everybody has voted the vote ends by itself: the result goes into the
# story (for the voters only, when they are not the whole party) and to the DM.
import vote as _vote_lib

VOTE_FILE = rt("vote.json")
_vote: Optional[dict] = None
_vote_lock = threading.Lock()


def _load_vote() -> None:
    global _vote
    try:
        with open(VOTE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        _vote = data if isinstance(data, dict) and data.get("options") else None
    except (OSError, ValueError):
        _vote = None


def _persist_vote() -> None:
    try:
        with open(VOTE_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(_vote, f, ensure_ascii=False)
        os.replace(VOTE_FILE + ".tmp", VOTE_FILE)
    except OSError:
        pass


_load_vote()


def _clear_vote() -> None:
    """No vote any more: the panel and the menu entry go away on every screen."""
    global _vote
    with _vote_lock:
        had = _vote is not None
        _vote = None
        _persist_vote()
    if had:
        _broadcast({"vote_event": {"cleared": True}})


def _vote_now() -> Optional[dict]:
    """The vote of the campaign being played, open or just ended (call with _vote_lock held)."""
    if _vote is None or _vote.get("campaign", "") != _active_campaign():
        return None
    return _vote


def _vote_view(vote: Optional[dict]) -> Optional[dict]:
    """What this screen may see: a secret vote hides the count and who chose what until it ends."""
    if not vote:
        return None
    me = (g.character or "") if getattr(g, "role", None) == "player" else ""
    hidden = vote.get("secret") and not vote.get("closed") and getattr(g, "role", None) == "player"
    votes = vote.get("votes") or {}
    out = {k: vote[k] for k in ("id", "question", "options", "voters", "group", "secret", "closed")}
    out["voted"] = [n for n in vote["voters"] if n in votes]
    out["counts"] = None if hidden else _vote_lib.tally(vote)
    out["votes"] = None if hidden else dict(votes)
    out["mine"] = next((votes[n] for n in votes if me and n.lower() == me.lower()), None)
    out["winners"] = _vote_lib.winners(vote) if vote.get("closed") else []
    return out


def _vote_story(vote: dict) -> None:
    """The result as a line of the story, for the voters when they are not the whole table."""
    line = _vote_lib.result_line(vote)
    entry = {"text": line, "dice": True}
    with _stats_lock:
        party = {p.get("name", "") for p in _current_stats.get("players", [])}
    if set(vote["voters"]) != party:
        entry["only"] = list(vote["voters"])
    camp = _active_campaign()
    log_entry = dict(entry, _camp=camp) if camp else dict(entry)
    with _text_log_lock:
        _text_log.append(log_entry)
    with _tail_lock:
        _tail_buffer.append(dict(log_entry))
    _persist_log()
    _persist_tail()
    _broadcast(entry)


@app.route("/vote", methods=["GET"])
def vote_get():
    if not _token_ok():
        return "Forbidden", 403
    with _vote_lock:
        return jsonify({"vote": _vote_view(_vote_now())}), 200


@app.route("/vote", methods=["POST"])
def vote_set():
    """Open a vote — {"question", "options": [...], "voters": [...] (default: the party),
    "group": label, "secret": bool} — {"close": true} to end the open one, or
    {"clear": true} to take the vote, open or ended, off every screen."""
    global _vote
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    if data.get("clear"):
        _clear_vote()
        return jsonify({"vote": None}), 200
    if data.get("close"):
        with _vote_lock:
            vote = _vote_now()
            if vote is None or vote.get("closed"):
                return jsonify({"error": "no vote is open"}), 409
            vote["closed"] = True
            _persist_vote()
            snap = json.loads(json.dumps(vote))
        _vote_story(snap)
        _broadcast({"vote_event": {"id": snap["id"], "closed": True}})
        with _vote_lock:
            return jsonify({"vote": _vote_view(snap)}), 200
    clean = lambda s, n: re.sub(r"[`\\$<>]", "", str(s or "")).strip()[:n]  # noqa: E731
    question = clean(data.get("question"), 160)
    options = list(dict.fromkeys(o for o in (clean(o, 60) for o in (data.get("options") or [])) if o))
    if not question or not 2 <= len(options) <= _vote_lib.MAX_OPTIONS:
        return jsonify({"error": f"a question and 2 to {_vote_lib.MAX_OPTIONS} options"}), 400
    with _stats_lock:
        party = [str(p.get("name", "")) for p in _current_stats.get("players", []) if p.get("name")]
    asked = [str(n).strip() for n in (data.get("voters") or []) if str(n).strip()]
    voters = []
    for n in asked or party:
        hit = next((p for p in party if _sheet_key(p) == _sheet_key(n)), None)
        if not hit:
            return jsonify({"error": f"not at the table: {n}"}), 400
        if hit not in voters:
            voters.append(hit)
    if not voters:
        return jsonify({"error": "nobody at the table"}), 400
    with _vote_lock:
        _vote = {"id": secrets.token_hex(4), "question": question, "options": options, "voters": voters,
                 "group": clean(data.get("group"), 60), "secret": bool(data.get("secret")), "closed": False,
                 "votes": {}, "campaign": _active_campaign()}
        _persist_vote()
        snap = json.loads(json.dumps(_vote))
    _broadcast({"vote_event": {"id": snap["id"], "opened": True, "voters": voters, "question": question}})
    with _vote_lock:
        return jsonify({"vote": _vote_view(snap)}), 200


@app.route("/vote/cast", methods=["POST"])
def vote_cast():
    """A voter's answer: {"character", "option": index, "id"}. It can be changed until the vote ends."""
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr):
        return "Too Many Requests", 429
    data = request.get_json(silent=True) or {}
    character = _acting_character(str(data.get("character", ""))[:50].strip())
    try:
        option = int(data.get("option"))
    except (TypeError, ValueError):
        return jsonify({"error": "which answer?", "code": "option"}), 400
    over = None
    with _vote_lock:
        vote = _vote_now()
        if vote is None or vote.get("closed") or (data.get("id") and data.get("id") != vote["id"]):
            return jsonify({"error": "the vote is over", "code": "closed"}), 409
        name = next((n for n in vote["voters"] if _sheet_key(n) == _sheet_key(character)), None)
        if not name:
            return jsonify({"error": "not one of the voters", "code": "voter"}), 403
        if not 0 <= option < len(vote["options"]):
            return jsonify({"error": "which answer?", "code": "option"}), 400
        vote["votes"][name] = option
        if set(vote["votes"]) >= set(vote["voters"]):
            vote["closed"] = True
            over = json.loads(json.dumps(vote))
        _persist_vote()
        view = _vote_view(vote)
    if over:
        _vote_lib.notes_add(over)
        _vote_story(over)
    _broadcast({"vote_event": {"id": view["id"], "voter": name, "closed": bool(over)}})
    return jsonify({"vote": view}), 200


# ─── Downtime (downtime.py) ──────────────────────────────────────────────────
# The DM opens a stretch of free time with downtime.py; each player picks an
# activity on their own screen. The choices are kept in the campaign
# (<campaign>/downtime.json), so they wait for the DM across a restart; when
# the last player has chosen, the DM gets a [[DOWNTIME: …]] line.
import downtime as _downtime

_downtime_lock = threading.Lock()


def _downtime_file() -> Optional[str]:
    camp = re.sub(r"[^A-Za-z0-9_-]", "", _active_campaign())[:50]
    if not camp:
        return None
    try:
        return str(_find_campaign(camp) / "downtime.json")
    except Exception:
        return None


def _downtime_view(data: dict) -> Optional[dict]:
    me = g.character if getattr(g, "role", None) == "player" else str(request.args.get("character") or "")[:50]
    # A screen bound to a character sees its own choice only, whoever is logged in.
    return _downtime.public_view(data, me or "", dm=getattr(g, "role", None) != "player" and not me)


@app.route("/downtime", methods=["GET"])
def downtime_get():
    if not _token_ok():
        return "Forbidden", 403
    path = _downtime_file()
    if not path:
        return jsonify({"downtime": None}), 200
    with _downtime_lock:
        try:
            data = _downtime.load(path)
        except _downtime.DowntimeError:
            return jsonify({"downtime": None}), 200
    return jsonify({"downtime": _downtime_view(data)}), 200


@app.route("/downtime", methods=["POST"])
def downtime_changed():
    """downtime.py opened or closed a stretch of downtime: tell every screen."""
    data = request.get_json(silent=True) or {}
    event = data.get("event") if isinstance(data.get("event"), dict) else {}
    what = event.get("what") if event.get("what") in ("open", "closed") else "changed"
    _broadcast({"downtime_event": {"what": what, "days": event.get("days")}})
    return "", 204


@app.route("/downtime/choose", methods=["POST"])
def downtime_choose():
    """A player's choice: {"character", "activity", "detail"}. It can be changed while the window is open."""
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr):
        return "Too Many Requests", 429
    body = request.get_json(silent=True) or {}
    character = _acting_character(str(body.get("character", ""))[:50].strip())
    path = _downtime_file()
    if not path:
        return jsonify({"error": "no campaign", "code": "closed"}), 409
    ready = None
    with _downtime_lock:
        try:
            data = _downtime.load(path)
            name, everyone = _downtime.choose(data, character, str(body.get("activity", "")),
                                              str(body.get("detail", "")))
        except _downtime.DowntimeError as e:
            code = "closed" if "no downtime" in str(e) else "detail" if "say what" in str(e) else "choice"
            return jsonify({"error": str(e), "code": code}), 409
        win = data["window"]
        if everyone and not win.get("told"):
            win["told"] = True
            ready = _downtime.format_ready(win)
        _downtime.save(path, data)
        view = _downtime_view(data)
    if ready:
        _downtime.notes_add(ready)
    _broadcast({"downtime_event": {"what": "chosen", "character": name, "everyone": everyone}})
    return jsonify({"downtime": view}), 200


# ─── On the road (travel.py) ─────────────────────────────────────────────────
# The DM sets out with travel.py and keeps the pace; each player picks their
# character's role on the road (guide, lookout, mapper, forager) on their own
# screen. Kept in <campaign>/travel.json; when everybody has a role the DM gets
# a [[MARCH: …]] line. Whether the party is lost is the DM's secret until
# travel.py reveal.
import travel as _travel

_travel_lock = threading.Lock()


def _travel_file() -> Optional[str]:
    camp = re.sub(r"[^A-Za-z0-9_-]", "", _active_campaign())[:50]
    if not camp:
        return None
    try:
        return str(_find_campaign(camp) / "travel.json")
    except Exception:
        return None


def _travel_passives(data: dict) -> dict:
    out = {}
    for n in data.get("players") or []:
        nums = _dm_sheet_numbers(n)
        if nums.get("passive"):
            out[n] = nums["passive"]["perception"]
    return out


def _weather_perception() -> int:
    """What today's weather takes off the lookouts' passive Perception (weather.py)."""
    try:
        import weather as _weather
        return _weather.perception_penalty(_weather.current(_active_campaign()))
    except Exception:
        return 0


def _travel_view(data: dict, dm: Optional[bool] = None, me: str = "") -> Optional[dict]:
    me = me or (g.character if getattr(g, "role", None) == "player" else str(request.args.get("character") or "")[:50])
    if dm is None:
        dm = getattr(g, "role", None) != "player" and not me
    return _travel.public_view(data, me or "", dm=dm, passives=_travel_passives(data), weather_mod=_weather_perception())


@app.route("/travel", methods=["GET"])
def travel_get():
    if not _token_ok():
        return "Forbidden", 403
    path = _travel_file()
    if not path:
        return jsonify({"travel": None}), 200
    with _travel_lock:
        try:
            data = _travel.load(path)
        except _travel.TravelError:
            return jsonify({"travel": None}), 200
    return jsonify({"travel": _travel_view(data)}), 200


@app.route("/travel", methods=["POST"])
def travel_changed():
    """travel.py changed the journey: tell every screen."""
    data = request.get_json(silent=True) or {}
    event = data.get("event") if isinstance(data.get("event"), dict) else {}
    what = event.get("what") if event.get("what") in ("start", "stop", "pace", "role", "lost", "found") else "changed"
    _broadcast({"travel_event": {"what": what, "pace": event.get("pace") if event.get("pace") in _travel.PACES else None}})
    return "", 204


@app.route("/travel/role", methods=["POST"])
def travel_role():
    """A player's role on the road: {"character", "role"}. It can be changed while the party travels."""
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr):
        return "Too Many Requests", 429
    body = request.get_json(silent=True) or {}
    character = _acting_character(str(body.get("character", ""))[:50].strip())
    path = _travel_file()
    if not path:
        return jsonify({"error": "no campaign", "code": "off"}), 409
    ready = None
    with _travel_lock:
        try:
            data = _travel.load(path)
            name, everyone = _travel.set_role(data, character, str(body.get("role", ""))[:20])
        except _travel.TravelError as e:
            code = "off" if "not on the road" in str(e) else "taken" if "already the guide" in str(e) else "role"
            return jsonify({"error": str(e), "code": code}), 409
        if everyone and not data.get("told"):
            data["told"] = True
            ready = _travel.ready_line(data)
        _travel.save(path, data)
        view = _travel_view(data, me=name)
    if ready:
        _travel.notes_add(ready)
    _broadcast({"travel_event": {"what": "role", "character": name}})
    return jsonify({"travel": view}), 200


# ─── The DM's screen (/dm-screen) ────────────────────────────────────────────
# A page for the DM alone: the foes' hit points, the party's passive scores
# and save DCs, every clock and faction (secret ones too), the secret dates,
# the session prep, supplies and downtime, and dice rolled behind the screen —
# never broadcast, never in the story. Players are refused (DM-only endpoints).

DM_SKILLS = ("Acrobatics", "Animal Handling", "Arcana", "Athletics", "Deception", "History", "Insight",
             "Intimidation", "Investigation", "Medicine", "Nature", "Perception", "Performance", "Persuasion",
             "Religion", "Sleight of Hand", "Stealth", "Survival")
_dm_rolls: list = []                 # hidden rolls, oldest first
_dm_rolls_lock = threading.Lock()
_DM_ROLLS_KEPT = 40
_DM_NOTATION = re.compile(r"^\s*(\d{0,2})d(\d{1,3})\s*([+-]\s*\d{1,3})?\s*$", re.I)


def _dm_sheet_numbers(name: str) -> dict:
    """Skill bonuses, passive scores and spell save DC read from a PC's sheet."""
    path, _safe, _camp = _find_sheet_file(name)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, TypeError):
        return {}
    skills = {}
    for row in re.finditer(r"^\|\s*([A-Za-z ]+?)\s*\|\s*(STR|DEX|CON|INT|WIS|CHA)\s*\|\s*([+\-\u2212]?\d+)", text, re.M):
        if row.group(1) in DM_SKILLS:
            skills[row.group(1)] = int(row.group(3).replace("\u2212", "-"))
    passive = re.search(r"Passive Perception:\**\s*(\d+)", text)
    dc = re.search(r"(?:spell save DC|CD dei tiri salvezza)\D{0,4}(\d{1,2})", text, re.I)
    return {"skills": skills,
            "passive": {"perception": int(passive.group(1)) if passive else 10 + skills.get("Perception", 0),
                        "insight": 10 + skills.get("Insight", 0), "investigation": 10 + skills.get("Investigation", 0)},
            "spell_dc": int(dc.group(1)) if dc else None}


def _dm_json(folder, name: str):
    try:
        with open(folder / name, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return None


def _dm_roll_notation(notation: str, adv: str = "normal") -> dict:
    m = _DM_NOTATION.match(notation or "")
    if not m:
        raise ValueError("write e.g. 1d20+5, 2d6 or d100")
    n, sides = int(m.group(1) or 1), int(m.group(2))
    mod = int((m.group(3) or "0").replace(" ", ""))
    if not (1 <= n <= 20 and 2 <= sides <= 100):
        raise ValueError("1 to 20 dice of 2 to 100 sides")
    rolls = [secrets.randbelow(sides) + 1 for _ in range(n)]
    both = None
    if adv in ("advantage", "disadvantage") and n == 1 and sides == 20:
        other = secrets.randbelow(20) + 1
        both = [rolls[0], other]
        rolls = [max(both) if adv == "advantage" else min(both)]
    return {"notation": f"{n}d{sides}" + (f"{mod:+d}" if mod else ""), "rolls": rolls, "both": both,
            "total": sum(rolls) + mod, "advantage": adv}


def _dm_party() -> list:
    with _stats_lock:
        return [dict(p) for p in _current_stats.get("players", []) if isinstance(p, dict) and p.get("name")]


@app.route("/dm-screen", methods=["GET"])
def dm_screen():
    return render_template("dm.html", i18n=_load_i18n_json(), skills=list(DM_SKILLS))


@app.route("/dm-screen/data", methods=["GET"])
def dm_screen_data():
    camp = re.sub(r"[^A-Za-z0-9_-]", "", _active_campaign())[:50]
    folder = None
    if camp:
        try:
            folder = _find_campaign(camp)
        except Exception:
            folder = None
    with _stats_lock:
        order = json.loads(json.dumps(_current_stats.get("turn_order") or {}))
        world = json.loads(json.dumps(_current_stats.get("world_time") or {}))
    party = []
    for p in _dm_party():
        nums = _dm_sheet_numbers(p["name"])
        party.append({"name": p["name"], "hp": p.get("hp"), "ac": p.get("ac"), "conditions": p.get("conditions") or [],
                      "concentration": p.get("concentration"), "load": (p.get("load") or {}).get("status"),
                      "passive": nums.get("passive"), "spell_dc": nums.get("spell_dc")})
    foes = []
    tracker = _dm_json(folder, "tracker.json") if folder else None
    in_order = {str(n).lower() for n in (order.get("order") or [])} if isinstance(order, dict) else set()
    pcs = {p["name"].lower() for p in party}
    for key, ent in (tracker or {}).items():
        if not isinstance(ent, dict) or str(ent.get("name", key)).lower() in pcs:
            continue
        if ent.get("hp") is None and str(ent.get("name", key)).lower() not in in_order:
            continue
        foes.append({"name": ent.get("name", key), "hp": ent.get("hp"), "max_hp": ent.get("max_hp"), "ac": ent.get("ac"),
                     "conditions": ent.get("conditions") or [], "concentration": ent.get("concentration"),
                     "legendary": ent.get("legendary") if isinstance(ent.get("legendary"), dict) else None,
                     "morale": ({k: ent["morale"].get(k) for k in ("group", "leader", "due", "broken", "fearless")}
                                if isinstance(ent.get("morale"), dict) else None)})
    clocks = (_dm_json(folder, "clocks.json") or {}).get("clocks", []) if folder else []
    factions = []
    for f in ((_dm_json(folder, "factions.json") or {}).get("factions", []) if folder else []):
        if isinstance(f, dict) and f.get("name"):
            score = int(f.get("score", 0))
            factions.append({"name": f["name"], "score": score, "standing": _factions.standing(max(-10, min(10, score))),
                             "secret": bool(f.get("secret"))})
    secrets_list = []
    cal = _dm_json(folder, "calendar.json") if folder else None
    for e in (cal or {}).get("events") or []:
        if isinstance(e, dict) and e.get("secret"):
            secrets_list.append({"what": e.get("name", ""), "when": f"{e.get('day', '?')}/{e.get('month', '?')}"
                                 + (f"/{e['year']}" if e.get("year") else ""), "note": e.get("note", ""), "kind": "calendar"})
    for e in ((_dm_json(folder, "timeline.json") or {}).get("events", []) if folder else []):
        if isinstance(e, dict) and e.get("secret"):
            secrets_list.append({"what": e.get("title", ""), "when": e.get("date", ""), "note": e.get("note", ""),
                                 "kind": "timeline"})
    prep = ""
    if folder:
        try:
            prep = (folder / "prep.md").read_text(encoding="utf-8")[:60000]
        except OSError:
            prep = ""
    downtime = None
    if folder and (folder / "downtime.json").is_file():
        try:
            downtime = _downtime.public_view(_downtime.load(str(folder / "downtime.json")), dm=True)
        except Exception:
            downtime = None
    with _dm_rolls_lock:
        rolls = list(_dm_rolls)[::-1]
    missed = []
    if folder:
        try:
            missed = [{"character": r["character"], "session": r.get("session", ""), "read": bool(r.get("read_at"))}
                      for r in _missed.load(str(folder / "missed.json"))][-12:]
        except _missed.MissedError:
            missed = []
    traps = []
    for t in ((_dm_json(folder, "traps.json") or {}).get("traps", []) if folder else []):
        if isinstance(t, dict) and t.get("name"):
            traps.append({k: t.get(k) for k in ("name", "where", "at", "status", "spot", "investigate", "disarm", "dispel",
                                                 "magic", "noticed_by", "found_by", "trigger")})
    afflictions = []
    if folder and (folder / "afflictions.json").is_file():     # every affliction, incubating ones too
        try:
            afflictions = _afflictions.public_view(_afflictions.load(str(folder / "afflictions.json")), dm=True,
                                                   now_h=_afflictions.now_hours(camp))
        except Exception:
            afflictions = []
    travel = None
    if folder and (folder / "travel.json").is_file():     # the journey: pace, roles, watch, lost (secret)
        try:
            travel = _travel_view(_travel.load(str(folder / "travel.json")), dm=True)
        except Exception:
            travel = None
    wander = None
    if folder and (folder / "wander.json").is_file():     # random encounters (wander.py): the zone and its table
        try:
            import wander as _wander
            wander = _wander.dm_view(_wander.load(str(folder / "wander.json")), camp)
        except Exception:
            wander = None
    rumors = None
    if folder and (folder / "rumors.json").is_file():       # rumors: how many are left, and which were true
        try:
            rumors = _rumors.dm_view(_rumors.load(str(folder / "rumors.json")))
        except Exception:
            rumors = None
    return jsonify({
        "rumors": rumors,
        "traps": traps,
        "afflictions": afflictions,
        "travel": travel,
        "wander": wander,
        "missed": missed,
        "campaign": camp, "world": {k: world.get(k) for k in ("date", "time", "weather", "weather_effects") if world.get(k)},
        "order": order if isinstance(order, dict) else {}, "party": party, "foes": foes, "clocks": clocks,
        "factions": factions, "secrets": secrets_list, "prep": prep, "supplies": _public_supplies(),
        "downtime": downtime, "dice_pending": _dice_pending_snapshot(), "rolls": rolls,
        "dcs": [["very_easy", 5], ["easy", 10], ["medium", 15], ["hard", 20], ["very_hard", 25], ["nearly_impossible", 30]],
    }), 200


@app.route("/dm-screen/roll", methods=["POST"])
def dm_screen_roll():
    """A roll behind the screen: {"notation", "label", "advantage"} or {"skill"} for every PC at once."""
    data = request.get_json(silent=True) or {}
    label = re.sub(r"[`\\$<>]", "", str(data.get("label") or ""))[:60].strip()
    adv = str(data.get("advantage") or "normal")
    adv = adv if adv in ("advantage", "disadvantage") else "normal"
    skill = str(data.get("skill") or "")
    if skill:
        if skill not in DM_SKILLS:
            return jsonify({"error": "unknown skill"}), 400
        lines = []
        for p in _dm_party():
            bonus = _dm_sheet_numbers(p["name"]).get("skills", {}).get(skill, 0)
            r = _dm_roll_notation(f"1d20{bonus:+d}", adv)
            lines.append({"who": p["name"], **r})
        entry = {"label": label or skill, "skill": skill, "group": lines, "ts": _time.time()}
    else:
        try:
            r = _dm_roll_notation(str(data.get("notation") or ""), adv)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        entry = {"label": label, **r, "ts": _time.time()}
    with _dm_rolls_lock:
        _dm_rolls.append(entry)
        del _dm_rolls[:-_DM_ROLLS_KEPT]
    return jsonify(entry), 200


# ─── Chases (chase.py) ───────────────────────────────────────────────────────
# A quarry and its pursuers on one line (DMG 2014 pp. 252-255). The server
# keeps the distances, the order, the dashes and the exhaustion; a player
# moves, dashes or stops on their own turn from the phone; the DM plays the
# creatures with chase.py and hears of the players' turns as [[CHASE: …]].
import chase as _chase_lib

CHASE_FILE = rt("chase.json")
_chase: Optional[dict] = None
_chase_lock = threading.Lock()


def _load_chase() -> None:
    global _chase
    try:
        with open(CHASE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        _chase = data if isinstance(data, dict) and data.get("runners") else None
    except (OSError, ValueError):
        _chase = None


def _persist_chase() -> None:
    try:
        if _chase is None:
            if os.path.exists(CHASE_FILE):
                os.remove(CHASE_FILE)
            return
        with open(CHASE_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(_chase, f, ensure_ascii=False)
        os.replace(CHASE_FILE + ".tmp", CHASE_FILE)
    except OSError:
        pass


_load_chase()


def _chase_now() -> Optional[dict]:
    """The chase of the campaign being played (call with _chase_lock held)."""
    if _chase is None or _chase.get("campaign", "") != _active_campaign():
        return None
    return _chase


def _chase_runner(raw, side: str) -> dict:
    """A runner sent by chase.py, rebuilt field by field."""
    if not isinstance(raw, dict):
        raise _chase_lib.ChaseError("a runner is not an object")
    num = lambda k, d, lo, hi: max(lo, min(hi, float(raw.get(k, d) if raw.get(k) is not None else d)))  # noqa: E731
    return _chase_lib.runner(str(raw.get("name", "")), side, num("speed", 9, 0, 60), int(num("con", 0, -5, 10)),
                             int(num("dex", 0, -5, 10)), int(num("stealth", 0, -5, 20)), int(num("passive", 10, 1, 30)),
                             bool(raw.get("pc")))


def _chase_publish(events: list, tell_dm: bool) -> dict:
    """Send every screen the chase and what happened; the DM's line when a player acted."""
    with _chase_lock:
        state = _chase_now() or _chase
        view = _chase_lib.public_view(state)
        note = _chase_lib.format_note(events, state) if (tell_dm and events and state) else ""
    if note:
        _chase_lib.notes_add(note)
    _broadcast({"chase": view, "chase_events": events[-8:]})
    return view


@app.route("/chase", methods=["GET"])
def chase_get():
    if not _token_ok():
        return "Forbidden", 403
    with _chase_lock:
        return jsonify({"chase": _chase_lib.public_view(_chase_now())}), 200


@app.route("/chase", methods=["POST"])
def chase_set():
    """chase.py: {"op": "start", "quarry": [...], "pursuers": [...], "gap", "setting"} | {"op": "act", "name",
    "action"} | {"op": "adjust", "name", "metres"} | {"op": "mark", "name", "status"} | {"op": "sight", "on"} |
    {"op": "cover", "cover"} | {"op": "end"}."""
    global _chase
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    op = data.get("op")
    events = []
    try:
        with _chase_lock:
            if op == "start":
                quarry = [_chase_runner(r, "quarry") for r in (data.get("quarry") or [])][:12]
                pursuers = [_chase_runner(r, "pursuer") for r in (data.get("pursuers") or [])][:12]
                _chase = _chase_lib.start(quarry, pursuers, float(data.get("gap") or 0), str(data.get("setting") or "urban"),
                                          campaign=_active_campaign())
                events = [_chase_lib._say(_chase, "start", "the chase begins", gap=data.get("gap"))]
            elif op == "end":
                if _chase_now() is None:
                    return jsonify({"error": "no chase"}), 409
                _chase = None
                _persist_chase()
                _broadcast({"chase": None})
                return jsonify({"chase": None, "events": ["the chase is over"]}), 200
            else:
                state = _chase_now()
                if state is None:
                    return jsonify({"error": "no chase"}), 409
                if op == "act":
                    events = _chase_lib.act(state, str(data.get("name", "")), str(data.get("action", "")))
                elif op == "adjust":
                    events = [_chase_lib.adjust(state, str(data.get("name", "")), float(data.get("metres") or 0))]
                elif op == "mark":
                    events = [_chase_lib.mark(state, str(data.get("name", "")), str(data.get("status", "")))]
                elif op == "sight":
                    state["sight"] = bool(data.get("on"))
                    events = [_chase_lib._say(state, "sight.on" if state["sight"] else "sight.off",
                                              "the quarry is in sight" if state["sight"] else "the quarry is out of sight")]
                elif op == "cover":
                    if data.get("cover") not in _chase_lib.COVERS:
                        return jsonify({"error": "cover is crowded, normal or open"}), 400
                    state["cover"] = data["cover"]
                    events = [_chase_lib._say(state, "cover." + state["cover"], f"cover: {state['cover']}")]
                else:
                    return jsonify({"error": "unknown op"}), 400
            _persist_chase()
    except (_chase_lib.ChaseError, TypeError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    view = _chase_publish(events, tell_dm=False)
    return jsonify({"chase": view, "events": [e["text"] for e in events]}), 200


@app.route("/chase/act", methods=["POST"])
def chase_act():
    """A player's turn: {"character", "action": "move"|"dash"|"stop", "id"}."""
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr):
        return "Too Many Requests", 429
    body = request.get_json(silent=True) or {}
    character = _acting_character(str(body.get("character", ""))[:50].strip())
    with _chase_lock:
        state = _chase_now()
        if state is None or state.get("over") or (body.get("id") and body.get("id") != state["id"]):
            return jsonify({"error": "no chase", "code": "over"}), 409
        try:
            runner = _chase_lib.find(state, character)
        except _chase_lib.ChaseError:
            return jsonify({"error": "not in the chase", "code": "runner"}), 403
        if not runner.get("pc"):
            return jsonify({"error": "not in the chase", "code": "runner"}), 403
        try:
            events = _chase_lib.act(state, runner["name"], str(body.get("action", "")))
        except _chase_lib.ChaseError as e:
            return jsonify({"error": str(e), "code": "turn" if "turn" in str(e) else "action"}), 409
        _persist_chase()
    view = _chase_publish(events, tell_dm=True)
    return jsonify({"chase": view}), 200


# ─── Attacks (scripts/attacks.py) ────────────────────────────────────────────
# A player picks a weapon from the sheet's Attacks table, a target and
# advantage; the server rolls to hit and for damage in one go, puts the line in
# the story and stages it as the player's action for the DM, already rolled.
import attacks as _attacks


def _attack_targets() -> list:
    """The creatures of the initiative order that are not in the party."""
    with _stats_lock:
        party = {_sheet_key(p.get("name", "")) for p in _current_stats.get("players", [])}
        to = _current_stats.get("turn_order") if isinstance(_current_stats.get("turn_order"), dict) else {}
        order = list(to.get("order") or [])
    out = []
    for e in order:
        name = str(e.get("name", "") if isinstance(e, dict) else e).strip()
        if name and _sheet_key(name) not in party and name not in out:
            out.append(name)
    return out


def _attack_sheet(character: str):
    """(name, attacks) of a character this screen may attack with, or (None, an error response)."""
    if not _token_ok() or _is_other_character(character):
        return None, ("Forbidden", 403)
    path, safe, _camp = _find_sheet_file(character)
    if not safe:
        return None, ("Bad character name", 400)
    if not path:
        return None, (jsonify({"error": "no sheet"}), 404)
    try:
        with open(path, encoding="utf-8") as f:
            rows = _attacks.read_attacks(f.read())
    except OSError as e:
        return None, (jsonify({"error": str(e)}), 500)
    name = (_live_player(character) or {}).get("name") or character
    beast = _form_attacks(name)                  # in a beast's body: its bite and claws, not the sword (shapes.py)
    return name, (beast if beast is not None else rows)


@app.route("/attacks", methods=["GET"])
def attacks_list():
    """The characters this screen may attack with: every one at the table for the DM, a player's own."""
    if not _token_ok():
        return "Forbidden", 403
    with _stats_lock:
        names = [str(p.get("name", "")) for p in _current_stats.get("players", []) if p.get("name")]
    if g.role == "player":
        names = [n for n in names if n.lower() == (g.character or "").lower()] or [g.character or ""]
    out = []
    for name in names:
        path, _safe, _camp = _find_sheet_file(name)
        if not path:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                count = len(_attacks.read_attacks(f.read()))
        except OSError:
            continue
        if count:
            out.append({"character": name, "attacks": count})
    return jsonify(out), 200


@app.route("/attacks/<character>", methods=["GET"])
def attacks_get(character):
    name, rows = _attack_sheet(character)
    if name is None:
        return rows
    return jsonify({"character": name, "attacks": rows, "targets": _attack_targets()}), 200


@app.route("/attacks/<character>/roll", methods=["POST"])
def attacks_roll(character):
    """Roll an attack: {"attack", "target", "advantage", "alt", "extra"}. The result is in the
    story at once and staged as the character's action, ready, for the DM."""
    if not _rate_ok(request.remote_addr):
        return "Too Many Requests", 429
    status = _device_ok(request.headers.get("X-DND-Device", ""), request.remote_addr)
    if status == "denied":
        return "Forbidden", 403
    if status == "pending":
        return jsonify({"status": "pending"}), 202
    name, rows = _attack_sheet(character)
    if name is None:
        return rows
    data = request.get_json(silent=True) or {}
    target = re.sub(r"[`\\$<>\[\]]", "", str(data.get("target", ""))).strip()[:40]
    try:
        attack = _attacks.find(rows, str(data.get("attack", ""))[:80])
        result = _attacks.roll(attack, str(data.get("advantage", "normal")), int(data.get("alt") or 0), bool(data.get("extra")),
                               wind=_attacks.weather_wind(_active_campaign()))     # a strong wind today (weather.py)
    except (_attacks.AttackError, ValueError, TypeError) as e:
        code = "ammo" if "left for" in str(e) else "attack"
        return jsonify({"error": str(e), "code": code}), 409
    path, _safe, camp = _find_sheet_file(character)
    _attacks.with_cover(result, _attacks.map_cover(_active_campaign(), name, target))   # the last battle map
    if path:
        _attacks.spend_ammo(attack, result, path, camp or "")       # an arrow off the sheet (ammo.py)
    lang = _spell_lang(data.get("lang", ""))
    line = _attacks.story_line(name, target, result, lang)
    entry = {"text": line, "dice": True}
    camp = _active_campaign()
    if camp:
        entry["_camp"] = camp
    with _text_log_lock:
        _text_log.append(dict(entry))
    with _tail_lock:
        _tail_buffer.append(dict(entry))
    _persist_log()
    _persist_tail()
    _broadcast({"text": line, "dice": True})

    action = _attacks.action_line(target, result, lang)
    with _staged_lock:
        before = _staged.get(name, {}).get("text", "")
        _staged[name] = {"text": (before + " — " if before else "") + action, "ready": True, "timestamp": _time.time()}
        snap = _staged_snapshot()
    _broadcast({"staged_inputs": snap, "attack": {"character": name, "target": target, "attack": result["attack"]}})
    _check_auto_trigger()
    return jsonify({"result": result, "line": line}), 200


# ─── Rests (scripts/rest.py) ─────────────────────────────────────────────────
# The DM opens a short or long rest (rest.py open); every resting player's
# screen shows a Rest panel. A short rest spends Hit Dice one at a time (rolled
# here, plus CON), a long rest brings HP, Hit Dice and slots back in one tap.
# The sheet and the display change together, and the DM is told (check_input.py).
# A player acts for their own character, the DM's screen for anyone resting.
import rest as _rest_lib

REST_FILE = rt("rest.json")
_rest: Optional[dict] = None
_rest_lock = threading.Lock()


def _load_rest() -> None:
    global _rest
    try:
        with open(REST_FILE, encoding="utf-8") as f:
            data = json.load(f)
        _rest = data if isinstance(data, dict) and data.get("kind") in _rest_lib.KINDS else None
    except (OSError, ValueError):
        _rest = None


def _persist_rest() -> None:
    try:
        if _rest is None:
            if os.path.exists(REST_FILE):
                os.remove(REST_FILE)
            return
        with open(REST_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(_rest, f, ensure_ascii=False)
        os.replace(REST_FILE + ".tmp", REST_FILE)
    except OSError:
        pass


_load_rest()


def _rest_now() -> Optional[dict]:
    """The open rest, if it belongs to the campaign being played (call with _rest_lock held)."""
    if _rest is None or _rest.get("campaign", "") != _active_campaign():
        return None
    return _rest


def _rest_snapshot() -> Optional[dict]:
    with _rest_lock:
        r = _rest_now()
        return json.loads(json.dumps(r)) if r else None


def _resting_name(rest: Optional[dict], character: str) -> str:
    """The resting character's name as the rest lists it, or ''."""
    key = _sheet_key(character)
    return next((n for n in (rest or {}).get("characters", []) if _sheet_key(n) == key), "")


def _rest_view(character: str, text: str) -> dict:
    st = _rest_lib.state(text, _live_player(character) or {})
    slots = _live_slots(_live_player(character)) or _spellbook.read_slots(text)
    rest = _rest_snapshot()
    name = _resting_name(rest, character)
    return {"character": (_live_player(character) or {}).get("name") or character,
            "hp": st["hp"], "hit_dice": st["hit_dice"], "con": st["con"], "warlock": st["warlock"],
            "slots": {str(k): v for k, v in sorted(slots.items())},
            "rest": rest, "resting": bool(name), "done": bool(name and name in rest.get("done", []))}


def _rest_to_display(character: str, text: str, second_wind: bool = False, long_rest: bool = False) -> None:
    """The display's own copy of the character follows the sheet: HP, Hit Dice, slots."""
    hp, hd = _rest_lib.read_hp(text), _rest_lib.read_hit_dice(text)
    slots = _spellbook.read_slots(text)
    snapshot = None
    with _stats_lock:
        for p in _current_stats.get("players", []):
            if _sheet_key(p.get("name", "")) != _sheet_key(character):
                continue
            if hp:
                p["hp"] = {**(p.get("hp") if isinstance(p.get("hp"), dict) else {}),
                           "current": hp["current"], "max": hp["max"], "temp": hp["temp"]}
            if hd:
                p["hit_dice"] = {"remaining": hd["remaining"], "max": hd["max"]}
            if slots:
                p["spell_slots"] = {str(k): {"used": v["used"], "max": v["max"]} for k, v in slots.items()}
            if second_wind and "second_wind" in p:
                p["second_wind"] = True
            if long_rest and isinstance(p.get("death_saves"), dict) and not p["death_saves"].get("dead"):
                p.pop("death_saves", None)
            _death_saves_follow_hp(p)
            snapshot = dict(_current_stats)
    if snapshot is not None:
        _persist_stats()
        _broadcast({"stats": snapshot})


def _write_sheet(path: str, text: str):
    """None, or an error response."""
    try:
        with open(path + ".tmp", "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(path + ".tmp", path)
    except OSError as e:
        return jsonify({"error": str(e), "code": "sheet"}), 500
    return None


@app.route("/rest", methods=["GET"])
def rest_get():
    if not _token_ok():
        return "Forbidden", 403
    return jsonify({"rest": _rest_snapshot()}), 200


@app.route("/rest", methods=["POST"])
def rest_set():
    """Open a rest — {"kind": "short"|"long", "characters": [...] (default: the party)} — or {"close": true}."""
    global _rest
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    if data.get("close"):
        with _rest_lock:
            _rest = None
            _persist_rest()
        _broadcast({"rest": None})
        return jsonify({"rest": None}), 200
    kind = str(data.get("kind", "")).strip().lower()
    if kind not in _rest_lib.KINDS:
        return jsonify({"error": "kind is short or long"}), 400
    with _stats_lock:
        party = [str(p.get("name", "")) for p in _current_stats.get("players", []) if p.get("name")]
    asked = [str(n).strip() for n in (data.get("characters") or []) if str(n).strip()]
    if asked:
        names, unknown = [], []
        for n in asked:
            hit = next((p for p in party if _sheet_key(p) == _sheet_key(n)), None)
            (names if hit else unknown).append(hit or n)
        if unknown:
            return jsonify({"error": "not at the table: " + ", ".join(unknown)}), 400
    else:
        names = party
    if not names:
        return jsonify({"error": "nobody at the table"}), 400
    with _rest_lock:
        _rest = {"kind": kind, "campaign": _active_campaign(), "characters": list(dict.fromkeys(names)), "done": []}
        _persist_rest()
        snap = json.loads(json.dumps(_rest))
    _broadcast({"rest": snap, "rest_opened": True})
    return jsonify({"rest": snap}), 200


def _rest_sheet(character: str):
    """(path, text, resting name) for an action of the open rest, or (None, error response, '')."""
    path, text = _spell_sheet(character)
    if path is None:
        return None, text, ""
    with _rest_lock:
        rest = _rest_now()
        name = _resting_name(rest, character)
        if rest is None:
            return None, (jsonify({"error": "no rest is open", "code": "closed"}), 409), ""
        if not name:
            return None, (jsonify({"error": "not resting", "code": "resting"}), 409), ""
        if name in rest.get("done", []):
            return None, (jsonify({"error": "already rested", "code": "done"}), 409), ""
    return path, text, name


@app.route("/rest/<character>", methods=["GET"])
def rest_character(character):
    path, text = _spell_sheet(character)
    if path is None:
        return text
    return jsonify(_rest_view(character, text)), 200


@app.route("/rest/<character>/hit-die", methods=["POST"])
def rest_hit_die(character):
    """Spend one Hit Die on a short rest: rolled here, written to the sheet and the display."""
    with _spell_lock:
        path, text, name = _rest_sheet(character)
        if path is None:
            return text
        if (_rest_snapshot() or {}).get("kind") != "short":
            return jsonify({"error": "Hit Dice are spent on a short rest", "code": "kind"}), 409
        try:
            new_text, done = _rest_lib.spend_hit_die(text, _live_player(character) or {})
        except _rest_lib.RestError as e:
            code = "none" if "no Hit Dice" in str(e) else "full" if "full" in str(e) else "sheet"
            return jsonify({"error": str(e), "code": code}), 409
        err = _write_sheet(path, new_text)
        if err:
            return err
        _rest_to_display(character, new_text)
    _rest_lib.notes_add({"kind": "hd", "character": name, **done})
    _broadcast({"rest_event": {"character": name, "kind": "hd", "gained": done["gained"], "hp": done["hp"], "max": done["max"]}})
    return jsonify({**_rest_view(character, new_text), "spent": done}), 200


@app.route("/rest/<character>/finish", methods=["POST"])
def rest_finish(character):
    """The character's rest is over: a long rest is applied now; a short one only recharges."""
    global _rest
    with _spell_lock:
        path, text, name = _rest_sheet(character)
        if path is None:
            return text
        kind = (_rest_snapshot() or {}).get("kind")
        try:
            if kind == "long":
                new_text, done = _rest_lib.long_rest(text, _spell_ruleset(), _live_player(character) or {})
            else:
                new_text, done = _rest_lib.finish_short(text)
        except (_rest_lib.RestError, _spellbook.SpellError) as e:
            return jsonify({"error": str(e), "code": "sheet"}), 409
        if new_text != text:
            err = _write_sheet(path, new_text)
            if err:
                return err
        _rest_to_display(character, new_text, second_wind=True, long_rest=kind == "long")
        try:
            _shapes.rested(_active_campaign(), name)          # Wild Shape uses back on a short or long rest
        except Exception:
            pass
    over = None
    with _rest_lock:
        rest = _rest_now()
        if rest is not None and name not in rest["done"]:
            rest["done"].append(name)
            if set(rest["done"]) >= set(rest["characters"]):
                over, _rest = rest["kind"], None
            _persist_rest()
        snap = json.loads(json.dumps(_rest_now())) if _rest_now() else None
    _rest_lib.notes_add({"kind": kind, "character": name, **done})
    if over:
        _rest_lib.notes_add({"kind": "over", "rest": over})
    _broadcast({"rest": snap, "rest_event": {"character": name, "kind": kind, "over": bool(over)}})
    return jsonify({**_rest_view(character, new_text), "finished": done}), 200


# ─── Companions (companion.py) ───────────────────────────────────────────────
# Animals and followers travelling with the party, kept by companion.py in
# <campaign>/companions.json. They are not players: they hang on their owner's
# card as chips and have a small sheet of their own.
import companion as _companion


def _public_companions() -> list:
    camp = re.sub(r"[^A-Za-z0-9_-]", "", _active_campaign())[:50]
    if not camp:
        return []
    try:
        return _companion.public_view(_companion.load(str(_find_campaign(camp) / "companions.json")))
    except Exception:
        return []


@app.route("/companions", methods=["GET"])
def companions_get():
    return jsonify(_public_companions()), 200


@app.route("/companions", methods=["POST"])
def companions_changed():
    """companion.py changed one: send every screen the list, and what happened."""
    data = request.get_json(silent=True) or {}
    payload = {"companions": _public_companions()}
    event = data.get("event")
    if isinstance(event, dict) and event.get("name"):
        payload["companion_event"] = {k: event.get(k) for k in ("name", "what", "delta", "current", "max")}
    _broadcast(payload)
    return "", 204


# ─── Vehicles (vehicles.py) ──────────────────────────────────────────────────
# Carts, barges and ships the party travels in, kept by vehicles.py in
# <campaign>/vehicles.json: every screen sees them, the one aboard on top.
import vehicles as _vehicles


def _public_vehicles() -> list:
    camp = re.sub(r"[^A-Za-z0-9_-]", "", _active_campaign())[:50]
    if not camp:
        return []
    try:
        data = _vehicles.load(str(_find_campaign(camp) / "vehicles.json"))
        return _vehicles.public_view(data, _vehicles._wind_strong(camp))
    except Exception:
        return []


@app.route("/vehicles", methods=["GET"])
def vehicles_get():
    return jsonify(_public_vehicles()), 200


@app.route("/vehicles", methods=["POST"])
def vehicles_changed():
    """vehicles.py changed one: send every screen the list, and what happened."""
    data = request.get_json(silent=True) or {}
    payload = {"vehicles": _public_vehicles()}
    event = data.get("event")
    if isinstance(event, dict) and event.get("name") and event.get("what") in ("new", "board", "leave", "hit", "sunk", "repair"):
        payload["vehicle_event"] = {"name": str(event["name"])[:50], "what": event["what"]}
    _broadcast(payload)
    return "", 204


# ─── Wild Shape and Polymorph (shapes.py) ────────────────────────────────────
# A character in a beast's body: shapes.py keeps the form in
# <campaign>/shapes.json and this server copies it onto the player as "form"
# for the cards. While the form lasts, an HP push for that character is damage
# (or healing) to the form: the character's own hit points stay as they were,
# and when the form drops to 0 the damage left over comes off them (PHB 2014
# p. 67). The Attacks panel rolls the form's attacks.
import shapes as _shapes


def _shapes_file() -> Optional[str]:
    camp = re.sub(r"[^A-Za-z0-9_-]", "", _active_campaign())[:50]
    if not camp:
        return None
    try:
        return str(_find_campaign(camp) / "shapes.json")
    except Exception:
        return None


def _forms_onto_players() -> None:
    """Copy every form onto its player. Call with _stats_lock held."""
    path = _shapes_file()
    try:
        data = _shapes.load(path) if path else _shapes.blank()
    except Exception:
        return
    for p in _current_stats.get("players", []):
        f = _shapes.form_of(data, p.get("name", ""))
        if f or p.get("form"):
            p["form"] = _shapes.public_form(f)


def _form_takes(p: dict, hp_before: dict) -> Optional[dict]:
    """An HP push for a character in another shape: the change goes to the form. Call with _stats_lock held.

    Returns {"line": for the DM, "conc": a concentration hit or None, "event": for the screens}, or None."""
    path = _shapes_file()
    after = p.get("hp") if isinstance(p.get("hp"), dict) else {}
    if "current" not in (hp_before or {}) or "current" not in after:
        return None
    try:
        delta = int(hp_before.get("current", 0)) - int(after.get("current", 0))
    except (TypeError, ValueError):
        return None
    if not path or not delta:
        return None
    data = _shapes.load(path)
    f = _shapes.form_of(data, p.get("name", ""))
    if not f:
        p["form"] = None
        return None
    p["hp"]["current"] = int(hp_before.get("current", 0))          # their own hit points wait under the form
    out = {"line": "", "conc": None, "event": {"what": "hp", "name": p.get("name", "")}}
    if delta < 0:
        _shapes.heal(f, -delta)
    else:
        r = _shapes.hit(f, delta)
        if p.get("concentration"):                                   # the form keeps the concentration, not the damage
            out["conc"] = {"name": p.get("name", ""), "spell": str(p["concentration"]), "damage": delta, "down": False}
        if r["ended"]:
            data["forms"].remove(f)
            cur = max(0, int(hp_before.get("current", 0)) - r["excess"])
            p["hp"]["current"] = cur
            out["line"] = (f"[[SHAPE: {p.get('name')} is back in their own shape — the {f['creature']} dropped to 0"
                           + (f" and {r['excess']} damage carried over: {cur} HP" if r["excess"] else ", no damage carried over")
                           + ".]]")
            out["event"] = {"what": "ended", "name": p.get("name", "")}
    _shapes.save(path, data)
    p["form"] = _shapes.public_form(_shapes.form_of(data, p.get("name", "")))
    return out


def _form_attacks(character: str) -> Optional[list]:
    path = _shapes_file()
    try:
        f = _shapes.form_of(_shapes.load(path), character) if path else None
    except Exception:
        return None
    return list(f.get("attacks") or []) if f else None


@app.route("/shapes", methods=["POST"])
def shapes_changed():
    """shapes.py changed a form: put it on the players and tell every screen."""
    data = request.get_json(silent=True) or {}
    with _stats_lock:
        _forms_onto_players()
        snapshot = dict(_current_stats)
    _persist_stats()
    payload = {"stats": snapshot}
    event = data.get("event")
    if isinstance(event, dict) and event.get("name") and event.get("what") in ("start", "ended", "hp"):
        payload["shape_event"] = {"what": event["what"], "name": str(event["name"])[:80],
                                  "creature": str(event.get("creature") or "")[:60]}
    _broadcast(payload)
    return "", 204


# ─── Rumors (rumors.py) ──────────────────────────────────────────────────────
# What is said in the taverns, kept by rumors.py in <campaign>/rumors.json.
# The players' screens get the rumors the party has heard — never whether
# they are true; the DM's screen gets that too.
import rumors as _rumors


def _rumors_file() -> Optional[str]:
    camp = re.sub(r"[^A-Za-z0-9_-]", "", _active_campaign())[:50]
    if not camp:
        return None
    try:
        return str(_find_campaign(camp) / "rumors.json")
    except Exception:
        return None


def _public_rumors() -> list:
    path = _rumors_file()
    try:
        return _rumors.public_view(_rumors.load(path)) if path else []
    except Exception:
        return []


@app.route("/rumors", methods=["GET"])
def rumors_get():
    return jsonify(_public_rumors()), 200


@app.route("/rumors", methods=["POST"])
def rumors_changed():
    """rumors.py changed them: send every screen what the party has heard, and who just heard something."""
    data = request.get_json(silent=True) or {}
    payload = {"rumors": _public_rumors()}
    event = data.get("event")
    if isinstance(event, dict) and event.get("what") == "heard" and event.get("who"):
        payload["rumor_event"] = {"who": str(event["who"])[:50], "n": int(event.get("n") or 0)}
    _broadcast(payload)
    return "", 204


# ─── Campaign book (scripts/campaign_book.py) ────────────────────────────────
# The chronicle of the campaign as a PDF, made by campaign_book.py into
# <campaign>/book/. Every player can download the last one made.

def _book_path():
    camp = re.sub(r"[^A-Za-z0-9_-]", "", _active_campaign())[:50]
    if not camp:
        return None
    path = _find_campaign(camp) / "book" / f"{camp}.pdf"
    return path if path.is_file() else None


def _book_info() -> dict:
    path = _book_path()
    if not path:
        return {"available": False}
    st = path.stat()
    return {"available": True, "updated": st.st_mtime, "size": st.st_size}


@app.route("/book/info", methods=["GET"])
def book_info():
    return jsonify(_book_info()), 200


@app.route("/book", methods=["GET"])
def book_download():
    path = _book_path()
    if not path:
        return "No book yet", 404
    return send_file(str(path), mimetype="application/pdf", as_attachment=True,
                     download_name=path.name, max_age=0)


@app.route("/book", methods=["POST"])
def book_changed():
    """campaign_book.py wrote a new book: tell every screen it can be downloaded."""
    _broadcast({"book": _book_info()})
    return "", 204


# ─── Puzzles (puzzle.py) ─────────────────────────────────────────────────────
# One puzzle at a time on the table: a combination lock, a riddle, a cipher.
# The answer stays here; the screens get only what public_view allows. Anyone
# may try, everyone sees each try, and the DM is told when it is over.
import puzzle as _puzzle_lib

PUZZLE_FILE = rt("puzzle.json")
_puzzle: Optional[dict] = None
_puzzle_lock = threading.Lock()


def _load_puzzle() -> None:
    global _puzzle
    try:
        with open(PUZZLE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("kind") in _puzzle_lib.KINDS:
            _puzzle = data
    except (OSError, ValueError):
        pass


def _persist_puzzle() -> None:
    try:
        if _puzzle is None:
            if os.path.exists(PUZZLE_FILE):
                os.remove(PUZZLE_FILE)
            return
        tmp = PUZZLE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_puzzle, f, ensure_ascii=False)
        os.replace(tmp, PUZZLE_FILE)
    except OSError:
        pass


_load_puzzle()

_PUZZLE_NEEDS = {"lock": ("symbols", "wheels", "solution"), "riddle": ("answers",), "cipher": ("cipher", "plain", "solution")}


@app.route("/puzzle", methods=["POST"])
def puzzle_open():
    """Put a puzzle on the table (replaces the one there). Body: {"puzzle": <made by puzzle.py>}."""
    global _puzzle
    data = (request.get_json(silent=True) or {}).get("puzzle")
    if not isinstance(data, dict) or data.get("kind") not in _puzzle_lib.KINDS or not data.get("title"):
        return jsonify({"error": "not a puzzle"}), 400
    if any(k not in data for k in _PUZZLE_NEEDS[data["kind"]]):
        return jsonify({"error": "the puzzle is incomplete"}), 400
    data = dict(data, id=secrets.token_hex(5), tries=[], used=0, solved=False, failed=False, solved_by="", last_try=0.0)
    with _puzzle_lock:
        _puzzle = data
        _persist_puzzle()
        view = _puzzle_lib.public_view(_puzzle)
    _broadcast({"puzzle": view, "puzzle_event": {"what": "new", "title": view["title"]}})
    return jsonify(view), 200


@app.route("/puzzle", methods=["GET"])
def puzzle_get():
    with _puzzle_lock:
        return jsonify(_puzzle_lib.public_view(_puzzle) or {}), 200


@app.route("/puzzle/status", methods=["GET"])
def puzzle_status():
    with _puzzle_lock:
        return jsonify(_puzzle_lib.public_view(_puzzle) or {}), 200


@app.route("/puzzle/hint", methods=["POST"])
def puzzle_hint():
    data = request.get_json(silent=True) or {}
    with _puzzle_lock:
        if _puzzle is None:
            return jsonify({"error": "no puzzle on the table"}), 404
        text = " ".join(str(data.get("text") or "").split())[:300]
        if text:
            _puzzle["hint"] = text
        if not _puzzle.get("hint"):
            return jsonify({"error": "this puzzle has no hint — pass one: puzzle.py hint \"…\""}), 400
        _puzzle["hint_shown"] = True
        _persist_puzzle()
        view = _puzzle_lib.public_view(_puzzle)
    _broadcast({"puzzle": view, "puzzle_event": {"what": "hint", "title": view["title"]}})
    return jsonify(view), 200


@app.route("/puzzle", methods=["DELETE"])
def puzzle_close():
    global _puzzle
    with _puzzle_lock:
        _puzzle = None
        _persist_puzzle()
    _broadcast({"puzzle": None})
    return jsonify({}), 200


@app.route("/puzzle/try", methods=["POST"])
def puzzle_try():
    """Anyone at the table tries the puzzle. A logged-in player tries as their own character."""
    data = request.get_json(silent=True) or {}
    who = re.sub(r"[`\\$<>]", "", _acting_character(str(data.get("character", ""))[:50])).strip()
    with _puzzle_lock:
        if _puzzle is None:
            return jsonify({"error": "no puzzle on the table", "code": "over"}), 404
        try:
            result = _puzzle_lib.attempt(_puzzle, who, data.get("guess"), _time.time())
        except _puzzle_lib.PuzzleError as e:
            code = "wait" if "wait" in str(e) else "over" if "over" in str(e) else "bad"
            return jsonify({"error": str(e), "code": code}), 409
        _persist_puzzle()
        view = _puzzle_lib.public_view(_puzzle)
        over = _puzzle["solved"] or _puzzle["failed"]
        line = _puzzle_lib.outcome_line(_puzzle) if over else ""
    if line:
        _puzzle_lib.news_add(line)
    _broadcast({"puzzle": view, "puzzle_event": {"what": "try", "title": view["title"], "who": who,
                                                 "guess": result["shown"], "ok": result["ok"],
                                                 "feedback": result["feedback"], "failed": view["failed"]}})
    return jsonify({"ok": result["ok"], "puzzle": view}), 200


@app.route("/music/<track_id>.mp3")
def music_track(track_id):
    """Serve a background-music MP3 built by build_music.py (Range-capable)."""
    path = _music.track_path(track_id) if _music else None
    if path is None:
        return "Not found", 404
    return send_file(path, mimetype="audio/mpeg", conditional=True, max_age=86400)


# Ambient sounds under the music (ambience.py): each layer is "auto" (the
# scene / the calendar's weather decide), "none", or a sound the DM chose.
AMBIENCE_FILE = rt("ambience.json")


def _ambience_now() -> dict:
    try:
        with open(AMBIENCE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    sounds = set(_music.SOUNDS) if _music else set()
    out = {}
    for layer in ("place", "weather"):
        v = str(data.get(layer, "auto")) if isinstance(data, dict) else "auto"
        out[layer] = v if v in ("auto", "none") or v in sounds else "auto"
    return out


@app.route("/ambience", methods=["GET"])
def ambience_get():
    if not _token_ok():
        return "Forbidden", 403
    return jsonify({"ambience": _ambience_now()}), 200


@app.route("/ambience", methods=["POST"])
def ambience_set():
    """{"place": "fire"|"auto"|"none", "weather": "rain"|"auto"|"none"} — either or both."""
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    state = _ambience_now()
    for layer in ("place", "weather"):
        if layer not in data:
            continue
        v = str(data[layer]).strip().lower()
        ok = v in ("auto", "none") or (_music and v in _music.SOUNDS and _music.SOUNDS[v]["layer"] == layer)
        if not ok:
            return jsonify({"error": f"{v!r} is not a {layer} sound"}), 400
        state[layer] = v
    try:
        with open(AMBIENCE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except OSError:
        pass
    _broadcast({"ambience": state})
    return jsonify({"ambience": state}), 200


def _in_combat() -> bool:
    """True while the initiative tracker runs (push_stats --turn-order … --turn-clear)."""
    return bool(_current_stats.get("turn_order"))


@app.route("/stats", methods=["POST"])
def stats():
    """Receive character/combat stat updates. Merges players by name, replaces turn_order.

    Pass replace_players=true to replace the entire player list (use on /dnd load to
    prevent stale characters from a previous campaign persisting in the sidebar).
    """
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    if not data:
        return "", 204

    _effect_expire_events: list[dict] = []
    fight_over = False              # --turn-clear: the battle map leaves the Map panel
    ignored_players: list[str] = []
    conc_hits: list[dict] = []      # damage taken while concentrating (see _concentration_hit)
    shape_news: list[dict] = []     # damage taken by a character in another shape (see _form_takes)
    with _stats_lock:
        if "players" in data:
            # replace_players=true wipes the list first — used on campaign load
            if data.get("replace_players"):
                _current_stats["players"] = []
            existing_players: list = _current_stats.setdefault("players", [])
            known_pcs = None   # read lazily, only when a new name shows up
            for incoming in data["players"]:
                name = incoming.get("name")
                if not name:
                    continue
                match = next((p for p in existing_players if p.get("name") == name), None)
                if not match:
                    # The player list is PCs only: an NPC there gets a card, an
                    # input tab and an autorun slot the table has to skip.
                    if known_pcs is None:
                        known_pcs = _known_pc_slugs(str(data.get("campaign") or ""))
                    if known_pcs and _pc_slug(name) not in known_pcs:
                        ignored_players.append(name)
                        print(f"[stats] '{name}' has no character sheet — not a PC, "
                              "left out of the player list", file=sys.stderr)
                        continue
                # Keys prefixed with _ are mutation ops, not stored fields
                _MUTATION_KEYS = {
                    "_inventory_add", "_inventory_remove",
                    "_conditions_add", "_conditions_remove",
                    "_slot_use", "_slot_restore",
                    "_hd_use", "_hd_restore",
                    "_effect_start", "_effect_end",
                    "_sheet_spells",
                    "_milestone_inc", "_milestone_dec",
                    "_death_save",
                }
                if match:
                    hp_before = dict(match.get("hp") or {}) if "hp" in incoming else None
                    for key, val in incoming.items():
                        if key == "_inventory_add":
                            inv = match.setdefault("sheet", {}).setdefault("inventory", [])
                            if val not in inv:
                                inv.append(val)
                        elif key == "_inventory_remove":
                            sheet = match.get("sheet", {})
                            sheet["inventory"] = [
                                i for i in sheet.get("inventory", [])
                                if i.lower() != str(val).lower()
                            ]
                        elif key == "_conditions_add":
                            # A plain name, or {name, duration_type, …} for a
                            # condition that ends by itself (see _condition_timer);
                            # a list of them when several arrive in one send.
                            for one in (val if isinstance(val, list) else [val]):
                                cond = str(one.get("name", "") if isinstance(one, dict) else one).strip()
                                if not cond:
                                    continue
                                conds = match.setdefault("conditions", [])
                                if cond.lower() not in [c.lower() for c in conds]:
                                    conds.append(cond)
                                timers = match.setdefault("condition_timers", {})
                                timer = _condition_timer(one) if isinstance(one, dict) else None
                                if timer:
                                    timers[cond.lower()] = timer
                                else:
                                    timers.pop(cond.lower(), None)   # re-applied with no end in sight
                        elif key == "_conditions_remove":
                            for one in (val if isinstance(val, list) else [val]):
                                match["conditions"] = [
                                    c for c in match.get("conditions", [])
                                    if c.lower() != str(one).lower()
                                ]
                                match.get("condition_timers", {}).pop(str(one).lower(), None)
                        elif key == "_slot_use":
                            slots = match.setdefault("spell_slots", {})
                            lvl = str(val)
                            slot = slots.setdefault(lvl, {"used": 0, "max": 0})
                            _normalize_slot(slot)
                            slot["used"] = min(slot["used"] + 1, slot.get("max", 99))
                        elif key == "_slot_restore":
                            slots = match.setdefault("spell_slots", {})
                            lvl = str(val)
                            slot = slots.setdefault(lvl, {"used": 0, "max": 0})
                            _normalize_slot(slot)
                            slot["used"] = max(slot["used"] - 1, 0)
                        elif key == "_hd_use":
                            hd = match.setdefault("hit_dice", {"remaining": 0, "max": 0})
                            hd["remaining"] = max(hd.get("remaining", 0) - 1, 0)
                        elif key == "_hd_restore":
                            hd = match.setdefault("hit_dice", {"remaining": 0, "max": 0})
                            hd["remaining"] = min(
                                hd.get("remaining", 0) + int(val),
                                hd.get("max", 99)
                            )
                        elif key == "_effect_start":
                            # val is an effect dict: {name, duration_type, ...}
                            spell_name = val.get("name", "")
                            effects = match.setdefault("effects", [])
                            # Replace any existing effect with the same name
                            match["effects"] = [
                                e for e in effects
                                if e.get("name", "").lower() != spell_name.lower()
                            ]
                            match["effects"].append(val)
                            # Sync concentration field if this is a conc effect
                            if val.get("concentration") and spell_name:
                                match["concentration"] = spell_name
                        elif key == "_effect_end":
                            # val is the spell name string
                            spell_lower = str(val).lower()
                            removed = [
                                e for e in match.get("effects", [])
                                if e.get("name", "").lower() == spell_lower
                            ]
                            match["effects"] = [
                                e for e in match.get("effects", [])
                                if e.get("name", "").lower() != spell_lower
                            ]
                            # If the ended effect was concentration, also clear it
                            if removed and any(e.get("concentration") for e in removed):
                                if (match.get("concentration") or "").lower() == spell_lower:
                                    match["concentration"] = None
                        elif key == "_death_save":
                            _apply_death_save(match, val)
                        elif key == "_sheet_spells":
                            # Patch only the spells sub-key inside sheet
                            sheet = match.setdefault("sheet", {})
                            sheet["spells"] = val
                        elif key == "inspiration" and val is False:
                            match["inspiration"] = False
                        elif key == "_milestone_inc":
                            # Stack-based reward counter — Inspiration variants,
                            # homebrew Hero Coins, Bardic Inspiration tokens, etc.
                            # The label string is the value; per-label cap optional.
                            label = str(val) or "Milestone"
                            ms = match.setdefault("milestones", {})
                            cap = match.get("milestone_caps", {}).get(label, 99)
                            ms[label] = min(ms.get(label, 0) + 1, cap)
                        elif key == "_milestone_dec":
                            label = str(val) or "Milestone"
                            ms = match.setdefault("milestones", {})
                            ms[label] = max(ms.get(label, 0) - 1, 0)
                            if ms.get(label, 0) == 0:
                                ms.pop(label, None)
                        elif isinstance(val, dict) and isinstance(match.get(key), dict):
                            match[key].update(val)
                        else:
                            match[key] = val
                    if hp_before is not None and match.get("form") and "form" not in incoming:
                        news = _form_takes(match, hp_before)
                        if news:
                            shape_news.append(news)
                            if news["conc"]:
                                conc_hits.append(news["conc"])
                            hp_before = dict(match.get("hp") or {})     # the form took it: nothing more to count below
                    _death_saves_follow_hp(match)
                    if hp_before is not None:
                        hit = _concentration_hit(match, hp_before)
                        if hit:
                            conc_hits.append(hit)
                    # A full --conditions replace drops the timers of what it dropped.
                    if match.get("condition_timers"):
                        have = {str(c).lower() for c in match.get("conditions", [])}
                        match["condition_timers"] = {
                            k: v for k, v in match["condition_timers"].items() if k in have}
                else:
                    # Strip mutation ops — they're meaningless for new players
                    existing_players.append(
                        {k: v for k, v in incoming.items() if k not in _MUTATION_KEYS}
                    )

        # turn_order: a full order replaces it, None clears it; also ticks round-based effects.
        # A partial update (--turn-current / --turn-round carries no "order") keeps
        # the rest, so a browser that loads mid-fight still gets the whole list.
        _effect_expire_events: list[dict] = []
        if "turn_order" in data:
            new_to = data["turn_order"]
            prev_to = _current_stats.get("turn_order")
            gone = new_to.pop("remove", None) if isinstance(new_to, dict) else None
            if isinstance(new_to, dict) and isinstance(prev_to, dict) and "order" not in new_to:
                new_to = {**prev_to, **new_to}
            if gone and isinstance(new_to, dict):        # --turn-remove: a creature killed
                new_to = json.loads(json.dumps(new_to))
                _turn_drop(new_to, gone if isinstance(gone, list) else [gone])
            _current_stats["turn_order"] = new_to
            fight_over = new_to is None and bool(prev_to)
            # Decrement round-based effects and conditions for the actor whose
            # turn just started. Only when the pointer really moved: a re-send
            # of the same turn (a creature joins, the list is pushed again) is
            # not another round gone.
            moved = (not isinstance(prev_to, dict) or not isinstance(new_to, dict)
                     or str(prev_to.get("current") or "").lower() != str(new_to.get("current") or "").lower()
                     or prev_to.get("round") != new_to.get("round"))
            if new_to and isinstance(new_to, dict) and new_to.get("current") and moved:
                actor = new_to["current"].lower()
                for p in _current_stats.get("players", []):
                    if p.get("name", "").lower() != actor:
                        continue
                    kept, expired = [], []
                    for eff in p.get("effects", []):
                        if eff.get("duration_type") == "rounds":
                            eff = dict(eff)  # don't mutate in-place
                            eff["duration_remaining"] = max(0, eff.get("duration_remaining", 1) - 1)
                            if eff["duration_remaining"] <= 0:
                                expired.append(eff)
                            else:
                                kept.append(eff)
                        else:
                            kept.append(eff)
                    p["effects"] = kept
                    for eff in expired:
                        was_conc = eff.get("concentration", False)
                        if was_conc and (p.get("concentration") or "").lower() == eff["name"].lower():
                            p["concentration"] = None
                        _effect_expire_events.append({
                            "owner": p["name"],
                            "name": eff["name"],
                            "was_concentration": was_conc,
                        })
                    timers = p.get("condition_timers") or {}
                    for cond_key in list(timers):
                        timer = timers[cond_key]
                        if timer.get("duration_type") != "rounds":
                            continue
                        left = max(0, int(timer.get("duration_remaining", 1)) - 1)
                        if left > 0:
                            timers[cond_key] = {**timer, "duration_remaining": left}
                            continue
                        del timers[cond_key]
                        shown = next((c for c in p.get("conditions", []) if c.lower() == cond_key), cond_key)
                        p["conditions"] = [c for c in p.get("conditions", []) if c.lower() != cond_key]
                        _effect_expire_events.append({
                            "owner": p["name"], "name": shown,
                            "was_concentration": False, "condition": True,
                        })

        # world_time replaces entirely
        if "world_time" in data:
            _current_stats["world_time"] = data["world_time"]

        # factions replaces entirely ([] clears)
        # Validate: default missing standing to "Neutral" and warn so the root
        # cause (DM omitting the field when building JSON from state.md prose)
        # is surfaced in logs without silently showing "—" in the sidebar.
        if "factions" in data:
            validated_factions = []
            for f in (data["factions"] or []):
                if not isinstance(f, dict):
                    continue
                if f.get("name") and not f.get("standing"):
                    print(
                        f"[WARN] faction '{f['name']}' missing standing field — "
                        "defaulting to Neutral. Push with standing: Allied/Friendly/"
                        "Neutral/Suspicious/Hostile to show correct colour.",
                        file=sys.stderr,
                    )
                    f = dict(f)
                    f["standing"] = "Neutral"
                validated_factions.append(f)
            _current_stats["factions"] = validated_factions

        # quests replaces entirely ([] clears)
        if "quests" in data:
            _current_stats["quests"] = data["quests"]

        _drop_dead_from_turns()

        current = dict(_current_stats)

    # autorun_waiting / autorun_cycle — display-only signals, not stored in stats
    if "autorun_waiting" in data:
        if not data["autorun_waiting"]:
            # Turn resolved — clear stored cycle so reconnecting clients don't see stale pie
            global _autorun_cycle
            with _autorun_cycle_lock:
                _autorun_cycle = None
        _broadcast({"autorun_waiting": bool(data["autorun_waiting"])})
        if not any(k in data for k in ("players", "turn_order", "world_time", "factions",
                                        "quests", "replace_players", "sheet", "autorun_cycle")):
            return "", 204

    if "autorun_cycle" in data:
        with _autorun_cycle_lock:
            _autorun_cycle = data["autorun_cycle"]
        _broadcast({"autorun_cycle": data["autorun_cycle"]})
        if not any(k in data for k in ("players", "turn_order", "world_time", "factions",
                                        "replace_players", "sheet", "autorun_threshold")):
            return "", 204

    if "autorun_threshold" in data:
        global _autorun_threshold
        val = data["autorun_threshold"]
        # 0 (push_stats.py --autorun-threshold 0) means "back to the player count", as documented
        _autorun_threshold = int(val) if val else None
        _broadcast({"autorun_threshold": _autorun_threshold})
        if not any(k in data for k in ("players", "turn_order", "world_time", "factions",
                                        "replace_players", "sheet")):
            return "", 204

    # Write active campaign name so dm_help.py always reads the current campaign.
    # Also reload the tail buffer from the new campaign's session_tail.json so
    # display replay at /dnd load shows the correct campaign's last session.
    if "campaign" in data:
        try:
            with open(CAMP_FILE, "w", encoding="utf-8") as f:
                f.write(str(data["campaign"]).strip())
            _load_tail()
            _apply_campaign_sfx_languages()
        except Exception:
            pass
        # A page that was open before the switch read the old campaign's
        # clocks, bestiary, companions and region map once, at load: send it
        # the new campaign's, or those panels stay empty until a reload.
        try:
            _broadcast({"clocks": _public_clocks(), "bestiary": _public_bestiary(), "people": _public_people(),
                        "timeline": _public_timeline(), "split": _party_split(), "split_event": "",
                        "companions": _public_companions(), "region_changed": _time.time(),
                        "supplies": _public_supplies()})
        except Exception:
            pass
        # The quests kept with the campaign come back with it, unless this same
        # call brings its own list.
        if "quests" not in data:
            try:
                if _adopt_campaign_quests() is not None:
                    with _stats_lock:
                        current = dict(_current_stats)
            except Exception:
                pass
        # The same for the factions' reputations (factions.py).
        if "factions" not in data:
            try:
                if _adopt_campaign_factions() is not None:
                    with _stats_lock:
                        current = dict(_current_stats)
            except Exception:
                pass
        # Resolve and stash the ruleset for this campaign so the sidebar badge
        # can render. Defaults to '2014' for legacy campaigns predating the
        # ruleset field. Wrapped in try/except so a missing paths import or
        # malformed state.md never breaks the stats endpoint.
        try:
            from paths import campaign_ruleset as _campaign_ruleset
            _rs = _campaign_ruleset(str(data["campaign"]).strip())
            with _stats_lock:
                _current_stats["ruleset"] = _rs
            current = dict(_current_stats)
        except Exception:
            pass

    # Explicit ruleset override (e.g. push_stats.py --ruleset 2024)
    if "ruleset" in data:
        rs_in = str(data.get("ruleset") or "").strip()
        if rs_in in ("2014", "2024"):
            with _stats_lock:
                _current_stats["ruleset"] = rs_in
            current = dict(_current_stats)

    _persist_stats()
    _broadcast({"stats": current})
    if fight_over:
        _clear_map()
    # Broadcast any round-based effect expiries after the stats update
    for evt in _effect_expire_events:
        _broadcast({"effect_expired": evt})
    # Concentration: a save on the player's screen, or the end of it at 0 HP.
    for hit in conc_hits:
        _concentration_follow(hit)
    for news in shape_news:
        if news["line"]:
            _shapes.notes_add(news["line"])
        _broadcast({"shape_event": news["event"]})

    # Update expected player count for staged-input auto-trigger
    global _expected_count
    with _stats_lock:
        players = _current_stats.get("players", [])
    _expected_count = max(1, len(players))

    if ignored_players:
        return jsonify({"ignored_players": ignored_players}), 200
    return "", 204


# ─── Death saving throws ─────────────────────────────────────────────────────
# A PC at 0 HP gets a death-save track on their card, visible to the table.
# The player rolls from their phone on their turn (the DM's screen can roll
# for them); the DM adds a failure for damage taken while down. Three
# successes: stable. Three failures: dead. A natural 20: back up with 1 HP.

_DEATH_SAVE_ACTIONS = ("start", "success", "failure", "crit", "stable", "reset")


def _apply_death_save(p: dict, action: str) -> None:
    """One step on a character's death-save track."""
    action = str(action or "").strip().lower()
    if action not in _DEATH_SAVE_ACTIONS:
        return
    if action == "reset":
        p.pop("death_saves", None)
        return
    ds = p.get("death_saves")
    if action == "start" or not isinstance(ds, dict) or ds.get("dead"):
        if action != "start" and isinstance(ds, dict) and ds.get("dead"):
            return                                       # the dead make no more saves
        ds = {"successes": 0, "failures": 0, "stable": False, "dead": False}
    if action == "success":
        ds["successes"] = min(3, int(ds.get("successes", 0)) + 1)
    elif action in ("failure", "crit"):                  # damage while down; a critical hit counts twice
        ds["failures"] = min(3, int(ds.get("failures", 0)) + (2 if action == "crit" else 1))
        ds["stable"] = False
    if ds["failures"] >= 3:
        ds.update(dead=True, stable=False)
    elif action == "stable" or ds["successes"] >= 3:
        ds.update(stable=True, successes=0, failures=0)  # the count starts over if they are hurt again
    p["death_saves"] = ds


def _turn_drop(to: dict, names: list) -> list:
    """Take these names out of a turn order (and its initiative numbers); the ones removed.

    The pointer is left alone: when the one who died was acting, the DM moves
    it on with --turn-current, which also ticks the next one's effects.
    """
    order = to.get("order") if isinstance(to, dict) else None
    if not isinstance(order, list):
        return []
    keys = {str(n).strip().lower() for n in names if str(n).strip()}
    gone = [n for n in order if str(n).lower() in keys]
    if gone:
        to["order"] = [n for n in order if str(n).lower() not in keys]
        if isinstance(to.get("init"), dict):
            to["init"] = {k: v for k, v in to["init"].items() if str(k).lower() not in keys}
    return gone


def _drop_dead_from_turns() -> list:
    """A dead character takes no more turns: out of the order (caller holds _stats_lock)."""
    dead = [p.get("name", "") for p in _current_stats.get("players", [])
            if isinstance(p.get("death_saves"), dict) and p["death_saves"].get("dead")]
    return _turn_drop(_current_stats.get("turn_order"), dead) if dead else []


def _death_saves_follow_hp(p: dict) -> None:
    """Dropping to 0 HP starts the track; any healing ends it (the dead stay dead)."""
    hp = p.get("hp") if isinstance(p.get("hp"), dict) else {}
    cur = hp.get("current")
    if not isinstance(cur, (int, float)) or isinstance(cur, bool):
        return
    ds = p.get("death_saves")
    if cur > 0:
        if isinstance(ds, dict) and not ds.get("dead"):
            p.pop("death_saves", None)
    elif not ds and (hp.get("max") or 0) > 0:
        _apply_death_save(p, "start")


def _pips(n: int, mark: str) -> str:
    n = max(0, min(3, int(n)))
    return mark * n + "○" * (3 - n)


@app.route("/death-save/roll", methods=["POST"])
def death_save_roll():
    """A dying character's death saving throw, rolled here.

    In a fight it can be rolled once, on that character's turn. A player rolls
    only for their own character; the DM's screen may roll for anyone.
    """
    data = request.get_json(silent=True) or {}
    name = str(data.get("character", "")).strip()
    if not name:
        return jsonify({"error": "no character"}), 400
    if _is_other_character(name):
        return "Forbidden", 403
    with _stats_lock:
        p = next((x for x in _current_stats.get("players", [])
                  if str(x.get("name", "")).lower() == name.lower()), None)
        ds = p.get("death_saves") if p else None
        if not isinstance(ds, dict) or ds.get("stable") or ds.get("dead"):
            return jsonify({"error": "not dying"}), 409
        turn = _current_stats.get("turn_order")
        if isinstance(turn, dict) and turn.get("current"):
            if str(turn["current"]).lower() != p["name"].lower():
                return jsonify({"error": "not your turn"}), 409
            stamp = f"{turn.get('round', 1)}:{p['name'].lower()}"
            if ds.get("rolled") == stamp:
                return jsonify({"error": "already rolled this turn"}), 409
            ds["rolled"] = stamp
        roll = secrets.randbelow(20) + 1
        if roll == 20:
            p.setdefault("hp", {})["current"] = 1
            p.pop("death_saves", None)
            outcome, tally = "up", "→ 1 HP"
        else:
            _apply_death_save(p, "crit" if roll == 1 else "success" if roll >= 10 else "failure")
            after = p["death_saves"]
            outcome = ("dead" if after["dead"] else "stable" if after["stable"]
                       else "fail2" if roll == 1 else "success" if roll >= 10 else "failure")
            # A stabilised track is wiped, so the line shows the three that got there.
            wins = 3 if outcome == "stable" else after["successes"]
            tally = f"{_pips(wins, '✓')} {_pips(0 if outcome == 'stable' else after['failures'], '✗')}"
            tally += " ☠" if outcome == "dead" else " ⚕" if outcome == "stable" else ""
        after = p.get("death_saves") or {}
        result = {"character": p["name"], "roll": roll, "outcome": outcome,
                  "successes": after.get("successes", 0), "failures": after.get("failures", 0)}
        line = f"☠ {p['name']}: d20 = {roll} · {tally}"
        _drop_dead_from_turns()
        current = dict(_current_stats)

    entry = {"text": line, "dice": True}
    try:
        camp = open(CAMP_FILE, encoding="utf-8").read().strip()
        if camp:
            entry["_camp"] = camp
    except Exception:
        pass
    with _text_log_lock:
        _text_log.append(entry)
    with _tail_lock:
        _tail_buffer.append(entry)
    _persist_log()
    _persist_tail()
    _persist_stats()
    _broadcast({"text": line, "dice": True})
    _broadcast({"stats": current})
    _broadcast({"death_save": result})
    return jsonify(result), 200


def _condition_timer(val: dict) -> Optional[dict]:
    """The duration fields of a timed condition, or None when it has no end set.

    Same shape as a timed effect: rounds count down on the owner's turns,
    minutes / hours run on the wall clock from started_at.
    """
    kind = val.get("duration_type")
    try:
        if kind == "rounds":
            left = int(val.get("duration_remaining", 0))
            return {"duration_type": "rounds", "duration_remaining": left} if left > 0 else None
        if kind in ("minutes", "hours"):
            secs = int(val.get("duration_seconds", 0))
            if secs > 0:
                return {"duration_type": kind, "duration_seconds": secs,
                        "started_at": float(val.get("started_at") or _time.time())}
    except (TypeError, ValueError):
        pass
    return None


def _timer_seconds_left(timer: dict) -> float:
    return float(timer.get("started_at", 0)) + float(timer.get("duration_seconds", 0)) - _time.time()


@app.route("/effects/expire", methods=["POST"])
def effects_expire():
    """Called by browser when a time-based effect countdown reaches zero.
    Removes the effect from stats, clears concentration if applicable,
    and broadcasts effect_expired to all connected clients.
    """
    if not _token_ok():
        return "Forbidden", 403
    data  = request.get_json(silent=True) or {}
    owner = data.get("owner", "").strip()
    name  = data.get("name", "").strip()
    if not owner or not name:
        return "", 400
    if _is_other_character(owner):
        return "Forbidden", 403   # a player's timer only expires their own effects

    expire_evt = None
    with _stats_lock:
        for p in _current_stats.get("players", []):
            if p.get("name", "").lower() != owner.lower():
                continue
            if data.get("condition"):
                # A timed condition ran out on the clock (minutes / hours).
                timer = (p.get("condition_timers") or {}).get(name.lower())
                if not timer or timer.get("duration_type") == "rounds" or _timer_seconds_left(timer) > 2:
                    return "", 204          # nothing to end, or a browser with a fast clock
                p["condition_timers"].pop(name.lower(), None)
                shown = next((c for c in p.get("conditions", []) if c.lower() == name.lower()), name)
                p["conditions"] = [c for c in p.get("conditions", []) if c.lower() != name.lower()]
                expire_evt = {"owner": p["name"], "name": shown, "was_concentration": False, "condition": True}
                break
            was_conc   = False
            new_effects = []
            for e in p.get("effects", []):
                if e.get("name", "").lower() == name.lower():
                    was_conc = e.get("concentration", False)
                    if was_conc and (p.get("concentration") or "").lower() == name.lower():
                        p["concentration"] = None
                else:
                    new_effects.append(e)
            p["effects"] = new_effects
            expire_evt = {"owner": p["name"], "name": name, "was_concentration": was_conc}
            break
        current = dict(_current_stats)

    if expire_evt:
        _broadcast({"effect_expired": expire_evt})
    _broadcast({"stats": current})
    _persist_stats()
    return "", 204


@app.route("/audio-toggle", methods=["POST"])
def audio_toggle():
    """Enable/disable ambient or SFX from the browser toggle switches.

    Body: {"ambient": true|false, "sfx": true|false}  (either or both keys)
    Response: {"ambient": bool, "sfx": bool, "available": bool}
    Broadcasts audio_state to all connected browsers so every device syncs.
    """
    data = request.get_json(silent=True) or {}
    if _audio:
        if "sfx" in data:
            _audio.set_sfx(bool(data["sfx"]))
        state = _audio.get_state()
    else:
        state = {"sfx": False, "available": False}
    return state, 200


@app.route("/narration-pref", methods=["POST"])
def narration_pref():
    """Set the narration-length target the DM aims for each turn.

    Body: {"target_words": int}.  0 clears the preference. Persisted to the
    runtime dir as a plain integer; check_input.py reads it and prepends a
    directive to queued player input so the DM honors it that turn — no
    separate file read required on the DM side.
    """
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr or "?"):
        return "Rate limited", 429
    data = request.get_json(silent=True) or {}
    try:
        n = int(data.get("target_words", 0))
    except (TypeError, ValueError):
        n = 0
    n = max(0, min(5000, n))
    pref = rt("narration_target")
    try:
        if n:
            with open(pref, "w", encoding="utf-8") as f:
                f.write(str(n))
        elif os.path.exists(pref):
            os.remove(pref)
    except OSError:
        pass
    return {"target_words": n}, 200


@app.route("/roll-pref", methods=["POST"])
def roll_pref():
    """Per-character roll preference. Body: {"character": str, "mode": "auto"|"players"}.

    Persisted to runtime roll_prefs.json; check_input.py surfaces each override as a
    [[<Char> roll mode: …]] directive so the DM honors it for that character,
    overriding the campaign-wide roll_mode in state.md.

    The character name is validated against the active party via _char_ok before
    persistence — otherwise a crafted value could smuggle prompt text into the DM
    through the [[<Char> roll mode: …]] template that check_input.py emits.
    """
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr or "?"):
        return "Rate limited", 429
    data = request.get_json(silent=True) or {}
    char = _acting_character((data.get("character") or "").strip())
    mode = (data.get("mode") or "").strip().lower()
    if not char or mode not in ("auto", "players"):
        return {"ok": False}, 400
    with _stats_lock:
        known = {p["name"] for p in _current_stats.get("players", [])}
    if not _char_ok(char, known):
        return "Forbidden", 403
    pref = rt("roll_prefs.json")
    try:
        prefs = {}
        if os.path.exists(pref):
            with open(pref, encoding="utf-8") as f:
                prefs = json.load(f)
        prefs[char] = mode
        with open(pref, "w", encoding="utf-8") as f:
            json.dump(prefs, f)
    except (OSError, ValueError):
        pass
    return {"ok": True, "character": char, "mode": mode}, 200


# ─── Narrator voice (Gemini Flash TTS) ────────────────────────────────────────
# Voice selection persists per-campaign in state.md → ## Session Flags →
# `tts_voice: <name>`. Read at /index render, written by POST /voice.

_VOICE_PAT = re.compile(r"^\s*tts_voice:\s*([A-Za-z_]+)\s*$", re.MULTILINE)


def _active_campaign_name() -> Optional[str]:
    try:
        return open(CAMP_FILE, encoding="utf-8").read().strip() or None
    except OSError:
        return None


def _read_narrator_voice() -> str:
    """Return the active campaign's tts_voice, or the module default."""
    if _tts is None:
        return ""
    name = _active_campaign_name()
    if not name:
        return _tts.default_voice()
    try:
        state = _find_campaign(name) / "state.md"
        if not state.exists():
            return _tts.default_voice()
        text = state.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return _tts.default_voice()
    m = _VOICE_PAT.search(text)
    if not m:
        return _tts.default_voice()
    v = m.group(1).strip()
    # A Gemini voice saved before switching to the local engine (or back)
    # falls back to the engine's default.
    return v if _tts.is_valid(v) else _tts.default_voice()


def _write_narrator_voice(voice: str) -> bool:
    """Persist tts_voice to the active campaign's state.md → ## Session Flags."""
    if _tts is None or not _tts.is_valid(voice):
        return False
    name = _active_campaign_name()
    if not name:
        return False
    try:
        state = _find_campaign(name) / "state.md"
        # utf8io transcodes legacy-GBK state.md losslessly (one-time migration);
        # raises ValueError on anything undecodable, so we never write U+FFFD
        # back over the file from the display UI.
        text = _read_text(state) if state.exists() else ""
    except (OSError, ValueError):
        return False

    new_line = f"tts_voice: {voice}"
    if _VOICE_PAT.search(text):
        text = _VOICE_PAT.sub(new_line, text, count=1)
    else:
        # Append under ## Session Flags. If the section is missing, append at EOF.
        if "## Session Flags" in text:
            # Insert right after the header line. Keep the existing template
            # comment if present, but place the flag immediately under it.
            text = re.sub(
                r"(## Session Flags\n(?:\*\(.*?\)\*\n)?)",
                r"\1" + new_line + "\n",
                text,
                count=1,
            )
        else:
            sep = "" if text.endswith("\n") else "\n"
            text = f"{text}{sep}\n## Session Flags\n{new_line}\n"

    try:
        state.write_text(text, encoding="utf-8")
        return True
    except OSError:
        return False


@app.route("/tts", methods=["POST"])
def tts_synthesize():
    """Synthesize a narrator/NPC block to L16 PCM.

    Body: {"text": str, "voice": str (optional)}
    Response: raw L16 PCM bytes, Content-Type: audio/L16;codec=pcm;rate=24000
    Failures: 503 (no key / module unavailable), 400 (bad input), 502 (upstream)
    """
    if _tts is None:
        return "TTS module unavailable", 503
    if not _token_ok():
        return "Forbidden", 403
    # The local engine costs nothing per call and the page asks for one
    # request per few sentences, so it skips the per-IP budget.
    if _tts.engine() != "local" and not _rate_ok(request.remote_addr or "?"):
        return "Rate limited", 429
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    voice = (data.get("voice") or _tts.default_voice()).strip()
    if not text:
        return "empty text", 400
    if len(text) > _tts.MAX_TEXT_CHARS:
        text = text[: _tts.MAX_TEXT_CHARS]
    if not _tts.is_valid(voice):
        voice = _tts.default_voice()
    if not _tts.available():
        return "TTS not configured (see docs/SKILL-tts.md)", 503
    try:
        pcm = _tts.synthesize_strict(text, voice)
    except _tts.TtsError as e:
        return f"TTS upstream: {e}", 502
    return Response(
        pcm,
        mimetype="audio/L16;codec=pcm;rate=24000",
        headers={
            "X-Audio-Chars": str(len(text)),
            "X-Audio-Voice": voice,
            # The page plays this effect when the voice reaches this text.
            "X-Audio-Sfx": (_audio.detect(text, combat=_in_combat()) if _audio else None) or "",
            "Cache-Control": "no-store",
        },
    )


@app.route("/voice", methods=["POST"])
def tts_voice():
    """Persist narrator voice selection for the active campaign.

    Body: {"voice": str}
    Response: {"voice": str, "persisted": bool}
    """
    if _tts is None:
        return jsonify({"voice": "", "persisted": False}), 503
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    voice = (data.get("voice") or "").strip()
    if not _tts.is_valid(voice):
        return jsonify({"error": "invalid voice"}), 400
    ok = _write_narrator_voice(voice)
    return jsonify({"voice": voice, "persisted": ok}), 200


@app.route("/audio/sfx/<name>")
def audio_sfx(name):
    """Serve a synthesized SFX WAV for the given effect name."""
    if not _audio:
        return "Audio not available", 503
    wav = _audio.get_sfx_wav(name)
    if wav is None:
        return "Not found", 404
    # no-cache: revalidate, so a replaced recording is heard at the next load
    return Response(wav, mimetype="audio/wav",
                    headers={"Cache-Control": "no-cache"})


@app.route("/clear", methods=["POST"])
def clear():
    """Wipe text log AND stats, broadcast clear to all connected browsers.

    Called on /dnd new (fresh campaign). Ensures sidebar shows no stale characters.
    """
    if not _token_ok():
        return "Forbidden", 403
    global _scene_buffer, _current_stats
    with _text_log_lock:
        _text_log.clear()
    with _stats_lock:
        _current_stats = {}
    _scene_buffer = []
    for path in (LOG_FILE, STATS_FILE):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    _clear_vote()                      # last session's vote does not belong to the new story
    _broadcast({"clear": True})
    return "", 204


@app.route("/help-request", methods=["POST"])
def help_request():
    """Spawn dm_help.py to generate and send an on-demand DM hint.

    Protected by an O_EXCL lock file — concurrent requests return 409
    so multiple players clicking the button never duplicates execution.
    Lock is released by dm_help.py in its finally block.
    """
    if not _token_ok():
        return "Forbidden", 403

    # Atomic lock: O_EXCL fails if file already exists — no race condition
    try:
        fd = os.open(HELP_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        return "Already running", 409

    # Read active campaign name
    try:
        campaign = open(CAMP_FILE, encoding="utf-8").read().strip()
    except FileNotFoundError:
        os.unlink(HELP_LOCK)
        return "No active campaign", 400

    if not campaign:
        os.unlink(HELP_LOCK)
        return "No active campaign", 400

    dm_help_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dm_help.py")
    subprocess.Popen(
        [sys.executable, dm_help_py, "--campaign", campaign],
        close_fds=True,
        start_new_session=True,
    )
    return "", 202


@app.route("/player-input", methods=["POST"])
def player_input():
    """Queue a player action submitted from the display companion.

    Body: {"character": "Mira", "text": "I draw my rapier", "hold": false}
    Broadcasts pending_input event to all connected browsers.
    """
    if not _token_ok():
        return "Forbidden", 403

    import time
    data = request.get_json(force=True, silent=True) or {}
    character = _acting_character(str(data.get("character", "Party"))[:50])
    text = str(data.get("text", ""))[:500]
    hold = bool(data.get("hold", False))

    # Strip shell metacharacters — input is player dialogue/action, not commands
    text = re.sub(r"[`\\$]", "", text).strip()
    if not text:
        return "empty", 400

    entry = {
        "character": character,
        "text": text,
        "hold": hold,
        "timestamp": time.time(),
    }

    with _input_lock:
        _input_queue.append(entry)
        current = list(_input_queue)

    _persist_input_queue()
    _broadcast({"pending_input": current})
    return "", 204


@app.route("/player-input/dice", methods=["POST"])
def player_dice():
    """Server-side dice roll submitted from a player's phone.

    Body: {"character": "Piper", "spec": "1d20", "modifier": 5,
           "advantage": "normal" | "advantage" | "disadvantage",
           "label": "Stealth check"  (optional)}

    Rolls server-side (secrets.randbelow → uniform, non-spoofable), broadcasts
    a dice-typed entry on the feed, and returns the result so the phone can
    finish its slot-machine animation on the real value.
    """
    if not _token_ok():
        return "Forbidden", 403

    data = request.get_json(force=True, silent=True) or {}
    character = re.sub(r"[`\\$]", "", _acting_character(str(data.get("character", "Player"))[:50])).strip() or "Player"
    spec      = str(data.get("spec", "1d20")).strip().lower()
    modifier  = int(data.get("modifier", 0) or 0)
    adv       = str(data.get("advantage", "normal")).strip().lower()
    label     = re.sub(r"[`\\$]", "", str(data.get("label", ""))[:60]).strip()
    req_id    = str(data.get("request_id", "")).strip()[:24]
    helped_by = ""
    if req_id:                     # the Help action: a teammate gave this roll advantage (or cancelled a disadvantage)
        with _dice_pending_lock:
            _e = _dice_pending.get(req_id)
            helped_by = ((_e or {}).get("meta", {}).get("help") or {}).get(character.lower(), "")
        if helped_by and spec == "1d20":
            adv = _helped_advantage(_e["meta"].get("advantage", "normal"))
            label = (label + " — " if label else "") + f"🤝 {helped_by}"

    m = re.fullmatch(r"(\d{1,2})d(\d{1,3})", spec)
    if not m:
        return jsonify({"error": "bad spec"}), 400
    n_dice, n_sides = int(m.group(1)), int(m.group(2))
    if not (1 <= n_dice <= 20 and 2 <= n_sides <= 100):
        return jsonify({"error": "out of range"}), 400
    modifier = max(-100, min(100, modifier))

    def _roll_once() -> list[int]:
        return [secrets.randbelow(n_sides) + 1 for _ in range(n_dice)]

    if adv in ("advantage", "disadvantage") and spec == "1d20":
        r1, r2 = _roll_once(), _roll_once()
        chosen = max(r1[0], r2[0]) if adv == "advantage" else min(r1[0], r2[0])
        rolls  = [chosen]
        kept   = [chosen]
        both   = [r1[0], r2[0]]
    else:
        rolls = _roll_once()
        kept  = rolls
        both  = None

    subtotal = sum(kept)
    total    = subtotal + modifier
    mod_str  = (f"+{modifier}" if modifier > 0 else (str(modifier) if modifier < 0 else ""))
    breakdown = f"[{', '.join(str(r) for r in (both or rolls))}]"
    if both is not None:
        breakdown += f" → keep {kept[0]} ({adv})"
    if modifier:
        breakdown += f" {mod_str}"
    suffix = f" — {label}" if label else ""
    text   = f"{character} rolls {spec}{mod_str}: {breakdown} = {total}{suffix}"

    payload   = {"text": text, "dice": True}
    log_entry = {"text": text, "dice": True}
    try:
        _camp_stamp = open(CAMP_FILE, encoding="utf-8").read().strip()
        if _camp_stamp:
            log_entry["_camp"] = _camp_stamp
    except Exception:
        pass

    with _text_log_lock:
        _text_log.append(log_entry)
    with _tail_lock:
        _tail_buffer.append(log_entry)
    _persist_log()
    _persist_tail()
    _broadcast(payload)

    # Correlate against any pending DM request. Case-insensitive match on the
    # character name — drop them from the request's expected-rollers set.
    pending_changed = False
    if req_id:
        with _dice_pending_lock:
            entry = _dice_pending.get(req_id)
            if entry is not None:
                ci = character.lower()
                matched = next((c for c in entry["chars"] if c.lower() == ci), None)
                if matched is not None:
                    entry["chars"].discard(matched)
                    pending_changed = True
                    if not entry["chars"]:
                        _dice_pending.pop(req_id, None)
                    got = _dice_results.get(req_id)
                    if got is not None:
                        got["results"].append({
                            "character": character, "spec": spec, "modifier": modifier, "advantage": adv,
                            "rolls": rolls, "kept": kept, "both": both, "total": total,
                            "natural": kept[0] if spec == "1d20" else None,
                            **({"helped_by": helped_by} if helped_by else {}),
                        })
    if pending_changed:
        _broadcast({"dice_pending": _dice_pending_snapshot()})
    if req_id:
        _concentration_result(req_id, character, total)
        _affliction_result(req_id, character, total)

    return jsonify({
        "character": character,
        "spec": spec,
        "modifier": modifier,
        "advantage": adv,
        "rolls": rolls,
        "kept": kept,
        "both": both,
        "subtotal": subtotal,
        "total": total,
        "text": text,
        "request_id": req_id or None,
    }), 200


@app.route("/dice-request", methods=["POST"])
def dice_request():
    """DM-initiated dice request — broadcast to player phones (no persistence).

    Body: {"character": "Piper" | "any",
           "spec": "1d20", "modifier": 5,
           "advantage": "normal" | "advantage" | "disadvantage",
           "label": "Stealth check"  (optional),
           "dc": 15  (optional, informational)}

    Phones bound to ?character=<name> match case-insensitively. "any" / ""
    targets every phone. No state stored — late-joining phones will not see
    requests issued before they connected.
    """
    if not _token_ok():
        return "Forbidden", 403

    import time
    data = request.get_json(force=True, silent=True) or {}
    raw_char  = data.get("characters") if "characters" in data else data.get("character", "any")
    if isinstance(raw_char, list):
        chars = [str(c).strip() for c in raw_char if str(c).strip()]
    else:
        chars = [c.strip() for c in re.sub(r"[`\\$]", "", str(raw_char))[:200].split(",") if c.strip()]
    if not chars:
        chars = ["any"]

    spec      = str(data.get("spec", "1d20")).strip().lower()
    modifier  = int(data.get("modifier", 0) or 0)
    adv       = str(data.get("advantage", "normal")).strip().lower()
    label     = re.sub(r"[`\\$]", "", str(data.get("label", ""))[:60]).strip()
    dc        = data.get("dc")

    if not re.fullmatch(r"\d{1,2}d\d{1,3}", spec):
        return jsonify({"error": "bad spec"}), 400
    if adv not in ("normal", "advantage", "disadvantage"):
        adv = "normal"
    modifier = max(-100, min(100, modifier))
    dc_val   = int(dc) if isinstance(dc, (int, float)) else None

    request_id, trackable = _open_dice_request(chars, spec, modifier, adv, label, dc_val)
    return jsonify({
        "request_id": request_id,
        "pending": sorted(trackable),
        "complete": not trackable,
    }), 200


def _open_dice_request(chars: list, spec: str, modifier: int, adv: str, label: str, dc_val) -> tuple:
    """Put a roll on the named players' screens; (request_id, the characters it waits for)."""
    import time
    request_id = secrets.token_hex(6)

    # Only register pending entries for explicit named targets. "any" is fire-and-forget.
    trackable = [c for c in chars if c.lower() != "any"]
    if trackable:
        with _dice_pending_lock:
            _dice_pending[request_id] = {
                "chars": set(trackable),
                "meta": {"spec": spec, "modifier": modifier, "advantage": adv, "label": label, "dc": dc_val},
                "started_at": time.time(),
            }
            _dice_results[request_id] = {"label": label, "dc": dc_val, "results": []}
            while len(_dice_results) > _DICE_RESULTS_MAX:
                _dice_results.pop(next(iter(_dice_results)))
        _broadcast({"dice_pending": _dice_pending_snapshot()})

    # Targets with no live phone bound → the main display should roll on-screen.
    onscreen_targets = [c for c in chars if c.lower() != "any" and not _phone_present(c)]
    payload = {
        "dice_request": {
            "request_id": request_id,
            "characters": chars,
            "character": chars[0] if len(chars) == 1 else "any",   # legacy single-target field
            "onscreen_targets": onscreen_targets,
            "spec": spec,
            "modifier": modifier,
            "advantage": adv,
            "label": label,
            "dc": dc_val,
        }
    }
    _broadcast(payload)
    return request_id, trackable


@app.route("/dice-request/<request_id>", methods=["GET"])
def dice_request_status(request_id):
    """Poll a dice request's completion state.

    Returns 200 with {complete, pending, label, started_at}. A request that
    never existed (or has already fully drained) reports complete=True with
    an empty pending list — send.py --wait treats both identically.
    """
    if not _token_ok():
        return "Forbidden", 403
    with _dice_pending_lock:
        entry = _dice_pending.get(request_id)
        got = _dice_results_entry(request_id) or {}
        if entry is None or not entry["chars"]:
            return jsonify({"complete": True, "pending": [], **got}), 200
        return jsonify({
            "complete": False,
            "pending": sorted(entry["chars"]),
            "label": entry["meta"].get("label", ""),
            "started_at": entry["started_at"],
            **got,
        }), 200


def _helped_advantage(adv: str) -> str:
    """Help gives advantage; with disadvantage the two cancel out."""
    return "normal" if adv == "disadvantage" else "advantage"


@app.route("/dice-request/<request_id>/help", methods=["POST"])
def dice_request_help(request_id):
    """The Help action from a player's phone: {"character": helper, "target": who rolls}.

    The target's roll in this request gets advantage. A helper who was asked
    for the same check (a group check) gives up their own roll to help."""
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr):
        return "Too Many Requests", 429
    data = request.get_json(silent=True) or {}
    helper = _acting_character(str(data.get("character", ""))[:50].strip())
    target = str(data.get("target", ""))[:50].strip()
    with _stats_lock:
        party = {str(p.get("name", "")).lower(): str(p.get("name", "")) for p in _current_stats.get("players", [])}
    if helper.lower() not in party:
        return jsonify({"error": "not at the table", "code": "helper"}), 403
    helper = party[helper.lower()]
    dropped = []
    with _dice_pending_lock:
        entry = _dice_pending.get(request_id)
        name = next((c for c in (entry or {}).get("chars", ()) if c.lower() == target.lower()), None)
        if entry is None or name is None:
            return jsonify({"error": "that roll is not waiting", "code": "gone"}), 409
        if name.lower() == helper.lower():
            return jsonify({"error": "you cannot help yourself", "code": "self"}), 409
        helps = entry["meta"].setdefault("help", {})
        if name.lower() in helps:
            return jsonify({"error": f"{helps[name.lower()]} is helping already", "code": "taken"}), 409
        helps[name.lower()] = helper
        meta = dict(entry["meta"])
        label = meta.get("label", "")
        for rid, other in list(_dice_pending.items()):       # the helper's own roll for the same check
            mine = next((c for c in other["chars"] if c.lower() == helper.lower()), None)
            if rid != request_id and mine and label and other["meta"].get("label", "") == label:
                other["chars"].discard(mine)
                got = _dice_results.get(rid)
                if got is not None:
                    got.setdefault("helping", []).append(helper)
                if not other["chars"]:
                    _dice_pending.pop(rid, None)
                dropped.append(rid)
    for rid in dropped:
        _broadcast({"dice_request_cancelled": rid})
    adv = _helped_advantage(meta.get("advantage", "normal"))
    _broadcast({"dice_request": {"request_id": request_id, "characters": [name], "character": name,
                                 "onscreen_targets": [] if _phone_present(name) else [name], "spec": meta.get("spec", "1d20"),
                                 "modifier": meta.get("modifier", 0), "advantage": adv,
                                 "label": (label + " — " if label else "") + f"🤝 {helper}", "dc": meta.get("dc")}})
    line = {"text": f"🤝 {helper} → {name}" + (f" — {label}" if label else ""), "dice": True, "help": {"helper": helper, "target": name}}
    camp = _active_campaign()
    with _text_log_lock:
        _text_log.append(dict(line, _camp=camp) if camp else dict(line))
    _persist_log()
    _broadcast(line)
    _broadcast({"dice_pending": _dice_pending_snapshot()})
    return jsonify({"target": name, "helper": helper, "advantage": adv}), 200


@app.route("/dice-request/<request_id>", methods=["DELETE"])
def dice_request_cancel(request_id):
    """Cancel a pending dice request (DM gave up waiting / moved on)."""
    if not _token_ok():
        return "Forbidden", 403
    with _dice_pending_lock:
        _dice_pending.pop(request_id, None)
    _broadcast({"dice_pending": _dice_pending_snapshot(), "dice_request_cancelled": request_id})
    return "", 204


# ─── Initiative from the players' phones ─────────────────────────────────────
# `combat.py init --phones` opens a roll here. Each PC's phone gets a button;
# the table screen can roll for a player who has no phone. Monsters are rolled
# at once and stay hidden. When every PC is in, or the time is up (whoever is
# missing is rolled for), the order is settled and shown to everyone.

_initiative: Optional[dict] = None
_initiative_lock = threading.Lock()
_INITIATIVE_SECONDS = (5, 300)


def _initiative_public(state: Optional[dict]) -> Optional[dict]:
    """What the screens may see: each PC's roll as it lands; monsters only at the end."""
    if not state:
        return None
    rows = []
    for e in state["entries"]:
        if e["type"] != "pc" and not state["done"]:
            continue
        row = {"name": e["name"], "type": e["type"], "modifier": e["modifier"], "rolled": e["total"] is not None}
        if e["total"] is not None:
            row.update(roll=e["roll"], total=e["total"], auto=bool(e.get("auto")))
        rows.append(row)
    return {"id": state["id"], "deadline": state["deadline"], "done": state["done"],
            "entries": rows, "order": list(state["order"]) if state["done"] else None}


def _initiative_roll(entry: dict, auto: bool = False) -> None:
    roll = secrets.randbelow(20) + 1
    entry.update(roll=roll, total=roll + entry["modifier"], auto=auto)


def _initiative_settle(state: dict) -> str:
    """Roll for whoever is missing, fix the order, return the line for the story.
    Call with _initiative_lock held."""
    for e in state["entries"]:
        if e["total"] is None:
            _initiative_roll(e, auto=True)
    # Highest total first; ties go to the better modifier, then to a PC, then to chance.
    ranked = sorted(state["entries"],
                    key=lambda e: (e["total"], e["modifier"], e["type"] == "pc", e["tiebreak"]), reverse=True)
    state["order"] = [e["name"] for e in ranked]
    state["done"] = True
    return "⚔ " + " · ".join(f"{e['name']} {e['total']}" for e in ranked)


def _initiative_announce(state: dict, line: str) -> None:
    """Tell every screen the result and put it in the story. Call without the lock."""
    entry = {"text": line, "dice": True}
    try:
        camp = open(CAMP_FILE, encoding="utf-8").read().strip()
        if camp:
            entry["_camp"] = camp
    except Exception:
        pass
    with _text_log_lock:
        _text_log.append(entry)
    with _tail_lock:
        _tail_buffer.append(entry)
    _persist_log()
    _persist_tail()
    _broadcast({"text": line, "dice": True})
    _broadcast({"initiative": _initiative_public(state)})


def _initiative_timeout(roll_id: str) -> None:
    with _initiative_lock:
        state = _initiative
        if not state or state["id"] != roll_id or state["done"]:
            return
        line = _initiative_settle(state)
    _initiative_announce(state, line)


@app.route("/initiative", methods=["POST"])
def initiative_open():
    """Open an initiative roll.

    Body: {"combatants": [{"name": "Aldric", "type": "pc", "dex_mod": 2}, …], "timeout": 60}
    `init_mod` overrides `dex_mod` for a character whose initiative bonus is not
    just Dexterity. Opening a new roll replaces one still open.
    """
    global _initiative
    data = request.get_json(silent=True) or {}
    entries, seen = [], set()
    for c in data.get("combatants") or []:
        if not isinstance(c, dict):
            continue
        name = re.sub(r"[`\\$<>]", "", str(c.get("name", "")))[:60].strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        try:
            mod = int(c.get("init_mod", c.get("dex_mod", 0)) or 0)
        except (TypeError, ValueError):
            mod = 0
        entries.append({"name": name, "type": "pc" if str(c.get("type", "npc")).lower() == "pc" else "npc",
                        "modifier": max(-20, min(40, mod)), "roll": None, "total": None,
                        "tiebreak": secrets.randbelow(10 ** 6)})
    if not entries:
        return jsonify({"error": "no combatants"}), 400
    try:
        seconds = int(data.get("timeout", 60))
    except (TypeError, ValueError):
        seconds = 60
    seconds = max(_INITIATIVE_SECONDS[0], min(_INITIATIVE_SECONDS[1], seconds))
    for e in entries:
        if e["type"] != "pc":
            _initiative_roll(e)
    state = {"id": secrets.token_hex(6), "deadline": _time.time() + seconds, "done": False,
             "entries": entries, "order": None}
    line = None
    with _initiative_lock:
        _initiative = state
        if all(e["total"] is not None for e in entries):       # no PC to wait for
            line = _initiative_settle(state)
    if line:
        _initiative_announce(state, line)
    else:
        _broadcast({"initiative": _initiative_public(state)})
        timer = threading.Timer(seconds, _initiative_timeout, args=[state["id"]])
        timer.daemon = True
        timer.start()
    return jsonify(_initiative_public(state)), 200


@app.route("/initiative", methods=["GET"])
def initiative_state():
    """The roll that is open, or the last one settled; {} when there is none."""
    with _initiative_lock:
        return jsonify(_initiative_public(_initiative) or {}), 200


@app.route("/initiative", methods=["DELETE"])
def initiative_cancel():
    global _initiative
    with _initiative_lock:
        _initiative = None
    _broadcast({"initiative": None})
    return "", 204


@app.route("/initiative/roll", methods=["POST"])
def initiative_roll():
    """A player rolls for their character (the DM's screen may roll for anyone)."""
    data = request.get_json(silent=True) or {}
    name = str(data.get("character", "")).strip()
    if not name:
        return jsonify({"error": "no character"}), 400
    if _is_other_character(name):
        return "Forbidden", 403
    line = None
    with _initiative_lock:
        state = _initiative
        if not state or state["done"]:
            return jsonify({"error": "no roll open"}), 409
        entry = next((e for e in state["entries"]
                      if e["type"] == "pc" and e["name"].lower() == name.lower()), None)
        if entry is None:
            return jsonify({"error": "not in this fight"}), 404
        if entry["total"] is not None:
            return jsonify({"error": "already rolled", "total": entry["total"]}), 409
        _initiative_roll(entry)
        mine = {"name": entry["name"], "roll": entry["roll"], "modifier": entry["modifier"], "total": entry["total"]}
        if all(e["total"] is not None for e in state["entries"]):
            line = _initiative_settle(state)
    if line:
        _initiative_announce(state, line)
    else:
        _broadcast({"initiative": _initiative_public(state)})
    return jsonify(mine), 200


def _sheet_key(name: str) -> str:
    """Letters and digits only, lowercased: "Vardamir Ma'feyn" == "vardamir-ma-feyn"."""
    return "".join(ch for ch in (name or "").casefold() if ch.isalnum())


@app.route("/character/<character>", methods=["GET"])
def get_character_sheet(character):
    """Return the markdown content of a PC sheet for the active campaign.

    Used by the phone's Character tab. Resolves the active campaign from
    CAMP_FILE, then reads:
        <DND_CAMPAIGN_ROOT>/campaigns/<campaign>/characters/<character>.md

    Falls back to the global roster at ~/.claude/dnd/characters/<character>.md
    if the campaign-side file is missing — useful when the character was just
    imported but not yet replicated.

    Returns text/markdown so the phone can render in JS without server-side
    dependencies (no `markdown` lib required).
    """
    if not _token_ok():
        return "Forbidden", 403

    if _is_other_character(character):
        return "Forbidden", 403   # players read only their own sheet
    path, safe, camp = _find_sheet_file(character)
    if not safe:
        return "Bad character name", 400
    if not path:
        return f"No sheet found for '{safe}' in campaign '{camp}'", 404
    try:
        with open(path, "r", encoding="utf-8") as f:
            body = f.read()
    except Exception as e:
        return f"Read error: {e}", 500
    return Response(body, mimetype="text/markdown; charset=utf-8")


def _find_sheet_file(character: str):
    """(path or None, sanitised name, campaign) of a PC's .md sheet.

    The active campaign's copy wins over the global roster.
    """
    safe = re.sub(r"[^A-Za-z0-9 _-]", "", character).strip()[:50]
    if not safe:
        return None, "", ""

    try:
        camp = open(CAMP_FILE, encoding="utf-8").read().strip()
    except Exception:
        camp = ""
    # Sanitise the campaign name with the same allowlist + length cap as the
    # character argument. CAMP_FILE is writable by anyone inside the LAN+token
    # trust boundary (via push_stats.py --set-campaign), so a malicious value
    # here could pivot to arbitrary `<name>.md` reads via os.path.join.
    camp = re.sub(r"[^A-Za-z0-9_-]", "", camp)[:50]

    root = os.environ.get("DND_CAMPAIGN_ROOT", os.path.expanduser("~/.claude/dnd"))
    folders = []
    if camp:
        folders.append(os.path.join(root, "campaigns", camp, "characters"))
    folders.append(os.path.expanduser("~/.claude/dnd/characters"))

    # Files are named by slug (vardamir-ma-feyn.md) while the display uses the
    # full name ("Vardamir Ma'feyn"), so after the exact name try every sheet
    # whose letters and digits match. The campaign copy wins over the roster.
    key = _sheet_key(character)
    candidates = []
    for folder in folders:
        candidates.append(os.path.join(folder, f"{safe}.md"))
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        candidates += [os.path.join(folder, fn) for fn in names
                       if fn.endswith(".md") and key and _sheet_key(fn[:-3]) == key]
    path = next((c for c in candidates if os.path.isfile(c)), None)
    return path, safe, camp


# ── Printable sheet / PDF download ───────────────────────────────────────────
# The same sheet as a formatted A4 page, with the live stats the DM pushed
# (current HP, slots, conditions) — and as a PDF printed by a headless Chrome
# or Edge on this machine. Without a browser the PDF link opens the page,
# which has its own Print button.
import sheet_print as _sheet_print

_pdf_cache = {}          # (path, mtime, lang, live json) → pdf bytes
_PDF_CACHE_MAX = 16


def _live_player(character: str) -> Optional[dict]:
    key = _sheet_key(character)
    with _stats_lock:
        for p in _current_stats.get("players", []):
            if _sheet_key(p.get("name", "")) == key:
                return json.loads(json.dumps(p))
    return None


def _sheet_page(character: str, for_pdf: bool):
    """(html, error response, cache key) for the printable sheet."""
    if not _token_ok():
        return None, ("Forbidden", 403), None
    if _is_other_character(character):
        return None, ("Forbidden", 403), None   # players print only their own sheet
    path, safe, camp = _find_sheet_file(character)
    if not safe:
        return None, ("Bad character name", 400), None
    if not path:
        return None, (f"No sheet found for '{safe}' in campaign '{camp}'", 404), None
    try:
        md = open(path, encoding="utf-8").read()
        mtime = os.path.getmtime(path)
    except OSError as e:
        return None, (f"Read error: {e}", 500), None
    lang = _sheet_print.pick_language(I18N_DIR, request.args.get("lang", ""),
                                      request.headers.get("Accept-Language", ""))
    tr = _sheet_print.Strings(I18N_DIR, lang)
    live = _live_player(character)
    stamp = tr.t("print.updated", "Sheet as of {date}",
                 date=_time.strftime("%d/%m/%Y %H:%M", _time.localtime()))
    pdf_href = f"/character/{quote(character)}/pdf?lang={quote(lang)}"
    page = _sheet_print.render(md, live, tr, pdf_href=pdf_href, for_pdf=for_pdf, stamp=stamp)
    key = (path, mtime, lang, json.dumps(live, sort_keys=True, default=str))
    return page, None, key


@app.route("/character/<character>/print", methods=["GET"])
def character_sheet_print(character):
    page, err, _ = _sheet_page(character, for_pdf=False)
    if err:
        return err
    return Response(page, mimetype="text/html; charset=utf-8")


@app.route("/character/<character>/pdf", methods=["GET"])
def character_sheet_pdf(character):
    page, err, key = _sheet_page(character, for_pdf=True)
    if err:
        return err
    pdf = _pdf_cache.get(key)
    if pdf is None:
        pdf = _sheet_print.to_pdf(page)
        if pdf is None:   # no headless browser here: the page prints itself
            return redirect(f"/character/{quote(character)}/print?lang={quote(request.args.get('lang', ''))}")
        if len(_pdf_cache) >= _PDF_CACHE_MAX:
            _pdf_cache.pop(next(iter(_pdf_cache)))
        _pdf_cache[key] = pdf
    lang = _sheet_print.pick_language(I18N_DIR, request.args.get("lang", ""),
                                      request.headers.get("Accept-Language", ""))
    name = (_live_player(character) or {}).get("name") or character
    fname = _sheet_print.Strings(I18N_DIR, lang).t("print.filename", "Character sheet - {name}",
                                                   name=name) + ".pdf"
    ascii_name = re.sub(r"[^A-Za-z0-9 ._-]", "", fname) or "sheet.pdf"
    return Response(pdf, mimetype="application/pdf", headers={
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; "
                               f"filename*=UTF-8''{quote(fname)}",
        "Cache-Control": "no-store",
    })


@app.route("/device/approve", methods=["POST"])
def device_approve():
    """DM approves a pending device. Body: {"id": "<device_id>"}"""
    if not _token_ok():
        return "Forbidden", 403
    device_id = str((request.get_json(force=True, silent=True) or {}).get("id", ""))
    with _devices_lock:
        _pending_devices.pop(device_id, None)
        _approved_devices.add(device_id)
    _persist_approved_devices()
    _persist_pending_devices()
    _broadcast({"device_approved": device_id})
    return "", 204


@app.route("/device/deny", methods=["POST"])
def device_deny():
    """DM denies a pending device. Body: {"id": "<device_id>"}"""
    if not _token_ok():
        return "Forbidden", 403
    device_id = str((request.get_json(force=True, silent=True) or {}).get("id", ""))
    with _devices_lock:
        _pending_devices.pop(device_id, None)
        _denied_devices.add(device_id)
    _persist_pending_devices()
    _broadcast({"device_denied": device_id})
    return "", 204


@app.route("/player-input/stage", methods=["POST"])
def stage_input():
    """Stage a player action for review. Broadcasts staged_inputs to all displays.

    Body: {"character": "Mira", "text": "draws her rapier"}
    """
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr):
        return "Too Many Requests", 429

    device_id = request.headers.get("X-DND-Device", "")
    status    = _device_ok(device_id, request.remote_addr)
    if status == "denied":
        return "Forbidden", 403
    if status == "pending":
        return jsonify({"status": "pending"}), 202

    data      = request.get_json(force=True, silent=True) or {}
    character = _acting_character(str(data.get("character", ""))[:50].strip())
    text      = _sanitize_input(str(data.get("text", "")))

    if not character or not text:
        return "Bad Request", 400

    with _stats_lock:
        known = {p["name"] for p in _current_stats.get("players", [])}
    if not _char_ok(character, known):
        return "Forbidden", 403

    # In solo mode (1 expected player), skip the manual Ready step and auto-trigger.
    solo = (_expected_count == 1)

    with _staged_lock:
        _staged[character] = {
            "text":      text,
            "ready":     solo,
            "timestamp": _time.time(),
        }
        snap = _staged_snapshot()

    _broadcast({"staged_inputs": snap})

    if solo:
        _check_auto_trigger()

    return "", 204


@app.route("/player-input/ready", methods=["POST"])
def ready_input():
    """Toggle the ready flag for a staged character.

    Body: {"character": "Mira", "ready": true}
    Triggers auto-fire when all expected players are ready.
    """
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr):
        return "Too Many Requests", 429

    device_id = request.headers.get("X-DND-Device", "")
    status    = _device_ok(device_id, request.remote_addr)
    if status == "denied":
        return "Forbidden", 403
    if status == "pending":
        return jsonify({"status": "pending"}), 202

    data      = request.get_json(force=True, silent=True) or {}
    character = _acting_character(str(data.get("character", ""))[:50].strip())
    ready     = bool(data.get("ready", True))

    with _staged_lock:
        if character not in _staged:
            return "Not Found", 404
        _staged[character]["ready"] = ready
        snap = _staged_snapshot()

    _broadcast({"staged_inputs": snap})

    if ready:
        _check_auto_trigger()

    return "", 204


@app.route("/player-input/unstage", methods=["POST"])
def unstage_input():
    """Remove a character's staged action (e.g. player wants to edit it).

    Body: {"character": "Mira"}
    """
    if not _token_ok():
        return "Forbidden", 403

    device_id = request.headers.get("X-DND-Device", "")
    if _device_ok(device_id, request.remote_addr) != "approved":
        return "Forbidden", 403

    data      = request.get_json(force=True, silent=True) or {}
    character = _acting_character(str(data.get("character", ""))[:50].strip())

    with _staged_lock:
        _staged.pop(character, None)
        snap = _staged_snapshot()

    _broadcast({"staged_inputs": snap})
    return "", 204


@app.route("/player-input/skip", methods=["POST"])
def skip_input():
    """Skip a character's turn — stages a 'skips their turn' entry marked ready.

    Counts toward the auto-trigger threshold and fires auto-trigger if threshold met.
    Body: {"character": "Mira", "lang": "it"} — the entry reads in that language
    (else the campaign's), so the queue says "salta il turno" at an Italian table.
    """
    if not _token_ok():
        return "Forbidden", 403

    device_id = request.headers.get("X-DND-Device", "")
    if _device_ok(device_id, request.remote_addr) != "approved":
        return "Forbidden", 403

    data      = request.get_json(force=True, silent=True) or {}
    character = _acting_character(str(data.get("character", ""))[:50].strip())
    if not character:
        return "Bad Request", 400

    with _stats_lock:
        known = {p["name"] for p in _current_stats.get("players", [])}
    if not _char_ok(character, known):
        return "Forbidden", 403

    lang = _spell_lang(data.get("lang", ""))           # the table's language, as the queue shows it
    text = _sheet_print.Strings(I18N_DIR, lang).t("queue.skipped", "skips their turn")
    with _staged_lock:
        _staged[character] = {
            "text":      text,
            "ready":     True,
            "timestamp": _time.time(),
        }
        snap = _staged_snapshot()

    _broadcast({"staged_inputs": snap})
    _check_auto_trigger()
    return "", 204


@app.route("/queue/consumed", methods=["POST"])
def queue_consumed():
    """Called by wrapper.py after it injects .input_queue into the PTY.

    Clears the server-side queue_status and broadcasts to all clients so
    the 'Queued — fires on DM Enter' indicator disappears on every display.
    Token required (called from localhost by the wrapper, but checked for
    consistency).
    """
    if not _token_ok():
        return "Forbidden", 403
    with _queue_status_lock:
        _queue_status.clear()
    _broadcast({"queue_status": [], "dm_processing": True})
    return "", 204


@app.route("/player-input/submit-now", methods=["POST"])
def submit_now():
    """Promote .input_queue → .input_trigger for immediate injection.

    Called by the DM or Claude when they want to process queued player actions
    right now rather than waiting for the DM's next CLI Enter press.
    Token required (DM-only action).
    """
    if not _token_ok():
        return "Forbidden", 403
    try:
        content = open(QUEUE_FILE, encoding="utf-8").read()
        os.unlink(QUEUE_FILE)
    except FileNotFoundError:
        return "No queue", 204
    except Exception:
        return "Error", 500
    try:
        with open(TRIGGER_FILE, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        return "Error", 500
    return "", 204


@app.route("/player-input/drain", methods=["POST"])
def drain_player_input():
    """Read and clear the player input queue. Called by check_input.py at turn start.

    Returns the drained entries as JSON, then broadcasts pending_input: [] to
    clear the indicator on all connected displays.
    """
    if not _token_ok():
        return "Forbidden", 403

    with _input_lock:
        drained = list(_input_queue)
        _input_queue.clear()

    _persist_input_queue()
    _broadcast({"pending_input": []})
    return jsonify(drained), 200


# ─── Secret notes (whispers) ─────────────────────────────────────────────────
# A note between the DM and ONE player. It never enters the narration log, the
# text replay or the voice: it is stored here and sent only to the browsers
# bound to that player's character (see _whisper_for, applied per SSE client).
# A note from a player is also dropped in WHISPER_INBOX_FILE, which
# whisper.py / check_input.py / autorun_wait.py hand to the DM and clear.

import whisper as _whisper

WHISPERS_FILE = rt("whispers.json")
_WHISPER_KEEP = 200            # newest notes kept on disk, all campaigns together
_WHISPER_DM_MAX = 2000         # characters in a note from the DM
_whispers: list = []
_whispers_lock = threading.Lock()


def _load_whispers() -> None:
    try:
        with open(WHISPERS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    if isinstance(data, list):
        _whispers[:] = [e for e in data if isinstance(e, dict) and e.get("text")][-_WHISPER_KEEP:]


def _persist_whispers() -> None:
    with _whispers_lock:
        data = list(_whispers)
    try:
        tmp = WHISPERS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, WHISPERS_FILE)
        os.chmod(WHISPERS_FILE, 0o600)
    except OSError:
        pass


_load_whispers()


def _whisper_owner(entry: dict) -> str:
    """The player character a note belongs to (its recipient, or its author)."""
    return str(entry.get("to") if entry.get("from") == "dm" else entry.get("from") or "")


def _addressed_elsewhere(entry, me: str) -> bool:
    only = entry.get("only") if isinstance(entry, dict) else None
    return isinstance(only, list) and bool(only) and me not in [str(n).lower() for n in only]


def _scene_for(payload: Optional[dict], viewer: Optional[str], bound: str) -> Optional[dict]:
    """A split party: keep a piece of the story for the players it was addressed to.

    `viewer` is a logged-in player's character (lowercase) or None; `bound` is
    the character a browser on this PC opened the phone view for ("" for the
    shared table screen, which shows everything). Whoever is elsewhere gets a
    bare "meanwhile, elsewhere" instead of the piece — once per run in a replay
    — and of the groups only their own.
    """
    me = viewer if viewer is not None else (bound or None)
    if not isinstance(payload, dict) or not me:
        return payload
    if _addressed_elsewhere(payload, me):
        return {"elsewhere": True}
    batch = payload.get("replay_batch")
    groups = payload.get("split")
    if not isinstance(batch, list) and not isinstance(groups, list):
        return payload
    out = dict(payload)
    if isinstance(batch, list):
        kept: list = []
        for entry in batch:
            if not _addressed_elsewhere(entry, me):
                kept.append(entry)
            elif not (kept and isinstance(kept[-1], dict) and kept[-1].get("elsewhere")):
                kept.append({"elsewhere": True})
        out["replay_batch"] = kept
    if isinstance(groups, list):
        out["split"] = _own_group(groups, me)
    return out


def _whisper_for(payload: Optional[dict], viewer: Optional[str], bound: str) -> Optional[dict]:
    """Keep a secret note for its player only.

    `viewer` is a logged-in player's character (lowercase) or None; `bound` is
    the character a browser on this PC opened the phone view for ("" for the
    shared table screen). Everyone else loses the note: they get a bare
    "the DM passed a note to X" when the DM asked for that, otherwise nothing.
    """
    if not isinstance(payload, dict) or "whisper" not in payload:
        return payload
    note = payload["whisper"]
    me = viewer if viewer is not None else (bound or None)
    if me and isinstance(note, dict) and me == _whisper_owner(note).lower():
        return payload
    out = {k: v for k, v in payload.items() if k != "whisper"}
    if isinstance(note, dict) and note.get("from") == "dm" and note.get("teaser"):
        out["whisper_teaser"] = {"to": note.get("to", "")}
    return out or None


@app.route("/whisper", methods=["POST"])
def whisper():
    """Send a secret note.

    From the DM:      {"to": "Aldric", "text": "...", "teaser": true}
    From a player:    {"text": "..."}   (a phone view on this PC adds "from")
    A logged-in player always writes to the DM as their own character.
    """
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(force=True, silent=True) or {}
    is_player = getattr(g, "role", None) == "player"
    to = str(data.get("to", "")).strip()[:50]
    with _stats_lock:
        party = [p.get("name", "") for p in _current_stats.get("players", []) if p.get("name")]

    if is_player or not to:
        if not _rate_ok(request.remote_addr):
            return "Too Many Requests", 429
        sender = _acting_character(str(data.get("from", ""))[:50].strip())
        text = _sanitize_input(str(data.get("text", "")))
        if not sender or not text:
            return "Bad Request", 400
        if not _char_ok(sender, set(party)) or sender == "Everybody":
            return "Forbidden", 403
        entry = {"from": sender, "to": "dm"}
    else:
        # The DM's own words: keep punctuation and line breaks, drop control characters.
        text = re.sub(r"[\x00-\x09\x0b-\x1f\x7f]", "", str(data.get("text", "")))[:_WHISPER_DM_MAX].strip()
        if not text:
            return "Bad Request", 400
        name = next((n for n in party if n.lower() == to.lower()), None)
        if name is None:
            return jsonify({"error": "unknown character", "party": party}), 404
        entry = {"from": "dm", "to": name, "teaser": bool(data.get("teaser", True))}

    entry.update({
        "id": secrets.token_hex(6),
        "text": text,
        "ts": _time.time(),
        "campaign": _active_campaign(),
    })
    with _whispers_lock:
        _whispers.append(entry)
        del _whispers[:-_WHISPER_KEEP]
    _persist_whispers()
    if entry["to"] == "dm":
        _whisper.inbox_add(entry)
    _broadcast({"whisper": entry})
    return jsonify(entry), 200


@app.route("/whispers")
def whispers_list():
    """The notes of one character in the active campaign, oldest first.

    A logged-in player gets their own. The DM names a character with
    ?character=, or passes ?all=1 for every thread (whisper.py --list).
    """
    if not _token_ok():
        return "Forbidden", 403
    camp = _active_campaign()
    with _whispers_lock:
        notes = [e for e in _whispers if e.get("campaign", "") == camp]
    if getattr(g, "role", None) == "player":
        who = (g.character or "").lower()
    elif request.args.get("all"):
        return jsonify(notes), 200
    else:
        who = request.args.get("character", "").strip().lower()
    return jsonify([e for e in notes if who and _whisper_owner(e).lower() == who]), 200


# Per-player fields kept for other characters when a logged-in player is
# watching: enough for the party cards (HP, AC, conditions), never the sheet.
_PARTY_CARD_KEYS = {
    "name", "race", "class", "level", "background", "hp", "ac", "speed",
    "conditions", "condition_timers", "death_saves", "concentration", "inspiration", "xp", "hit_dice",
    "spell_slots", "effects", "initiative", "player_name",
}


def _for_viewer(payload: dict, viewer: Optional[str]) -> Optional[dict]:
    """What a logged-in player's browser may see of a broadcast.

    `viewer` is the player's character (lowercase), or None for the DM, who
    gets everything. A player gets the full sheet of their own character only,
    sees only their own queued and staged actions, and never sees the DM's
    device approvals. Returns None when nothing is left to send.
    """
    if viewer is None:
        return payload
    out = dict(payload)
    for k in ("device_request", "device_approved", "device_denied"):
        out.pop(k, None)
    stats = out.get("stats")
    if isinstance(stats, dict) and isinstance(stats.get("players"), list):
        stats = dict(stats)
        stats["players"] = [
            p if (str(p.get("name", "")).lower() == viewer)
            else {k: v for k, v in p.items() if k in _PARTY_CARD_KEYS}
            for p in stats["players"] if isinstance(p, dict)
        ]
        out["stats"] = stats
    staged = out.get("staged_inputs")
    if isinstance(staged, dict):
        out["staged_inputs"] = {k: v for k, v in staged.items() if str(k).lower() == viewer}
    pending = out.get("pending_input")
    if isinstance(pending, list):
        out["pending_input"] = [e for e in pending if isinstance(e, dict)
                                and str(e.get("character", "")).lower() == viewer]
    return out or None


@app.route("/stream")
def stream():
    q: queue.Queue = queue.Queue(maxsize=256)
    _viewer = (g.character or "").lower() if g.role == "player" else None
    with _clients_lock:
        _clients.append(q)
        # Register this client's bound character (phones pass ?character=/?char=);
        # the main display passes neither. Drives dice-request phone-vs-screen routing.
        _ch = (request.args.get("character") or request.args.get("char") or "").strip().lower()[:48]
        if g.role == "player":
            _ch = (g.character or "").lower()[:48]   # a phone is bound to its login
        if _ch:
            _client_chars[q] = _ch

    # Send the current scene immediately on connect so the browser
    # starts with the right background even mid-session.
    q.put_nowait({"scene": _scene_payload(_current_scene_name)})

    # Replay recent entries so late-connecting / reconnecting browsers catch up.
    # _text_log is the durable session record (maxlen=2000); replay only the
    # last 200 chunks — the browser renders this batch on join, not the whole
    # log, so a late joiner isn't shown sessions 1..N rendered at them.
    with _text_log_lock:
        recent = list(_text_log)[-200:]
    if recent:
        q.put_nowait({"replay_batch": recent})

    # Send current stats so the sidebar is populated immediately on (re)connect.
    with _stats_lock:
        if _current_stats:
            q.put_nowait({"stats": dict(_current_stats)})

    # A shop that is open stays open for whoever connects now.
    with _shop_lock:
        if _open_shop_now():
            q.put_nowait({"shop": json.loads(json.dumps(_open_shop_now()))})

    # Send current input queue so the pending indicator is accurate on reconnect.
    with _input_lock:
        if _input_queue:
            q.put_nowait({"pending_input": list(_input_queue)})

    # Send current staged inputs so the panel reflects live state on reconnect.
    with _staged_lock:
        if _staged:
            q.put_nowait({"staged_inputs": _staged_snapshot()})

    # Send current queue status so the 'Queued' indicator survives page reload.
    with _queue_status_lock:
        if _queue_status:
            q.put_nowait({"queue_status": list(_queue_status)})

    # Send current pending dice requests so the "Waiting on…" badge survives reload.
    snap = _dice_pending_snapshot()
    if snap:
        q.put_nowait({"dice_pending": snap})

    # Replay every active dice_request so phones that connected *after* a DM
    # broadcast still pre-fill their pad and store the request_id. Without this,
    # a late-joining or reloaded phone rolls without a request_id, the roll logs
    # but the pending set never drains, and the "Waiting on…" banner gets stuck.
    with _dice_pending_lock:
        active = [(rid, dict(e["meta"]), sorted(e["chars"])) for rid, e in _dice_pending.items() if e["chars"]]
    for rid, meta, chars in active:
        q.put_nowait({"dice_request": {
            "request_id": rid,
            "characters": chars,
            "character": chars[0] if len(chars) == 1 else "any",
            "onscreen_targets": [c for c in chars if c.lower() != "any" and not _phone_present(c)],
            "spec": meta.get("spec", "1d20"),
            "modifier": meta.get("modifier", 0),
            "advantage": meta.get("advantage", "normal"),
            "label": meta.get("label", ""),
            "dc": meta.get("dc"),
        }})

    # A puzzle on the table stays there for whoever connects now.
    with _puzzle_lock:
        if _puzzle is not None:
            q.put_nowait({"puzzle": _puzzle_lib.public_view(_puzzle)})

    # An initiative roll still open: a phone that connects now can still roll.
    with _initiative_lock:
        if _initiative and not _initiative["done"]:
            q.put_nowait({"initiative": _initiative_public(_initiative)})

    # Replay autorun cycle so reconnecting clients resume the countdown from correct elapsed position.
    with _autorun_cycle_lock:
        if _autorun_cycle:
            q.put_nowait({"autorun_cycle": dict(_autorun_cycle)})

    # Replay threshold so the ready counter reflects the correct target on reconnect.
    if _autorun_threshold is not None:
        q.put_nowait({"autorun_threshold": _autorun_threshold})

    # Send any pending device approval requests so the DM sees them on reconnect.
    with _devices_lock:
        for dev in list(_pending_devices.values()):
            q.put_nowait({"device_request": {"id": dev["id"], "ip": dev["ip"]}})

    def generate():
        try:
            while True:
                try:
                    payload = _whisper_for(_for_viewer(q.get(timeout=5), _viewer), _viewer, _ch)
                    payload = _scene_for(payload, _viewer, _ch)
                    payload = _handout_for(payload, _viewer, _ch)
                    if payload:
                        yield f"data: {json.dumps(payload)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"   # prevent proxy timeout
        except GeneratorExit:
            with _clients_lock:
                try:
                    _clients.remove(q)
                except ValueError:
                    pass
                _client_chars.pop(q, None)

    resp = Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Transfer-Encoding": "chunked",
        },
    )
    # Force a single authoritative Connection header — Werkzeug otherwise
    # emits both keep-alive (ours) and close (its default), which confuses
    # transparent proxies (e.g. eero mesh routing) into buffering the stream.
    resp.headers["Connection"] = "keep-alive"
    return resp


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Wire audio SFX broadcast now that _broadcast is defined
    if _audio:
        _audio.set_broadcast(_broadcast)
    # Local narrator voice (XTTS on the GPU): start its server now so the
    # model is loaded by the time the first block is narrated.
    if _tts and _tts.engine() == "local" and _tts.ensure_local_server():
        print("Narrator voice: local XTTS server on 127.0.0.1 (log: ~/.config/claude-dnd/tts_local.log)")

    host = "0.0.0.0" if _LAN_MODE else "localhost"
    # TLS — only enabled when --tls is explicitly passed; HTTP is the default.
    # Certs are runtime state (persist across plugin updates) → rt(); .scheme is a
    # launch-time marker read by simple shell commands → stays in the code dir.
    _display_dir = os.path.dirname(os.path.abspath(__file__))
    _cert = rt("cert.pem")
    _key  = rt("key.pem")
    ssl_ctx = None
    if _TLS_MODE and os.path.exists(_cert) and os.path.exists(_key):
        import ssl

        # Werkzeug wraps the listening socket, so by default every TLS handshake
        # runs inside accept() on the main thread: one client that connects and
        # never finishes the handshake (a port scanner on the public port) stalls
        # the whole server. Defer the handshake to the request thread instead,
        # where the handler timeout below bounds it.
        class _LazyHandshakeContext(ssl.SSLContext):
            def wrap_socket(self, sock, *args, **kwargs):
                kwargs["do_handshake_on_connect"] = False
                return super().wrap_socket(sock, *args, **kwargs)

        ssl_ctx = _LazyHandshakeContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(_cert, _key)
    scheme  = "https" if ssl_ctx else "http"

    import socket as _socket
    from werkzeug.serving import WSGIRequestHandler

    _HOST_RE = re.compile(r"^[A-Za-z0-9.\-]+(:\d{1,5})?$|^\[[0-9A-Fa-f:]+\](:\d{1,5})?$")

    class _TimeoutRequestHandler(WSGIRequestHandler):
        # Drops connections that stay silent this long while we wait to read
        # (stalled handshakes, idle keep-alives). SSE streams only write, so
        # they are unaffected unless the client stops reading entirely.
        timeout = 30
        _redirected = False

        def setup(self):
            super().setup()
            if ssl_ctx is None:
                return
            # A phone that opens "host:port" without a scheme speaks plain HTTP to
            # the TLS port and just gets an error. Peek at the raw bytes (the
            # handshake hasn't run yet) and send plain HTTP clients to https://.
            raw = self.connection
            try:
                first = _socket.socket.recv(raw, 1, _socket.MSG_PEEK)
            except OSError:
                return  # timeout / reset: let the normal handler fail quietly
            if not first or first[0] == 0x16:  # 0x16 = TLS handshake record
                return
            self._redirected = True
            try:
                head = _socket.socket.recv(raw, 4096).decode("latin-1", "replace")
                lines = head.split("\r\n")
                parts = lines[0].split(" ")
                path = parts[1] if len(parts) >= 2 and parts[1].startswith("/") else "/"
                host = ""
                for line in lines[1:]:
                    if line.lower().startswith("host:"):
                        host = line[5:].strip()
                        break
                if _HOST_RE.match(host):
                    location = f"https://{host}{path}".replace("\r", "").replace("\n", "")
                    reply = ("HTTP/1.1 301 Moved Permanently\r\n"
                             f"Location: {location}\r\n"
                             "Content-Length: 0\r\nConnection: close\r\n\r\n")
                else:
                    reply = ("HTTP/1.1 400 Bad Request\r\n"
                             "Content-Length: 0\r\nConnection: close\r\n\r\n")
                _socket.socket.sendall(raw, reply.encode("latin-1"))
            except OSError:
                pass

        def handle(self):
            if not self._redirected:
                super().handle()

    # Write .scheme so push_stats.py / send.py / autorun_wait.py know which to use
    try:
        with open(os.path.join(_display_dir, ".scheme"), "w", encoding="utf-8") as _sf:
            _sf.write(scheme)
    except OSError:
        pass

    if _LAN_MODE:
        print(f"DnD DM Display — LAN mode (0.0.0.0:5001) [{scheme.upper()}]")
        print(f"  Local:  {scheme}://localhost:5001")
        print("  Token stored at:", TOKEN_FILE)
        print("  POST endpoints require X-DND-Token header (send.py/push_stats.py handle this automatically)")
        print("  Other devices must log in with a character PIN (display/accounts.py set-pin)")
        print()
    else:
        print(f"DnD DM Display — Flask server starting on {scheme}://localhost:5001")
        print(f"Open {scheme}://localhost:5001 in your browser, then Chromecast the tab.")
        print()
    # The players' door: the same app on a second port of this PC only, where
    # this PC is not the DM (see the Login section). It takes the port of the
    # players' public address when there is one, so the link the DM gives the
    # table works from the DM's own PC too.
    if _LAN_MODE:
        _door_port = 5002
        try:
            _pub = open(rt("public_host"), encoding="utf-8").read().strip()
            if ":" in _pub and int(_pub.rsplit(":", 1)[1]) != _DISPLAY_PORT:
                _door_port = int(_pub.rsplit(":", 1)[1])
        except (OSError, ValueError):
            pass
        try:
            from werkzeug.serving import make_server
            _door = make_server("127.0.0.1", _door_port, app, threaded=True,
                                request_handler=_TimeoutRequestHandler, ssl_context=ssl_ctx)
            threading.Thread(target=_door.serve_forever, daemon=True).start()
            _PLAYER_PORT = _door_port
            print(f"  This PC as a player: {scheme}://localhost:{_door_port} (PIN login; the DM's screen stays on 5001)")
        except OSError as _exc:
            print(f"  Players' door on this PC not opened (port {_door_port}): {_exc}")

    app.run(host=host, port=_DISPLAY_PORT, threaded=True, debug=False, ssl_context=ssl_ctx,
            request_handler=_TimeoutRequestHandler)
