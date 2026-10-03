"""Sound effects work out of the box (display/audio.py + dnd-display-app.py).

Detection is on from startup — each browser has its own on/off switch — and
the trigger words cover every language the display ships, so Italian
narration plays sounds without setting `sfx_languages` in state.md.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"


def _load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class SfxDefaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _load(DISPLAY / "dnd-display-app.py", "sfx_display_app_under_test")
        cls.audio = cls.app._audio

    def setUp(self):
        self.sent = []
        self._saved = (self.audio._broadcast_fn, list(self.audio._SFX_LANGUAGES), self.audio._sfx_on)
        self.audio.set_broadcast(self.sent.append)

    def tearDown(self):
        fn, langs, on = self._saved
        self.audio.set_broadcast(fn)
        self.audio.set_sfx_languages(langs)
        self.audio.set_sfx(on)

    def test_detection_is_on_by_default(self):
        self.assertTrue(_load(DISPLAY / "audio.py", "fresh_audio_under_test")._sfx_on)

    def test_display_languages_include_italian_and_english_last(self):
        langs = self.app._display_sfx_languages()
        self.assertIn("it", langs)
        self.assertEqual(langs[-1], "en")

    def test_italian_narration_triggers_a_sound(self):
        self.audio.set_sfx_languages(self.app._display_sfx_languages())
        self.audio.on_text("Il goblin estrae la spada.")
        self.assertEqual(self.sent, [{"sfx": "sword"}])
        self.sent.clear()
        self.audio.on_text("Silenzio. Nessuno parla.")
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
