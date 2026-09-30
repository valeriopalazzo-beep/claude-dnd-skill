#!/usr/bin/env python3
"""image_gen.py — generate portraits, monsters, scenes and items for the display.

Three backends, chosen in ~/.config/claude-dnd/images.json (or --backend):

  pollinations  gen.pollinations.ai. With a key (POLLINATIONS_KEY env var or
                ~/.config/claude-dnd/pollinations.key) it uses `flux` and
                spends the account's pollen. Without a key it falls back to the
                anonymous legacy endpoint: free, lower quality, sometimes busy.
  local         An AUTOMATIC1111 / Forge / SD.Next server started with --api
                (default http://127.0.0.1:7860). Free, needs a GPU.
  gemini        Gemini image model with a Google AI Studio key (same key
                resolution as tts.py). Paid per image.
  off           Never generate; the script exits 0 and says so.

Every image is cached per (kind, subject) in the campaign's media folder, so a
recurring NPC keeps the SAME face: asking again for an existing subject just
re-shows the file. --regenerate makes a new version. `action` images are the
exception: each one is a single moment, so they are always generated anew
(as the next --vN of their subject).

Usage:
    python3 image_gen.py --kind portrait --subject "Vesna" \\
        --prompt "half-elf innkeeper, 50s, grey braid, burn scar on left hand, wary eyes"
    python3 image_gen.py --kind monster --subject "Ghoul" --prompt "..." --caption "Dal buio, un ghoul"
    python3 image_gen.py --list                 # what exists for this campaign
    python3 image_gen.py --status               # effective config (keys masked)
    python3 image_gen.py --test                 # one small image, not shown

stdlib only.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as _dt
import json
import os
import random
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

for _stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

CONFIG_DIR = Path.home() / ".config" / "claude-dnd"
CONFIG_FILE = CONFIG_DIR / "images.json"
POLLINATIONS_KEY_FILE = CONFIG_DIR / "pollinations.key"

DEFAULTS = {
    "backend": "pollinations",
    # Appended to every prompt so a campaign's images share one look.
    "style": "detailed painterly fantasy illustration, dramatic lighting",
    "pollinations_model": "flux",
    "local_url": "http://127.0.0.1:7860",
    "local_steps": 25,
    "local_max_side": 768,     # SD 1.5 on a 6 GB card; raise for SDXL
    # Sampler settings for the local server. Empty = the server's current
    # choice. Distilled models need their own: SDXL Lightning wants ~6 steps,
    # CFG 2, "DPM++ SDE" + "Karras".
    "local_cfg": 7.0,
    "local_sampler": "",
    "local_scheduler": "",
    "local_model": "",         # checkpoint to use (Forge title or file name); empty = loaded one
    # --compose: depth ControlNet that pins where each subject goes (compose.py).
    # Name as Forge lists it (GET /controlnet/model_list); empty = --compose is ignored.
    "local_controlnet_depth": "",
    # Tested with SDXL Lightning (6 steps) + control-lora-depth-rank128: at
    # 0.8 / 0.6 the silhouettes were ignored and the creature still vanished;
    # full guidance keeps every subject where the sketch puts it.
    "compose_weight": 1.0,     # how strictly the silhouettes are followed
    "compose_end": 1.0,        # share of the steps that are guided
    "gemini_model": "gemini-2.5-flash-image",
    "timeout": 180,
    # Battle-map art (map_render.py → map_art.py): auto | paint | tiles | off
    "map_art": "auto",
    "map_denoise": 0.6,        # paint: how far the backend may stray from the layout
}

# kind → (prompt template, width, height, gemini aspect ratio)
KIND_PRESETS = {
    "portrait": ("fantasy character portrait, head and shoulders, {p}", 768, 1024, "3:4"),
    "monster":  ("fantasy creature, full body, menacing, {p}", 1024, 1024, "1:1"),
    "scene":    ("fantasy environment, wide establishing shot, {p}", 1344, 768, "16:9"),
    # A moment of the fight or a dramatic event (a blow, an explosion, a portal
    # opening). Close framing keeps SD 1.5 on the action instead of drifting
    # into a landscape the way "establishing shot" does.
    "action":   ("dynamic fantasy action illustration, medium shot, motion, {p}", 1216, 832, "3:2"),
    "item":     ("fantasy item, single object centred on a plain background, {p}", 768, 768, "1:1"),
    "texture":  ("{p}", 512, 512, "1:1"),     # battle-map terrain, used by map_art.py
}
CLI_KINDS = ("action", "item", "monster", "portrait", "scene")
NEGATIVE = "text, letters, watermark, signature, logo, frame, blurry, lowres, deformed, extra limbs"


class ImageError(Exception):
    pass


class ConnectionDropped(ImageError):
    """The server closed the socket mid-request (Forge does this while it swaps checkpoints)."""


# ── Config ───────────────────────────────────────────────────────────────────

def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        user = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(user, dict):
            cfg.update({k: v for k, v in user.items() if v is not None})
    except FileNotFoundError:
        pass
    except (OSError, json.JSONDecodeError) as e:
        print(f"image_gen: ignoring unreadable {CONFIG_FILE}: {e}", file=sys.stderr)
    if os.environ.get("DND_IMAGE_BACKEND", "").strip():
        cfg["backend"] = os.environ["DND_IMAGE_BACKEND"].strip()
    if os.environ.get("DND_IMAGE_LOCAL_URL", "").strip():
        cfg["local_url"] = os.environ["DND_IMAGE_LOCAL_URL"].strip()
    return cfg


def _pollinations_key() -> Optional[str]:
    for env in ("POLLINATIONS_KEY", "DND_POLLINATIONS_KEY"):
        v = os.environ.get(env, "").strip()
        if v:
            return v
    try:
        return POLLINATIONS_KEY_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _gemini_key() -> Optional[str]:
    v = os.environ.get("DND_IMAGE_KEY", "").strip()
    if v:
        return v
    import tts  # same env vars + key file as narration
    return tts._get_api_key()


def build_prompt(kind: str, prompt: str, style: str) -> str:
    tmpl = KIND_PRESETS[kind][0]
    full = tmpl.format(p=prompt.strip().rstrip("."))
    return f"{full}, {style}" if style else full


def _size_for(kind: str, max_side: Optional[int] = None) -> "tuple[int, int]":
    _, w, h, _ = KIND_PRESETS[kind]
    if max_side and max(w, h) > max_side:
        f = max_side / max(w, h)
        w, h = int(w * f) // 64 * 64, int(h * f) // 64 * 64
    return w, h


def _ext_for(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    raise ImageError(f"backend returned something that is not an image ({data[:60]!r})")


def _http(req: urllib.request.Request, timeout: float) -> bytes:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read(300).decode("utf-8", errors="replace")
        except Exception:
            pass
        raise ImageError(f"http {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise ImageError(f"network: {e.reason}") from e
    except TimeoutError as e:
        raise ImageError("timed out") from e
    except ConnectionError as e:
        raise ConnectionDropped(f"connection dropped: {e}") from e


# ── Backends — each returns raw image bytes ─────────────────────────────────

def gen_pollinations(prompt: str, kind: str, seed: int, cfg: dict) -> bytes:
    w, h = _size_for(kind)
    key = _pollinations_key()
    q = {"width": w, "height": h, "seed": seed, "nologo": "true"}
    enc = urllib.parse.quote(prompt, safe="")
    if key:
        q["model"] = cfg["pollinations_model"]
        url = f"https://gen.pollinations.ai/image/{enc}?{urllib.parse.urlencode(q)}"
        headers = {"Authorization": f"Bearer {key}"}
    else:
        # Anonymous legacy endpoint — only its default model is free.
        url = f"https://image.pollinations.ai/prompt/{enc}?{urllib.parse.urlencode(q)}"
        headers = {}
    headers["User-Agent"] = "claude-dnd-skill/image_gen"
    last: Optional[ImageError] = None
    for attempt in range(3):
        try:
            return _http(urllib.request.Request(url, headers=headers), cfg["timeout"])
        except ImageError as e:
            last = e
            busy = "503" in str(e) or "Queue full" in str(e) or "429" in str(e)
            if not busy or attempt == 2:
                break
            time.sleep(6 * (attempt + 1))
    if key:
        hint = ""
    elif "402" in str(last):
        hint = (" (the anonymous endpoint now asks for payment — set a key from "
                "enter.pollinations.ai, or use the local backend)")
    else:
        hint = " (anonymous endpoint — a free key from enter.pollinations.ai is more reliable)"
    raise ImageError(f"pollinations: {last}{hint}")


LOCAL_LOCK = Path(tempfile.gettempdir()) / "claude-dnd-local-image.lock"


@contextlib.contextmanager
def local_gpu_lock(timeout: float = 900, path: Path = LOCAL_LOCK):
    """One request at a time to the local server, across processes.

    The DM starts images and map art as separate background calls. Queued in
    Forge they mostly wait their turn, but a txt2img that arrived while an
    SDXL img2img was running had its connection dropped twice (GTX 1660,
    2026-09-30). Waiting here costs nothing: the GPU does one image at a time anyway.
    """
    path.touch(exist_ok=True)
    f = open(path, "rb+")
    locked = False
    try:
        deadline = time.time() + timeout
        while not locked:
            try:
                if os.name == "nt":
                    import msvcrt
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError:
                if time.time() > deadline:
                    raise ImageError(f"local: the GPU has been busy for over {int(timeout)} s")
                time.sleep(0.5)
        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f, fcntl.LOCK_UN)
            except OSError:
                pass
        f.close()


def a1111(endpoint: str, body: dict, cfg: dict) -> bytes:
    """POST to a Forge / A1111 /sdapi/v1/<endpoint> (txt2img, img2img); first image."""
    with local_gpu_lock():
        return _a1111(endpoint, body, cfg)


def _a1111(endpoint: str, body: dict, cfg: dict) -> bytes:
    url = cfg["local_url"].rstrip("/") + "/sdapi/v1/" + endpoint
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    for attempt in (1, 2):
        try:
            raw = _http(req, max(cfg["timeout"], 300))
            break
        except ConnectionDropped:
            if attempt == 2:
                raise ImageError(f"local ({cfg['local_url']}): connection dropped twice") from None
            time.sleep(3)   # first call after a checkpoint change: retry once
        except ImageError as e:
            raise ImageError(f"local ({cfg['local_url']}): {e} — is Forge/A1111 running with --api?") from e
    try:
        return base64.b64decode(json.loads(raw)["images"][0].split(",", 1)[-1])
    except (KeyError, IndexError, ValueError) as e:
        raise ImageError(f"local: unexpected response shape: {e}") from e


def local_params(cfg: dict) -> dict:
    """Sampler/model fields shared by txt2img and img2img on the local server."""
    body = {"steps": int(cfg["local_steps"]), "cfg_scale": float(cfg["local_cfg"])}
    if cfg.get("local_sampler"):
        body["sampler_name"] = cfg["local_sampler"]
    if cfg.get("local_scheduler"):
        body["scheduler"] = cfg["local_scheduler"]
    if cfg.get("local_model"):
        # Keep it loaded afterwards: swapping an SDXL checkpoint back and forth
        # costs far more than the image itself on a small GPU.
        body["override_settings"] = {"sd_model_checkpoint": cfg["local_model"]}
        body["override_settings_restore_afterwards"] = False
    return body


def compose_unit(spec: str, w: int, h: int, cfg: dict) -> dict:
    """ControlNet unit (Forge / sd-webui-controlnet API) for a compose.py depth sketch."""
    import compose
    return {"enabled": True, "module": "None", "model": cfg["local_controlnet_depth"],
            "image": base64.b64encode(compose.depth_png(spec, w, h)).decode("ascii"),
            "weight": float(cfg["compose_weight"]), "guidance_start": 0.0,
            "guidance_end": float(cfg["compose_end"]), "resize_mode": "Crop and Resize",
            "control_mode": "Balanced", "pixel_perfect": False}


def gen_local(prompt: str, kind: str, seed: int, cfg: dict, compose_spec: str = "") -> bytes:
    w, h = _size_for(kind, int(cfg["local_max_side"]))
    body = {"prompt": prompt, "negative_prompt": NEGATIVE, "width": w,
            "height": h, "seed": seed, **local_params(cfg)}
    if compose_spec:
        body["alwayson_scripts"] = {"ControlNet": {"args": [compose_unit(compose_spec, w, h, cfg)]}}
    return a1111("txt2img", body, cfg)


def gemini(parts: list, aspect: str, cfg: dict) -> bytes:
    """One Gemini image from `parts` (text, and optionally an inline image)."""
    key = _gemini_key()
    if not key:
        raise ImageError("gemini: no API key (DND_IMAGE_KEY / GEMINI_API_KEY / tts.key)")
    model = cfg["gemini_model"]
    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {"responseModalities": ["IMAGE"],
                             "imageConfig": {"aspectRatio": aspect}},
    }
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": key})
    raw = _http(req, cfg["timeout"])
    try:
        for part in json.loads(raw)["candidates"][0]["content"]["parts"]:
            if "inlineData" in part:
                return base64.b64decode(part["inlineData"]["data"])
    except (KeyError, IndexError, ValueError) as e:
        raise ImageError(f"gemini: unexpected response shape: {e}") from e
    raise ImageError("gemini: no image in the response (prompt refused?)")


def gen_gemini(prompt: str, kind: str, seed: int, cfg: dict) -> bytes:
    return gemini([{"text": prompt}], KIND_PRESETS[kind][3], cfg)


BACKENDS = {"pollinations": gen_pollinations, "local": gen_local, "gemini": gen_gemini}


def compose_usable(cfg: dict) -> bool:
    return cfg["backend"] == "local" and bool(cfg.get("local_controlnet_depth"))


def generate(prompt: str, kind: str, seed: int, cfg: dict, compose_spec: str = "") -> bytes:
    backend = cfg["backend"]
    if backend not in BACKENDS:
        raise ImageError(f"unknown backend {backend!r} (pollinations | local | gemini | off)")
    if compose_spec:
        if compose_usable(cfg):
            return gen_local(prompt, kind, seed, cfg, compose_spec)
        print("image_gen: --compose needs the local backend and local_controlnet_depth — ignored",
              file=sys.stderr)
    return BACKENDS[backend](prompt, kind, seed, cfg)


# ── Cache: one file per (kind, subject), --vN for regenerations ─────────────

def existing_versions(d: Path, kind: str, subj: str) -> list:
    """Paths for kind-subj.* and kind-subj--vN.*, oldest first."""
    found = []
    for p in d.glob(f"{kind}-{subj}*"):
        stem = p.stem
        if stem == f"{kind}-{subj}":
            found.append((1, p))
        elif stem.startswith(f"{kind}-{subj}--v") and stem.rsplit("--v", 1)[1].isdigit():
            found.append((int(stem.rsplit("--v", 1)[1]), p))
    return [p for _, p in sorted(found)]


def _mask(v: Optional[str]) -> str:
    return f"set (…{v[-4:]})" if v else "not set"


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a fantasy image and show it on the DnD display.")
    ap.add_argument("--kind", choices=CLI_KINDS, default="portrait")
    ap.add_argument("--subject", help="who/what this is — the cache key (e.g. the NPC's name)")
    ap.add_argument("--prompt", help="visual description, in English for best results")
    ap.add_argument("--caption", help="caption on the display (default: the subject)")
    ap.add_argument("--compose", metavar="SPEC",
                    help="where each subject goes, e.g. 'humanoid-short:left, quadruped:right' "
                         "(compose.py; local backend + depth ControlNet)")
    ap.add_argument("--campaign", help="campaign name (default: the display's active campaign)")
    ap.add_argument("--backend", choices=[*BACKENDS, "off"], help="override the configured backend")
    ap.add_argument("--seed", type=int, help="fixed seed (default: random, stored in media/index.json)")
    ap.add_argument("--regenerate", action="store_true", help="make a new version even if one exists")
    ap.add_argument("--no-send", action="store_true", help="save only; do not show on the display")
    ap.add_argument("--list", action="store_true", help="list this campaign's images and exit")
    ap.add_argument("--status", action="store_true", help="print the effective configuration and exit")
    ap.add_argument("--test", action="store_true", help="generate one image to a temp file, show nothing")
    args = ap.parse_args()

    cfg = load_config()
    if args.backend:
        cfg["backend"] = args.backend

    if args.status:
        print(f"config file:   {CONFIG_FILE} ({'found' if CONFIG_FILE.exists() else 'not found — defaults'})")
        print(f"backend:       {cfg['backend']}")
        print(f"style:         {cfg['style']}")
        print(f"pollinations:  key {_mask(_pollinations_key())}, model {cfg['pollinations_model']}")
        print(f"local:         {cfg['local_url']} (model {cfg['local_model'] or 'as loaded'}, steps {cfg['local_steps']}, "
              f"cfg {cfg['local_cfg']}, sampler {cfg['local_sampler'] or 'default'} {cfg['local_scheduler']}, "
              f"max side {cfg['local_max_side']})")
        try:
            gk = _gemini_key()
        except Exception:
            gk = None
        print(f"gemini:        key {_mask(gk)}, model {cfg['gemini_model']}")
        import map_art
        print(f"compose:       {cfg['local_controlnet_depth'] or 'off (set local_controlnet_depth)'}"
              f" (weight {cfg['compose_weight']}, until {cfg['compose_end']})")
        print(f"map art:       {cfg['map_art']} → {map_art.resolve_mode(cfg['map_art'], cfg['backend'])}"
              f" (paint denoise {cfg['map_denoise']})")
        return 0

    import media

    if args.list:
        try:
            d = media.media_dir(args.campaign, create=False)
        except RuntimeError as e:
            print(f"image_gen: {e}", file=sys.stderr)
            return 2
        for e in media.load_index(d):
            print(f"{e.get('kind', '?'):8} {e.get('subject', ''):24} {e.get('file', '')}  — {e.get('prompt', '')}")
        return 0

    if cfg["backend"] == "off" and not args.test:
        print("image_gen: images are off (backend \"off\") — nothing generated")
        return 0

    if args.test:
        seed = args.seed if args.seed is not None else random.randint(1, 2**31 - 1)
        full = build_prompt("item", args.prompt or "an ornate brass lantern", cfg["style"])
        t0 = time.time()
        try:
            data = generate(full, "item", seed, cfg)
            ext = _ext_for(data)
        except ImageError as e:
            print(f"FAIL [{cfg['backend']}]: {e}")
            return 1
        out = Path(os.environ.get("TEMP") or "/tmp") / f"dnd-image-test.{ext}"
        out.write_bytes(data)
        print(f"OK [{cfg['backend']}] {len(data)} bytes in {time.time() - t0:.1f}s → {out}")
        return 0

    if not args.subject:
        ap.error("--subject is required")
    try:
        d = media.media_dir(args.campaign)
    except RuntimeError as e:
        print(f"image_gen: {e}", file=sys.stderr)
        return 2

    subj = media.slug(args.subject)
    caption = args.caption if args.caption is not None else args.subject
    versions = existing_versions(d, args.kind, subj)

    if versions and not args.regenerate and args.kind != "action":
        fname = versions[-1].name
        print(f"image_gen: reusing {fname} (use --regenerate for a new one)", file=sys.stderr)
    else:
        if not args.prompt:
            ap.error("--prompt is required to generate a new image")
        seed = args.seed if args.seed is not None else random.randint(1, 2**31 - 1)
        full = build_prompt(args.kind, args.prompt, cfg["style"])
        if args.compose:
            import compose
            try:
                compose.parse(args.compose)
            except compose.ComposeError as e:
                ap.error(f"--compose: {e}")
        try:
            data = generate(full, args.kind, seed, cfg, args.compose or "")
            ext = _ext_for(data)
        except ImageError as e:
            print(f"image_gen: {e}", file=sys.stderr)
            return 1
        suffix = f"--v{len(versions) + 1}" if versions else ""
        fname = f"{args.kind}-{subj}{suffix}.{ext}"
        (d / fname).write_bytes(data)
        media.add_to_index(d, {
            "file": fname, "kind": args.kind, "subject": args.subject,
            "prompt": args.prompt, "full_prompt": full, "seed": seed,
            **({"compose": args.compose} if args.compose and compose_usable(cfg) else {}),
            "backend": cfg["backend"],
            "created": _dt.datetime.now().isoformat(timespec="seconds"),
        })

    print(str(d / fname))
    if not args.no_send:
        if not media.post_image(fname, caption=caption, kind=args.kind, subject=args.subject):
            print("image_gen: display offline — image saved but not shown", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
