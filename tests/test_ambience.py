"""
test_ambience.py — ambient sounds under the music.

The place layer follows the scene, the weather layer the calendar's weather
(not under ground); the DM can set either with ambience.py. Only built sounds
are offered, and build_music.py turns the recordings into seamless loops.

Run from repo root:
    python -m pytest tests/test_ambience.py -q
"""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
HTML = DISPLAY / "templates" / "index.html"
NODE = shutil.which("node")
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(DISPLAY.parent / "scripts"))

import ambience  # noqa: E402
import music  # noqa: E402


class CatalogTests(unittest.TestCase):
    def test_every_sound_is_a_free_loop_and_every_name_points_at_one(self):
        for name, s in music.SOUNDS.items():
            self.assertIn(s["id"], music.TRACKS, name)
            self.assertIn(s["layer"], ("place", "weather"))
        for tid, t in music.TRACKS.items():
            if tid.startswith("amb_"):
                self.assertTrue(t.get("loop"), tid)
                self.assertEqual(t["license"], "CC0", tid)
        for table in (music.SCENE_AMBIENCE, music.WEATHER_AMBIENCE):
            for tid in table.values():
                self.assertIn(tid, music.TRACKS)
        self.assertTrue(set(music.SHELTERED) <= set(music.SCENE_MUSIC))

    def test_only_built_sounds_are_offered(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("DND_MUSIC_DIR")
            os.environ["DND_MUSIC_DIR"] = tmp
            try:
                for tid in ("amb_rain", "amb_fire"):
                    pathlib.Path(tmp, tid + ".mp3").write_bytes(b"ID3")
                cat = music.catalog()
            finally:
                if old is None:
                    os.environ.pop("DND_MUSIC_DIR", None)
                else:
                    os.environ["DND_MUSIC_DIR"] = old
        self.assertEqual(cat["weather"], {"rain": "amb_rain"})
        self.assertEqual(cat["ambience"], {"fire": "amb_fire"})
        self.assertEqual(cat["sounds"], {"rain": "amb_rain", "fire": "amb_fire"})
        self.assertIn("cave", cat["sheltered"])

    def test_what_the_command_asks_for(self):
        self.assertEqual(ambience.request_body("rain"), {"weather": "rain"})
        self.assertEqual(ambience.request_body("Fire"), {"place": "fire"})
        self.assertEqual(ambience.request_body("none", "weather"), {"weather": "none"})
        self.assertEqual(ambience.request_body("off"), {"place": "none", "weather": "none"})
        self.assertEqual(ambience.request_body("auto"), {"place": "auto", "weather": "auto"})
        for bad in (("thunder", ""), ("auto", "sky")):
            with self.assertRaises(ValueError):
                ambience.request_body(*bad)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(ambience.main(["thunder"]), 1)
        self.assertIn("Nothing was changed", err.getvalue())


class LoopTests(unittest.TestCase):
    def test_a_loop_has_no_fade_and_lasts(self):
        try:
            import numpy as np
            import build_music
        except ImportError:
            raise unittest.SkipTest("numpy / scipy / soundfile not installed (build_music.py needs them)")
        sr = build_music.SR
        x = np.ones((sr * 10, 2)) * 0.5
        y = build_music.make_loop(x, sr)
        self.assertGreaterEqual(len(y) / sr, build_music.LOOP_MIN_S)
        self.assertGreater(float(np.abs(y[0]).min()), 0.3)      # no fade-in
        self.assertGreater(float(np.abs(y[-1]).min()), 0.3)     # no fade-out


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_ambience", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        saved = (self.mod.AMBIENCE_FILE, self.mod._broadcast)
        self.addCleanup(lambda: setattr(self.mod, "AMBIENCE_FILE", saved[0]) or setattr(self.mod, "_broadcast", saved[1]))
        self.mod.AMBIENCE_FILE = str(pathlib.Path(tmp.name) / "ambience.json")
        self.sent = []
        self.mod._broadcast = self.sent.append

    def test_the_dm_sets_a_layer_and_back_to_auto(self):
        self.assertEqual(self.client.get("/ambience").get_json(), {"ambience": {"place": "auto", "weather": "auto"}})
        r = self.client.post("/ambience", json={"weather": "rain"})
        self.assertEqual(r.get_json()["ambience"], {"place": "auto", "weather": "rain"})
        self.assertEqual(self.sent[-1], {"ambience": {"place": "auto", "weather": "rain"}})
        self.client.post("/ambience", json={"place": "none"})
        self.assertEqual(self.client.get("/ambience").get_json()["ambience"], {"place": "none", "weather": "rain"})
        self.assertEqual(self.client.post("/ambience", json={"place": "rain"}).status_code, 400)   # rain is weather
        self.assertEqual(self.client.post("/ambience", json={"weather": "thunder"}).status_code, 400)
        self.assertIn("ambience_set", self.mod._DM_ENDPOINTS)


_SLICE = re.compile(r"(function _ambiencePick\(cat, choice, scene, weatherKey, combat\).*?)\n// ── end ambience pick ──", re.S)


class PageTests(unittest.TestCase):
    def test_what_plays_where(self):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the ambience block is no longer where this test slices it")
        cat = {"ambience": {"tavern": "tavern_chatter", "cave": "amb_cave"}, "weather": {"rain": "amb_rain"},
               "sounds": {"rain": "amb_rain", "fire": "amb_fire"}, "sheltered": ["cave"]}
        cases = [({}, "tavern", "rain", False), ({}, "cave", "rain", False), ({}, "tavern", "clear", True),
                 ({"place": "fire", "weather": "none"}, "tavern", "rain", False), ({"weather": "rain"}, "cave", "", False),
                 ({"place": "storm"}, "forest", "", False)]
        script = m.group(1) + "\nconst cat = %s;\nconsole.log(JSON.stringify(%s.map(c => _ambiencePick(cat, c[0], c[1], c[2], c[3]))));\n" % (
            json.dumps(cat), json.dumps(cases))
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            out = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        self.assertEqual(out, [
            {"place": "tavern_chatter", "weather": "amb_rain"},
            {"place": "amb_cave", "weather": None},              # under ground the rain is not heard
            {"place": None, "weather": None},                    # combat: no chatter
            {"place": "amb_fire", "weather": None},
            {"place": "amb_cave", "weather": "amb_rain"},        # the DM's choice wins
            {"place": None, "weather": None},                    # a sound that is not built
        ])

    def test_the_page_follows_the_calendar_and_the_dm(self):
        src = HTML.read_text(encoding="utf-8")
        for needle in ("window._ambWeather(wt)", "window._onAmbience(payload.ambience)", "fetch('/ambience'"):
            self.assertIn(needle, src)


if __name__ == "__main__":
    unittest.main()
