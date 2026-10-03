"""Narrator TTS: local XTTS-v2 on this PC's GPU, or Gemini Flash TTS.

Server-side wrapper called by /tts in dnd-display-app.py.

Local engine (free, nothing leaves the PC): enabled by
~/.config/claude-dnd/tts_local.json, e.g.
    {"python": "C:/.../venv/Scripts/python.exe", "voice": "Damien_Black", "language": "it"}
`python` is a venv with torch + coqui-tts; the display starts tts_local.py
with it (ensure_local_server) and talks to it on 127.0.0.1.

Gemini engine: reads the API key from DND_TTS_KEY env var → GEMINI_API_KEY
env var → ~/.config/claude-dnd/tts.key (in that order).

Returns None on any failure so the caller can silently degrade to text-only
narration. stdlib only — no requests, no google-cloud-sdk, no dependencies.
Setup walkthrough: docs/SKILL-tts.md.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

# ── Configuration ───────────────────────────────────────────────────────────

GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"
GEMINI_TTS_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_TTS_MODEL}:generateContent"
)

KEY_FILE = Path.home() / ".config" / "claude-dnd" / "tts.key"

# Curated 9-voice catalog. The full Gemini API exposes 30 voices; this
# subset keeps the per-block dropdown scannable. Extend VALID_VOICES and
# the matching arrays in display/templates/index.html if you want the
# full catalog.
VALID_VOICES = frozenset({
    # Male
    "Charon", "Enceladus", "Fenrir", "Umbriel",
    # Female
    "Aoede", "Gacrux", "Kore", "Vindemiatrix", "Zephyr",
})
DEFAULT_VOICE = "Enceladus"

# Gemini Flash TTS responses degrade in latency past this point and may
# truncate. Bigger blocks should be chunked by the caller; the narration
# path naturally chunks at block flush.
MAX_TEXT_CHARS = 2000

# Request timeout — Gemini Flash TTS typical latency is 1-3s; we give it
# substantial headroom for cold-start or transient slowness.
DEFAULT_TIMEOUT = 30.0


# ── Local engine (XTTS-v2 via tts_local.py) ─────────────────────────────────

LOCAL_CONFIG = Path.home() / ".config" / "claude-dnd" / "tts_local.json"
LOCAL_LOG = LOCAL_CONFIG.with_name("tts_local.log")
LOCAL_PORT = 5056
# XTTS has 58 studio voices; these read Italian well and split clearly by
# pitch. Ids use "_" for the space in the XTTS speaker name.
LOCAL_VOICES_MALE = ["Damien_Black", "Viktor_Menelaos", "Dionisio_Schuyler",
                     "Baldur_Sanjin", "Kumar_Dahl", "Torcull_Diarmuid"]
LOCAL_VOICES_FEMALE = ["Ana_Florence", "Claribel_Dervla", "Daisy_Studious",
                       "Sofia_Hellen", "Gracie_Wise", "Tammie_Ema"]
LOCAL_DEFAULT_VOICE = "Damien_Black"
# One block of narration is ~2x faster than real time on a GTX 1660.
LOCAL_TIMEOUT = 120.0

_local_lock = threading.Lock()
_local_started = 0.0


def _local_config() -> Optional[dict]:
    try:
        cfg = json.loads(LOCAL_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return cfg if isinstance(cfg, dict) and cfg.get("python") else None


def engine() -> str:
    """'local' when tts_local.json is set up, else 'gemini'."""
    return "local" if _local_config() else "gemini"


def voices() -> dict:
    """Voice menu for the active engine: {"male": [...], "female": [...]}."""
    if engine() == "local":
        return {"male": LOCAL_VOICES_MALE, "female": LOCAL_VOICES_FEMALE}
    return {"male": ["Charon", "Enceladus", "Fenrir", "Umbriel"],
            "female": ["Aoede", "Gacrux", "Kore", "Vindemiatrix", "Zephyr"]}


def default_voice() -> str:
    cfg = _local_config()
    if cfg:
        v = cfg.get("voice")
        return v if v in LOCAL_VOICES_MALE + LOCAL_VOICES_FEMALE else LOCAL_DEFAULT_VOICE
    return DEFAULT_VOICE


def is_valid(voice: str) -> bool:
    menu = voices()
    return voice in menu["male"] or voice in menu["female"]


def available() -> bool:
    """True when /tts can be expected to work (engine configured)."""
    return engine() == "local" or key_source() != "unset"


def _local_url(path: str) -> str:
    cfg = _local_config() or {}
    return f"http://127.0.0.1:{int(cfg.get('port') or LOCAL_PORT)}{path}"


def local_health() -> Optional[dict]:
    try:
        with urllib.request.urlopen(_local_url("/health"), timeout=2) as r:
            return json.loads(r.read())
    except Exception:
        return None


def ensure_local_server() -> bool:
    """Start tts_local.py with the configured venv unless it already answers.

    Returns True if the server is up or was just started. The model takes
    ~20 s to load; /synth answers 503 "loading" until then.
    """
    global _local_started
    cfg = _local_config()
    if not cfg:
        return False
    if local_health() is not None:
        return True
    with _local_lock:
        # Don't respawn while a previous start is still loading the model.
        if time.time() - _local_started < 90:
            return True
        here = Path(__file__).resolve().parent
        # The server outlives the display, so keep its cwd and open log out
        # of the plugin folder: on Windows they would lock it and block
        # `claude plugin install`.
        log = open(LOCAL_LOG, "wb")  # fresh log per start
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            subprocess.Popen(
                [cfg["python"], str(here / "tts_local.py"),
                 "--port", str(int(cfg.get("port") or LOCAL_PORT))],
                stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                cwd=str(Path.home()), creationflags=flags,
                env={**os.environ, "PYTHONUTF8": "1", "COQUI_TOS_AGREED": "1"},
            )
        except OSError:
            return False
        finally:
            log.close()
        _local_started = time.time()
    return True


def _synthesize_local(text: str, voice: str) -> bytes:
    cfg = _local_config() or {}
    body = json.dumps({"text": text, "voice": voice.replace("_", " "),
                       "language": cfg.get("language") or "it"}).encode("utf-8")
    req = urllib.request.Request(_local_url("/synth"), data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        try:
            with urllib.request.urlopen(req, timeout=LOCAL_TIMEOUT) as resp:
                pcm = resp.read()
        except ConnectionResetError:
            # Windows sometimes resets the socket while the audio is on its
            # way back; the server cached it, so asking again is instant.
            with urllib.request.urlopen(req, timeout=LOCAL_TIMEOUT) as resp:
                pcm = resp.read()
    except urllib.error.HTTPError as e:
        raise TtsError(f"local http {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        ensure_local_server()   # crashed or never started: bring it back
        raise TtsError(f"local server down: {e}") from e
    if not pcm:
        raise TtsError("empty pcm payload")
    return pcm


# ── Key resolution ──────────────────────────────────────────────────────────

def _get_api_key() -> Optional[str]:
    """Resolve the Gemini API key. Env vars take precedence over the key file."""
    for env in ("DND_TTS_KEY", "GEMINI_API_KEY"):
        v = os.environ.get(env)
        if v and v.strip():
            return v.strip()
    if KEY_FILE.exists():
        try:
            return KEY_FILE.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
    return None


def key_source() -> str:
    """Return a human description of where the key came from, for diagnostics.

    Used by the verify path and the /dnd load surface — does not return the key.
    """
    for env in ("DND_TTS_KEY", "GEMINI_API_KEY"):
        v = os.environ.get(env)
        if v and v.strip():
            return f"env:{env}"
    if KEY_FILE.exists() and KEY_FILE.read_text(encoding="utf-8").strip():
        return f"file:{KEY_FILE}"
    return "unset"


# ── Synthesis ───────────────────────────────────────────────────────────────

class TtsError(Exception):
    """Raised by synthesize_strict; caught by synthesize to fail silently."""


def synthesize_strict(
    text: str,
    voice: str = DEFAULT_VOICE,
    timeout: float = DEFAULT_TIMEOUT,
) -> bytes:
    """Call Gemini Flash TTS and return raw L16 PCM bytes (24 kHz mono).

    Raises TtsError for any failure. Use synthesize() for the silent-fail path.
    """
    if engine() == "local":
        text = (text or "").strip()[:MAX_TEXT_CHARS]
        if not text:
            raise TtsError("empty text")
        return _synthesize_local(text, voice if is_valid(voice) else default_voice())

    key = _get_api_key()
    if not key:
        raise TtsError("no api key configured")

    text = (text or "").strip()
    if not text:
        raise TtsError("empty text")
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]

    if voice not in VALID_VOICES:
        voice = DEFAULT_VOICE

    body = {
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": voice}
                }
            },
        },
    }
    body_bytes = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(
        f"{GEMINI_TTS_URL}?key={key}",
        data=body_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                raise TtsError(f"http {resp.status}")
            raw = resp.read()
    except urllib.error.HTTPError as e:
        snippet = ""
        try:
            snippet = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        raise TtsError(f"http {e.code}: {snippet}") from e
    except urllib.error.URLError as e:
        raise TtsError(f"network: {e.reason}") from e
    except Exception as e:
        raise TtsError(f"unexpected: {e}") from e

    try:
        data = json.loads(raw)
        b64 = data["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
        raise TtsError(f"bad response shape: {e}") from e

    try:
        pcm = base64.b64decode(b64)
    except Exception as e:
        raise TtsError(f"bad base64: {e}") from e

    if not pcm:
        raise TtsError("empty pcm payload")
    return pcm


def synthesize(text: str, voice: str = DEFAULT_VOICE) -> Optional[bytes]:
    """Silent-fail wrapper. Returns L16 PCM bytes or None on any failure."""
    try:
        return synthesize_strict(text, voice)
    except TtsError:
        return None


# ── CLI for verification ────────────────────────────────────────────────────

def _cli() -> int:
    import argparse
    import sys

    p = argparse.ArgumentParser(
        description="Verify Gemini Flash TTS setup for the DnD display companion.",
    )
    p.add_argument(
        "--test", action="store_true",
        help="Check key source + run a synthesis call. Writes nothing.",
    )
    p.add_argument(
        "--speak", action="store_true",
        help="Also play the synthesized audio via afplay (macOS).",
    )
    p.add_argument(
        "--text", default="Hello, narrator voice test. The torchlit hall awaits.",
        help="Override the default test phrase.",
    )
    p.add_argument(
        "--voice", default=DEFAULT_VOICE,
        help=f"Voice name (default: {DEFAULT_VOICE}). Valid: {sorted(VALID_VOICES)}",
    )
    args = p.parse_args()

    if not args.test:
        p.print_help()
        return 0

    src = key_source()
    print(f"API key source: {src}")
    if src == "unset":
        print("  → no key found. See docs/SKILL-tts.md to configure one.")
        return 2

    print(f"Model: {GEMINI_TTS_MODEL}")
    print(f"Voice: {args.voice}")
    print(f"Text:  {args.text!r}")
    print("Calling Gemini Flash TTS…")
    try:
        pcm = synthesize_strict(args.text, args.voice)
    except TtsError as e:
        print(f"  FAIL: {e}")
        return 1

    print(f"  OK — received {len(pcm)} bytes of L16 PCM (24 kHz mono).")

    if args.speak:
        import struct
        import subprocess
        import tempfile

        sample_rate = 24000
        n_samples = len(pcm) // 2
        byte_rate = sample_rate * 2
        wav_header = (
            b"RIFF"
            + struct.pack("<I", 36 + len(pcm))
            + b"WAVE"
            + b"fmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, byte_rate, 2, 16)
            + b"data"
            + struct.pack("<I", len(pcm))
        )
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_header + pcm)
            wav_path = f.name
        print(f"  Playing via afplay: {wav_path}")
        try:
            subprocess.run(["afplay", wav_path], check=False)
        except FileNotFoundError:
            print("  (afplay not found — non-macOS? PCM file kept at the path above.)")
            return 0
        os.unlink(wav_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
