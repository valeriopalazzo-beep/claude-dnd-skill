"""
test_pdf_workdir.py — the sheet and the book are printed in a plain folder.

On Windows, Python 3.12's mkdtemp gives its folder an ACL of its own, and
headless Chrome was seen to exit 0 there without writing the PDF. to_pdf
works in a folder made with a plain mkdir and removes it afterwards.

Run from repo root:
    python -m pytest tests/test_pdf_workdir.py -q
"""
import os
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
sys.path.insert(0, str(SKILL / "display"))

import sheet_print  # noqa: E402


class WorkDirTests(unittest.TestCase):
    def test_a_plain_folder_in_temp_that_goes_away(self):
        made = []
        real_mkdtemp = tempfile.mkdtemp
        tempfile.mkdtemp = lambda *a, **k: self.fail("mkdtemp is not used")
        try:
            with sheet_print._work_dir() as path:
                made.append(path)
                self.assertTrue(os.path.isdir(path))
                self.assertEqual(os.path.dirname(path), tempfile.gettempdir())
                pathlib.Path(path, "x.txt").write_text("x", encoding="utf-8")
        finally:
            tempfile.mkdtemp = real_mkdtemp
        self.assertFalse(os.path.exists(made[0]))

    def test_to_pdf_prints_when_a_browser_is_there(self):
        if not sheet_print.find_browser():
            raise unittest.SkipTest("no browser on this machine")
        data = sheet_print.to_pdf("<html><body><h1>Prova</h1></body></html>", timeout=60)
        self.assertTrue(data and data.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
