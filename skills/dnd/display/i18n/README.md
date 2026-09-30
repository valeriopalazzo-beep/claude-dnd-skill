# Display translations

One JSON file per language, named with its two-letter code (`en.json`, `it.json`, …).
The display server reads every file here on each page load and embeds them in the
page. Each browser picks its own language: the one chosen in **Settings → Language**
(stored per device), otherwise the device language, otherwise English.

## Adding a language

1. Copy `en.json` to `<code>.json` (for example `fr.json`).
2. Set `"_name"` to the language's own name (`"Français"`) — that is what the picker shows.
3. Translate the values. Keep the keys and any `{placeholder}` exactly as they are.
4. Reload the display. No restart needed.

Anything missing from a language falls back to English, so a partial file is fine.

## Keys that only non-English files need

These translate words that arrive from the game data (conditions, quest status,
faction standing). English shows the original word, so `en.json` does not list them:

- `cond.<name>` — conditions, e.g. `"cond.poisoned": "Avvelenato"`
- `quest.status.<status>` — `active`, `threat`, `resolved`, `failed`
- `faction.<standing>` — `allied`, `friendly`, `neutral`, `unfriendly`, `suspicious`, `hostile`

`it.json` has the full set to copy from.
