"""Local narrator TTS server — Coqui XTTS-v2 on this PC's GPU.

Runs in its OWN Python (a venv with torch + coqui-tts), not the display's:
the display only talks to it over HTTP on 127.0.0.1, through tts.py.

    <venv>/python tts_local.py [--port 5056]

GET  /health  → {"ready": bool, "device": str}
POST /synth   {"text": str, "voice": str, "language": str} → L16 PCM, 24 kHz mono

Italian XTTS reads a trailing period as "punto", drops words on long input
and places its own pauses, so the text is split into sentences here, each
sentence loses its final period, the sampler runs cold, and the pauses
between sentences are fixed silences.

Normally started by the display (see tts.py, ~/.config/claude-dnd/tts_local.json).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SAMPLE_RATE = 24000
PAUSE_S = 0.35
CACHE_ITEMS = 64
MAX_TEXT_CHARS = 2000

_model = None
_device = "?"
_gpu_lock = threading.Lock()
_cache: "OrderedDict[tuple, bytes]" = OrderedDict()
_cache_lock = threading.Lock()


def sentences(text: str) -> list:
    """Split narration into sentences without the trailing period."""
    text = re.sub(r"[*_#`~]+", "", text)
    # «Finalmente» sussurra → Finalmente, sussurra: the closing quote is a pause
    text = re.sub(r"(?<![.!?…])[»”\"]\s+(?=\w)", ", ", text)
    text = re.sub(r"[«»“”\"]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    out = []
    for s in re.split(r"(?<=[.!?…])\s+", text):
        s = s.strip().rstrip(".").strip()
        if re.search(r"\w", s):
            out.append(s)
    return out


def _load():
    global _model, _device
    os.environ.setdefault("COQUI_TOS_AGREED", "1")
    try:
        import truststore  # Windows certificate store; certifi alone fails on some PCs
        truststore.inject_into_ssl()
    except ImportError:
        pass
    import torch
    from TTS.api import TTS
    _device = "cuda" if torch.cuda.is_available() else "cpu"
    _model = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(_device)
    print(f"[tts_local] XTTS-v2 ready on {_device}", flush=True)


def synth(text: str, voice: str, language: str) -> bytes:
    import numpy as np
    key = (voice, language, text)

    def cached():
        with _cache_lock:
            if key in _cache:
                _cache.move_to_end(key)
                return _cache[key]
        return None

    pcm = cached()
    if pcm is not None:
        return pcm
    pause = np.zeros(int(SAMPLE_RATE * PAUSE_S), dtype=np.float32)
    parts = []
    with _gpu_lock:
        # Every phone with auto-narrate asks for the same block at once: the
        # ones that waited for the GPU get the first one's audio.
        pcm = cached()
        if pcm is not None:
            return pcm
        for s in sentences(text):
            wav = _model.tts(text=s, speaker=voice, language=language,
                             split_sentences=False, temperature=0.3,
                             repetition_penalty=5.0, top_p=0.8, top_k=30)
            parts += [np.asarray(wav, dtype=np.float32), pause]
    if not parts:
        raise ValueError("nothing to say")
    audio = np.clip(np.concatenate(parts), -1.0, 1.0)
    pcm = (audio * 32767).astype("<i2").tobytes()
    with _cache_lock:
        _cache[key] = pcm
        while len(_cache) > CACHE_ITEMS:
            _cache.popitem(last=False)
    return pcm


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/health":
            return self._send(404, b"{}")
        body = {"ready": _model is not None, "device": _device}
        self._send(200, json.dumps(body).encode())

    def do_POST(self):
        if self.path != "/synth":
            return self._send(404, b"{}")
        if _model is None:
            return self._send(503, b'{"error": "loading"}')
        try:
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n) or b"{}")
            text = str(data.get("text") or "")[:MAX_TEXT_CHARS]
            voice = str(data.get("voice") or "")
            language = str(data.get("language") or "it")
            if voice not in _model.synthesizer.tts_model.speaker_manager.speakers:
                return self._send(400, b'{"error": "unknown voice"}')
            pcm = synth(text, voice, language)
        except ValueError as e:
            return self._send(400, json.dumps({"error": str(e)}).encode())
        except Exception as e:  # keep serving after a bad sentence
            print(f"[tts_local] synth failed: {e}", flush=True)
            return self._send(500, json.dumps({"error": str(e)[:200]}).encode())
        self._send(200, pcm, "audio/L16;codec=pcm;rate=24000")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--port", type=int, default=5056)
    args = p.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    # Answer /health ("loading") while the model loads (~20 s on first start).
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[tts_local] listening on 127.0.0.1:{args.port}", flush=True)
    _load()
    threading.Event().wait()


if __name__ == "__main__":
    main()
