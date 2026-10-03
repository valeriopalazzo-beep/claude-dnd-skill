"""Sound effects work out of the box (display/audio.py + dnd-display-app.py).

Detection is on from startup — each browser has its own on/off switch — and
the trigger words cover every language the display ships, so Italian
narration plays sounds without setting `sfx_languages` in state.md.
Effects are recorded combat sounds and play only while the initiative
tracker runs.
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

    def test_italian_narration_triggers_a_sound_in_combat(self):
        self.audio.set_sfx_languages(self.app._display_sfx_languages())
        self.audio.on_text("Il goblin estrae la spada.", combat=True)
        self.assertEqual(self.sent, [{"sfx": "sword"}])
        self.sent.clear()
        self.audio.on_text("Silenzio. Nessuno parla.", combat=True)
        self.assertEqual(self.sent, [])

    def test_no_sound_outside_combat(self):
        self.audio.set_sfx_languages(self.app._display_sfx_languages())
        self.audio.on_text("Il goblin estrae la spada e apre la porta.")
        self.assertEqual(self.sent, [])

    def test_combat_actions_pick_the_right_recording(self):
        self.audio.set_sfx_languages(self.app._display_sfx_languages())
        cases = {
            "Thalia scocca una freccia verso l'orco.": "arrow",
            "Il bandito spara un quadrello con la balestra.": "crossbow",
            "Il nano cala il martello sull'elmo.": "blunt",
            "L'ascia si pianta nello scudo di legno.": "shield",
            "Il fendente del goblin va a vuoto.": "miss",
            "Mira beve la pozione di guarigione.": "potion",
            "Lanci un dardo incantato.": "spell",
            "L'orco crolla al suolo.": "fall",
            "The rogue looses an arrow.": "arrow",
        }
        for text, sfx in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.audio.detect(text, combat=True), sfx)

    def test_combat_effects_are_recordings(self):
        for name in self.audio.COMBAT_SFX:
            with self.subTest(name=name):
                wav = self.audio.get_sfx_wav(name)
                self.assertIsNotNone(wav)
                self.assertEqual(wav[:4], b"RIFF")
        self.assertIsNone(self.audio.get_sfx_wav("../tts"))

    def test_combat_follows_the_initiative_tracker(self):
        saved = self.app._current_stats.get("turn_order")
        try:
            self.app._current_stats["turn_order"] = None
            self.assertFalse(self.app._in_combat())
            self.app._current_stats["turn_order"] = {"order": ["Mira"], "current": "Mira", "round": 1}
            self.assertTrue(self.app._in_combat())
        finally:
            self.app._current_stats["turn_order"] = saved


if __name__ == "__main__":
    unittest.main()
