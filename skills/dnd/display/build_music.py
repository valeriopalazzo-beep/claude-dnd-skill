"""Download and prepare the display's background music (see music.py).

Each track in music.TRACKS is downloaded once, mixed to 44.1 kHz, levelled to
the same loudness (times its `gain`), faded in/out at the ends so restarts
are soft, and written as <music_dir>/<id>.mp3. A CREDITS.md with authors and
licenses is written next to them.

Needs numpy, scipy and soundfile (libsndfile >= 1.1 for MP3), which the
display itself does not: run it with any Python that has them, e.g. the
local-voice venv.

    python build_music.py            # build what is missing
    python build_music.py --force    # rebuild everything
"""

from __future__ import annotations

import argparse
import os
import ssl
import sys
import urllib.request
from math import gcd

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import music  # noqa: E402

SR = 44100
TARGET_RMS_DB = -20.0
PEAK_DB = -1.0
FADE_S = 1.5


def _ssl_context():
    try:  # Windows certificate store — certifi alone fails behind some antivirus
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


def download(url: str, dest: str) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (dnd-display music)"})
    with urllib.request.urlopen(req, context=_ssl_context(), timeout=120) as r, \
            open(dest + ".part", "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    os.replace(dest + ".part", dest)


def prepare(src: str, dest: str, gain: float) -> float:
    x, sr = sf.read(src, always_2d=True, dtype="float64")
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    x = x[:, :2]
    if sr != SR:
        g = gcd(SR, sr)
        x = resample_poly(x, SR // g, sr // g, axis=0)
    # Level by the loud parts (ignore silence), then keep the peak under -1 dB.
    blk = SR // 2
    rms = [np.sqrt(np.mean(x[i:i + blk] ** 2)) for i in range(0, len(x) - blk, blk)]
    loud = np.percentile(rms, 75) if rms else 1.0
    x *= (10 ** (TARGET_RMS_DB / 20) / max(loud, 1e-6)) * gain
    peak = np.abs(x).max()
    if peak > 10 ** (PEAK_DB / 20):
        x *= 10 ** (PEAK_DB / 20) / peak
    n = min(int(SR * FADE_S), len(x) // 4)
    ramp = np.linspace(0, 1, n)[:, None]
    x[:n] *= ramp
    x[-n:] *= ramp[::-1]
    sf.write(dest, x.astype(np.float32), SR, format="MP3", subtype="MPEG_LAYER_III")
    return len(x) / SR


def credits(out_dir) -> None:
    lines = ["# Background music — credits", "",
             "Downloaded and converted by build_music.py. Not redistributed with the plugin.", "",
             "| Track | Author | License | Source |", "|---|---|---|---|"]
    for tid, t in music.TRACKS.items():
        lines.append(f"| {t['title']} (`{tid}`) | {t['author']} | {t['license']} | {t['page']} |")
    (out_dir / "CREDITS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description="Download and prepare the display's background music.")
    p.add_argument("--force", action="store_true", help="rebuild tracks that already exist")
    args = p.parse_args()
    out = music.music_dir()
    src_dir = out / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    failed = 0
    for tid, t in music.TRACKS.items():
        dest = out / f"{tid}.mp3"
        if dest.exists() and not args.force:
            print(f"{tid:20s} already built")
            continue
        ext = os.path.splitext(t["url"].split("?")[0])[1] or ".bin"
        src = str(src_dir / f"{tid}{ext}")
        try:
            if not os.path.exists(src):
                download(t["url"], src)
            secs = prepare(src, str(dest), float(t.get("gain", 1.0)))
            print(f"{tid:20s} {secs / 60:4.1f} min  {dest.stat().st_size / 1e6:4.1f} MB")
        except Exception as e:  # keep going: one broken link must not stop the rest
            failed += 1
            print(f"{tid:20s} FAILED: {e}")
    credits(out)
    print(f"\n{out}  ({failed} failed)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
