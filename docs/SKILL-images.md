# Images & battle maps (optional)

The display companion can show pictures in the feed: **battle maps** the DM draws, and generated **portraits, monsters, scenes and items**. Everything here is optional — without it the skill works exactly as before.

## Battle maps

The DM writes the layout as a text grid; `display/map_render.py` places grid, coordinate ruler and tokens in the exact squares, and the **image backend below paints the terrain**. Re-sending the same map with moved tokens shrinks the previous copy in the feed.

| `map_art` | Needs | What the backend makes |
|---|---|---|
| `paint` | `local` or `gemini` | repaints the whole layout (img2img), so walls, water and doors land where the grid says — best result |
| `tiles` | any backend | one texture per terrain type, tiled square by square — works with Pollinations |
| `auto` (default) | — | `paint` on local/gemini, `tiles` otherwise |
| `off` | — | plain vector map, no network |

```json
{ "backend": "local", "map_art": "auto", "map_denoise": 0.6 }
```

`map_denoise` (paint only): lower keeps the layout more faithfully, higher gives the model more freedom. Art is cached per terrain in `<campaign>/media/` (`mapart-*`, `maptex-*`), so moving tokens never regenerates it. The first time a place is shown, the plain map appears at once and the painted one replaces it. If the backend fails, the plain map stays.

## Generated pictures — pick a backend

`display/image_gen.py` generates images and caches them per subject in `<campaign>/media/`, so a recurring NPC keeps the same face. Choose a backend in `~/.config/claude-dnd/images.json`:

```json
{ "backend": "pollinations" }
```

| Backend | Cost | Quality | Setup |
|---|---|---|---|
| `pollinations` without a key | free, but since Sept 2026 often refused with HTTP 402 (payment required) | fair; small "pollinations.ai" logo | none — this is the default |
| `pollinations` with a key | pollen credits (Flux schnell ≈ 0.002 pollen/image) | good, no logo | create a key at https://enter.pollinations.ai |
| `local` | free | depends on your model | Forge / AUTOMATIC1111 with `--api` and a GPU |
| `gemini` | paid per image (billing must be enabled) | very good | Google AI Studio key |
| `off` | — | — | disables generation |

### Pollinations key (optional)

```bash
mkdir -p ~/.config/claude-dnd
cat > ~/.config/claude-dnd/pollinations.key     # paste the key, Return, Ctrl-D
```

Or set `POLLINATIONS_KEY`. Model: `"pollinations_model": "flux"` (default) or any image model from `https://gen.pollinations.ai/image/models`.

### Local (Stable Diffusion WebUI Forge / AUTOMATIC1111 / SD.Next)

1. Install [Forge](https://github.com/lllyasviel/stable-diffusion-webui-forge) and a fantasy-friendly checkpoint (SD 1.5 fits a 6 GB card; SDXL wants 8 GB+ or `--medvram`).
2. Start it with the API on: add `--api` to `COMMANDLINE_ARGS` in `webui-user.bat`.
3. Configure:

```json
{ "backend": "local", "local_url": "http://127.0.0.1:7860", "local_steps": 25, "local_max_side": 768 }
```

Raise `local_max_side` to 1024 for SDXL models.

### Gemini

Uses the same key as narration TTS (`DND_IMAGE_KEY`, else `DND_TTS_KEY` / `GEMINI_API_KEY` / `~/.config/claude-dnd/tts.key`). Image models are **not** on the Gemini free tier — enable billing first.

```json
{ "backend": "gemini", "gemini_model": "gemini-2.5-flash-image" }
```

### One look per campaign

`"style"` is appended to every prompt (default: `detailed painterly fantasy illustration, dramatic lighting`). Set it once — e.g. `"ink and watercolour, muted palette"` — so all images match.

## Check it works

```bash
python3 <skill>/display/image_gen.py --status   # effective config, keys masked
python3 <skill>/display/image_gen.py --test     # one small image to a temp file
```
