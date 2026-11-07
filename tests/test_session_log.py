"""
test_session_log.py — one entry of session-log.md for the resume.

Run from repo root:
    python -m pytest tests/test_session_log.py -q
"""
import importlib.util
import io
import pathlib
import tempfile
import unittest
from contextlib import redirect_stdout

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
spec = importlib.util.spec_from_file_location("session_log", str(SKILL / "scripts" / "session_log.py"))
sl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sl)

LOG = """# Session Log

## Session 12 — 2026-11-04 — Kaldrheim

### Session Recap
Old night.

## Session 13 — 2026-11-06 — Hvalhall *(in corso — fino al 3 Ventaio, mezzogiorno)*

### Recap
Tonight so far.
```
## not a heading inside a fence
```

## Session 11 — 2026-10-30 — written out of order
Older.
"""


class SessionLogTests(unittest.TestCase):
    def test_the_running_entry_wins(self):
        out = sl.current(LOG)
        self.assertTrue(out.startswith("## Session 13"))
        self.assertIn("Tonight so far.", out)
        self.assertIn("## not a heading inside a fence", out)
        self.assertNotIn("Old night.", out)
        self.assertNotIn("Older.", out)

    def test_without_a_running_entry_the_highest_number(self):
        text = LOG.replace(" *(in corso — fino al 3 Ventaio, mezzogiorno)*", "")
        self.assertTrue(sl.current(text).startswith("## Session 13"))
        self.assertTrue(sl.current("## Prologue\nx\n").startswith("## Prologue"))
        self.assertIsNone(sl.current("no entries"))

    def test_entry_and_headings(self):
        self.assertIn("Old night.", sl.entry(LOG, 12))
        self.assertIsNone(sl.entry(LOG, 99))
        self.assertEqual([h[:13] for h, _ in sl.entries(LOG)], ["## Session 12", "## Session 13", "## Session 11"])

    def test_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "session-log.md"
            p.write_text(LOG, encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(sl.main(["current", "--file", str(p)]), 0)
            self.assertIn("in corso", out.getvalue())
            self.assertEqual(sl.main(["entry", "99", "--file", str(p)]), 2)
            self.assertEqual(sl.main(["current", "--file", str(p) + ".missing"]), 2)


if __name__ == "__main__":
    unittest.main()
