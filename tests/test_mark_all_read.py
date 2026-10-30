"""
test_mark_all_read.py — one tap clears the New marks of a whole list.

The Bestiary, People met and Documents mark every page that grew as New, and
the only way to clear them was to open each page. With fifty people in the
notebook that is fifty taps. Each of those lists now has a "Mark all as read"
button, shown only on the list and only while something is New. (Rumors and
the Timeline already count as read when they are opened.)

Run from repo root:
    python -m pytest tests/test_mark_all_read.py -q
"""
import json
import pathlib
import re
import unittest

DISPLAY = pathlib.Path(__file__).resolve().parent.parent / "skills" / "dnd" / "display"
PAGE = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")


class MarkAllReadTests(unittest.TestCase):
    def test_each_list_with_new_marks_has_the_button(self):
        for panel in ("beast", "people", "handout"):
            tag = re.search(r'<button id="' + panel + r'-allread"[^>]*>', PAGE)
            self.assertIsNotNone(tag, panel)
            self.assertIn('data-i18n="read.all"', tag.group(0))
            self.assertIn(" hidden", tag.group(0), "hidden until something is New")
            self.assertIn("getElementById('" + panel + "-allread')", PAGE, panel + " wires it")

    def test_the_button_remembers_every_page_at_once(self):
        self.assertIn("allRead.addEventListener('click', () => { remember(...beasts);", PAGE)
        self.assertIn("allRead.addEventListener('click', () => { remember(...people);", PAGE)
        self.assertIn("_handoutMarkSeen(..._handouts.map(h => h.id));", PAGE)

    def test_it_is_worded_in_every_language(self):
        for path in (DISPLAY / "i18n").glob("*.json"):
            words = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(words.get("read.all"), path.name)


if __name__ == "__main__":
    unittest.main()
