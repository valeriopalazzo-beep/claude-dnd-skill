"""
test_voice_input.py — the player speaks the action instead of typing it.

A microphone next to the action box and the secret-note box uses the
browser's own speech recognition, in the display's language, and writes what
it heard after what is already there. Nothing is sent by itself.

Run from repo root:
    python -m pytest tests/test_voice_input.py -q
"""
import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"
HTML = DISPLAY / "templates" / "index.html"
NODE = shutil.which("node")

_SLICE = re.compile(r"(function _micLang\(lang\).*?)\n// ── end mic helpers ──", re.S)


class VoiceInputTests(unittest.TestCase):
    def test_language_and_how_the_words_are_added(self):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the voice input block is no longer where this test slices it")
        script = m.group(1) + """
console.log(JSON.stringify([
  _micLang('it'), _micLang('en'), _micLang('ja'), _micLang(''), _micLang(undefined),
  _micMerge('', 'apro la porta'), _micMerge('Attacco', 'il goblin'), _micMerge('Attacco ', 'il goblin'),
  _micMerge('abc', '   '), _micMerge('x'.repeat(498), 'lunghissimo', 500).length,
]));
"""
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            out = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        self.assertEqual(out, ["it-IT", "en-US", "ja-JA", "en-US", "en-US",
                               "Apro la porta", "Attacco il goblin", "Attacco il goblin", "abc", 500])

    def test_the_buttons_are_there_hidden_until_the_browser_can_listen(self):
        src = HTML.read_text(encoding="utf-8")
        self.assertRegex(src, r'<button id="mic-btn" class="mic-btn" type="button" hidden')
        self.assertRegex(src, r'<button id="whisper-mic" class="mic-btn" type="button" hidden')
        block = src[src.index("(function _initMic() {"):]
        block = block[:block.index("\n})();")]
        self.assertIn("window.SpeechRecognition || window.webkitSpeechRecognition", block)
        self.assertIn("window.isSecureContext", block)
        self.assertNotIn("stage", block.lower(), "speaking never sends the action by itself")

    def test_strings_exist_in_every_language(self):
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("mic.speak", "mic.stop", "mic.listening", "mic.denied", "mic.noSpeech", "mic.failed"):
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
