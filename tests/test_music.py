"""Background music and scenes (display/music.py + dnd-display-app.py).

The DM names the scene with push_stats.py --scene; keyword guessing is the
fallback and matches whole words only, in English and Italian. Music files
live outside the plugin (DND_MUSIC_DIR here) and only built tracks are
offered to the page.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"


def _load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class MusicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.music_dir = tempfile.mkdtemp(prefix="dnd-test-music-")
        cls._saved_env = os.environ.get("DND_MUSIC_DIR")
        os.environ["DND_MUSIC_DIR"] = cls.music_dir
        for tid in ("old_tower_inn", "battle_theme_a", "tavern_chatter"):
            pathlib.Path(cls.music_dir, f"{tid}.mp3").write_bytes(b"ID3fake-mp3")
        cls.app = _load(DISPLAY / "dnd-display-app.py", "music_display_app_under_test")
        cls.client = cls.app.app.test_client()

    @classmethod
    def tearDownClass(cls):
        if cls._saved_env is None:
            os.environ.pop("DND_MUSIC_DIR", None)
        else:
            os.environ["DND_MUSIC_DIR"] = cls._saved_env

    def setUp(self):
        self.app._scene_pinned = False
        self.app._current_scene_name = "tavern"
        self.app._scene_buffer.clear()

    def test_catalog_lists_only_built_tracks(self):
        cat = self.app._music.catalog()
        self.assertEqual(cat["scenes"]["tavern"], ["old_tower_inn"])
        self.assertEqual(cat["scenes"]["combat"], ["battle_theme_a"])
        self.assertEqual(cat["scenes"]["crypt"], [])
        self.assertEqual(cat["ambience"], {"tavern": "tavern_chatter"})

    def test_every_scene_has_music(self):
        self.assertEqual(set(self.app.SCENES) | {"combat"}, set(self.app._music.SCENE_MUSIC))
        for ids in self.app._music.SCENE_MUSIC.values():
            for tid in ids:
                self.assertIn(tid, self.app._music.TRACKS)

    def test_music_route_serves_built_tracks_only(self):
        r = self.client.get("/music/old_tower_inn.mp3")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "audio/mpeg")
        r.close()
        self.assertEqual(self.client.get("/music/crypt_theme.mp3").status_code, 404)
        self.assertEqual(self.client.get("/music/forgotten_tomb.mp3").status_code, 404)

    def test_dm_sets_the_scene_and_it_sticks(self):
        r = self.client.post("/scene", json={"scene": "crypt"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.app._current_scene_name, "crypt")
        # keyword guessing no longer moves it
        self.assertIsNone(self.app._detect_scene("Entrate nella locanda, il boccale di birra vi aspetta."))
        self.assertEqual(self.app._current_scene_name, "crypt")
        self.assertEqual(self.client.post("/scene", json={"scene": "moon"}).status_code, 400)

    def test_keywords_match_whole_words_only(self):
        # "signore" holds "ore" (mine), "porta" holds "port" (ocean), "quale" holds "ale"
        self.assertIsNone(self.app._detect_scene("Il signore apre la porta. Quale strada?"))
        scene = self.app._detect_scene("Arrivate al castello: i bastioni si stagliano nel cielo.")
        self.assertEqual(scene["name"], "castle")
        self.assertNotIn("keywords", scene)

    def test_page_carries_the_catalog(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('name="music-catalog"', html)
        self.assertIn("old_tower_inn", html)


if __name__ == "__main__":
    unittest.main()
