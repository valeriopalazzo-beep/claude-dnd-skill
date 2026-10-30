"""
test_long_messages.py — a player can write a whole plan or speech.

500 characters cut a real turn in half at the table: a plan for the whole
party, a speech to the council. Actions and whispers now take 2000, and the
page, the server and the terminal wrapper agree on it; so does the chat that
builds a character on /crea.

Run from repo root:
    python -m pytest tests/test_long_messages.py -q
"""
import importlib.util
import pathlib
import re
import unittest

DISPLAY = pathlib.Path(__file__).resolve().parent.parent / "skills" / "dnd" / "display"


class LongMessageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_long", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def test_the_server_keeps_two_thousand_characters(self):
        text = "Avanzo " + "piano " * 400
        self.assertEqual(self.mod.MAX_INPUT_CHARS, 2000)
        self.assertEqual(len(self.mod._sanitize_input(text)), 2000)
        self.assertEqual(self.mod._sanitize_input("a" * 1999), "a" * 1999)

    def test_the_boxes_on_the_page_take_as_many(self):
        page = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")
        for box in ("player-input-text", "whisper-text"):
            tag = re.search(r'<textarea id="' + box + r'"[^>]*>', page).group(0)
            self.assertIn('maxlength="2000"', tag, box)
        crea = (DISPLAY / "templates" / "crea.html").read_text(encoding="utf-8")
        self.assertIn('<textarea id="text" rows="1" maxlength="2000">', crea)

    def test_the_character_chat_agrees_with_its_page(self):
        spec = importlib.util.spec_from_file_location("char_create_long", str(DISPLAY / "char_create.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(mod.MAX_MSG_LEN, 2000)


if __name__ == "__main__":
    unittest.main()
