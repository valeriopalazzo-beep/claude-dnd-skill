"""Background music for the display — one mood per scene, a rousing one in combat.

The tracks are free-license recordings listed in TRACKS (CC0, CC-BY 4.0, and
one Mixkit-licensed crowd ambience). They are NOT shipped with the plugin:
build_music.py downloads them, converts them to small MP3s and writes them to
<data-root>/music/ (default ~/.claude/dnd/music), which survives plugin updates.
The display serves them at /music/<id>.mp3; the page crossfades between them.

stdlib only.
"""

from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from paths import _root  # noqa: E402

# id → where it comes from. gain is applied on top of loudness normalisation
# (ambiences sit lower than melodic tracks).
TRACKS: dict[str, dict] = {
    "old_tower_inn": {
        "title": "Medieval: The Old Tower Inn", "author": "RandomMind", "license": "CC0",
        "url": "https://opengameart.org/sites/default/files/Loop_The_Old_Tower_Inn.wav",
        "page": "https://opengameart.org/content/medieval-the-old-tower-inn",
    },
    "crowded_pub": {
        "title": "Crowded Pub", "author": "Bobjt", "license": "CC0",
        "url": "https://opengameart.org/sites/default/files/crowded_pub_2025_2.mp3",
        "page": "https://opengameart.org/content/crowded-pub",
    },
    "town_theme": {
        "title": "Town Theme RPG", "author": "cynicmusic", "license": "CC0",
        "url": "https://opengameart.org/sites/default/files/TownTheme.mp3",
        "page": "https://opengameart.org/content/town-theme-rpg",
    },
    "kings_feast": {
        "title": "Medieval: King's Feast", "author": "RandomMind", "license": "CC0",
        "url": "https://opengameart.org/sites/default/files/Loop_Kings_Feast_0.wav",
        "page": "https://opengameart.org/content/medieval-kings-feast",
    },
    "forest_ambience": {
        "title": "Forest Ambience", "author": "TinyWorlds", "license": "CC0",
        "url": "https://opengameart.org/sites/default/files/Forest_Ambience.mp3",
        "page": "https://opengameart.org/content/forest-ambience",
    },
    "field_of_dreams": {
        "title": "The Field Of Dreams", "author": "pauliuw", "license": "CC0",
        "url": "https://opengameart.org/sites/default/files/the_field_of_dreams.mp3",
        "page": "https://opengameart.org/content/the-field-of-dreams",
    },
    "legend_will_rise": {
        "title": "A Legend Will Rise", "author": "CodeManu", "license": "CC0",
        "url": "https://opengameart.org/sites/default/files/A%20Legend%20Will%20Rise.mp3",
        "page": "https://opengameart.org/content/a-legend-will-rise-orchestral",
    },
    "unexplored": {
        "title": "Unexplored (expanded)", "author": "TAD, Bo Jingles", "license": "CC0",
        "url": "https://opengameart.org/sites/default/files/tad_-_unexplored_-_expanded_1_by_bo_jingles_0.mp3",
        "page": "https://opengameart.org/content/unexplored-expansion",
    },
    "seaside_village": {
        "title": "Seaside Village", "author": "Leonardo Paz", "license": "CC-BY 4.0",
        "url": "https://opengameart.org/sites/default/files/02_seaside_village.wav",
        "page": "https://opengameart.org/content/ocean-music-pack",
    },
    "cave_theme": {
        "title": "Cave Theme", "author": "Brandon75689", "license": "CC0",
        "url": "https://opengameart.org/sites/default/files/cave%20themeb4.ogg",
        "page": "https://opengameart.org/content/cave-theme",
    },
    "dungeon_ambience": {
        "title": "Dungeon Ambience", "author": "yd", "license": "CC0",
        "url": "https://opengameart.org/sites/default/files/dungeon002_0.ogg",
        "page": "https://opengameart.org/content/dungeon-ambience",
    },
    "forgotten_tomb": {
        "title": "Forgoten Tomb Ambience", "author": "kindland", "license": "CC0", "gain": 0.7,
        "url": "https://opengameart.org/sites/default/files/Forgoten_tombs_1.mp3",
        "page": "https://opengameart.org/content/forgoten-tomb-ambience",
    },
    "battle_theme_a": {
        "title": "Battle Theme A", "author": "cynicmusic", "license": "CC0",
        "url": "https://opengameart.org/sites/default/files/battleThemeA.mp3",
        "page": "https://opengameart.org/content/battle-theme-a",
    },
    "determined_pursuit": {
        "title": "Determined Pursuit (epic orchestra loop)", "author": "Emma_MA", "license": "CC0",
        "url": "https://opengameart.org/sites/default/files/determined_pursuit_loop.wav",
        "page": "https://opengameart.org/content/determined-pursuit-epic-orchestra-loop",
    },
    "tavern_chatter": {
        "title": "Restaurant crowd talking ambience", "author": "Mixkit", "license": "Mixkit Sound Effects Free License",
        "url": "https://assets.mixkit.co/active_storage/sfx/444/444-preview.mp3",
        "page": "https://mixkit.co/free-sound-effects/restaurant/",
    },
    # Ambient sounds under the music: the weather and the place. "loop" makes
    # build_music.py stitch the end into the start (no fades) and lengthen a
    # short loop, so the browser's looping never pulses or clicks.
    "amb_rain": {
        "title": "Rain (loopable) — 1", "author": "Ylmir", "license": "CC0", "loop": True, "gain": 0.7,
        "url": "https://opengameart.org/sites/default/files/Rain%20OGG.zip", "member": "1.ogg",
        "page": "https://opengameart.org/content/rain-loopable",
    },
    "amb_storm": {
        "title": "Rain and Thunders (Dark Rainy Night)", "author": "kindland", "license": "CC0", "loop": True, "gain": 0.7,
        "url": "https://opengameart.org/sites/default/files/Dark_Rainy_Night%28ambience%29.ogg",
        "page": "https://opengameart.org/content/rain-and-thunders",
    },
    "amb_wind": {
        "title": "Wind Whoosh Loop", "author": "SketchMan3", "license": "CC0", "loop": True, "gain": 0.6,
        "url": "https://opengameart.org/sites/default/files/wind%20woosh%20loop.ogg",
        "page": "https://opengameart.org/content/wind-whoosh-loop",
    },
    "amb_fire": {
        "title": "Fireplace Sound loop", "author": "PagDev", "license": "CC0", "loop": True, "gain": 0.7,
        "url": "https://opengameart.org/sites/default/files/fire.wav",
        "page": "https://opengameart.org/content/fireplace-sound-loop",
    },
    "amb_river": {
        "title": "Sea and river wave sounds (short)", "author": "RandomMind", "license": "CC0", "loop": True, "gain": 0.6,
        "url": "https://opengameart.org/sites/default/files/VistulaShort_0.mp3",
        "page": "https://opengameart.org/content/sea-and-river-wave-sounds",
    },
    "amb_night": {
        "title": "Crickets Ambient Noise (loopable)", "author": "Wolfgang_", "license": "CC0", "loop": True, "gain": 0.5,
        "url": "https://opengameart.org/sites/default/files/crickets_1.mp3",
        "page": "https://opengameart.org/content/crickets-ambient-noise-loopable",
    },
    "amb_cave": {
        "title": "Loopable Dungeon Ambience", "author": "JaggedStone", "license": "CC0", "loop": True, "gain": 0.7,
        "url": "https://opengameart.org/sites/default/files/dungeon_ambient_1_0.ogg",
        "page": "https://opengameart.org/content/loopable-dungeon-ambience",
    },
}

# Display scene (SCENES in dnd-display-app.py) → tracks to rotate through.
SCENE_MUSIC: dict[str, list[str]] = {
    "tavern": ["old_tower_inn", "crowded_pub"],
    "city": ["town_theme"],
    "castle": ["kings_feast"],
    "temple": ["field_of_dreams"],
    "arcane": ["field_of_dreams"],
    "night": ["forest_ambience"],
    "forest": ["forest_ambience"],
    "mountain": ["legend_will_rise"],
    "desert": ["unexplored"],
    "swamp": ["unexplored"],
    "ocean": ["seaside_village"],
    "mine": ["cave_theme"],
    "cave": ["cave_theme"],
    "dungeon": ["dungeon_ambience"],
    "ruins": ["dungeon_ambience"],
    "fire": ["dungeon_ambience"],
    "crypt": ["forgotten_tomb"],
    "combat": ["battle_theme_a", "determined_pursuit"],
}

# A second, quieter layer under the music: the sound of the place.
SCENE_AMBIENCE: dict[str, str] = {
    "tavern": "tavern_chatter",
    "city": "tavern_chatter",
    "night": "amb_night",
    "swamp": "amb_night",
    "ocean": "amb_river",
    "mine": "amb_cave",
    "cave": "amb_cave",
    "dungeon": "amb_cave",
    "crypt": "amb_cave",
    "fire": "amb_fire",
}

# A third layer: the weather of the campaign calendar (calendar.py weather keys).
WEATHER_AMBIENCE: dict[str, str] = {
    "rain": "amb_rain",
    "storm": "amb_storm",
    "windy": "amb_wind",
    "snow": "amb_wind",
}

# Under ground the weather is not heard.
SHELTERED: tuple = ("mine", "cave", "dungeon", "crypt")

# The names the DM uses with ambience.py, and the layer each one goes to.
SOUNDS: dict[str, dict] = {
    "rain": {"id": "amb_rain", "layer": "weather"},
    "storm": {"id": "amb_storm", "layer": "weather"},
    "wind": {"id": "amb_wind", "layer": "weather"},
    "fire": {"id": "amb_fire", "layer": "place"},
    "river": {"id": "amb_river", "layer": "place"},
    "night": {"id": "amb_night", "layer": "place"},
    "cave": {"id": "amb_cave", "layer": "place"},
    "crowd": {"id": "tavern_chatter", "layer": "place"},
}


def music_dir() -> pathlib.Path:
    """<data-root>/music — outside the plugin, so updates keep the files."""
    raw = os.environ.get("DND_MUSIC_DIR", "").strip()
    return pathlib.Path(raw).expanduser() if raw else _root() / "music"


def track_path(track_id: str) -> pathlib.Path | None:
    """The built MP3 for a known track id, or None (unknown id or not built)."""
    if track_id not in TRACKS:
        return None
    p = music_dir() / f"{track_id}.mp3"
    return p if p.is_file() else None


def catalog() -> dict:
    """What the page needs: scene → playable track ids, plus ambience layers.

    Tracks not built yet are left out, so a scene with none stays silent.
    """
    built = {t for t in TRACKS if track_path(t)}
    return {
        "scenes": {s: [t for t in ids if t in built] for s, ids in SCENE_MUSIC.items()},
        "ambience": {s: t for s, t in SCENE_AMBIENCE.items() if t in built},
        "weather": {w: t for w, t in WEATHER_AMBIENCE.items() if t in built},
        "sounds": {n: s["id"] for n, s in SOUNDS.items() if s["id"] in built},
        "sheltered": list(SHELTERED),
    }
