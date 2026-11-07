"""
test_quota.py — the 5-hour usage limit, saved by the status line and read back.

Run from repo root:
    python -m pytest tests/test_quota.py -q
"""
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
spec = importlib.util.spec_from_file_location("quota", str(SKILL / "scripts" / "quota.py"))
qt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qt)

NOW = 1_800_000_000


def saved(pct, resets_at=NOW + 3600, saved_at=NOW, **extra):
    window = {"used_percentage": pct, **extra}
    if resets_at is not None:
        window["resets_at"] = resets_at
    return {"rate_limits": {"five_hour": window}, "saved_at": saved_at}


class QuotaTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.file = pathlib.Path(tmp.name) / "quota.json"

    def test_levels(self):
        self.assertEqual(qt.parse(saved(40), NOW)["level"], "fine")
        self.assertEqual(qt.parse(saved(75), NOW)["level"], "warn")
        self.assertEqual(qt.parse(saved(90), NOW)["level"], "save")

    def test_a_window_that_has_reset_says_nothing(self):
        self.assertIsNone(qt.parse(saved(95, resets_at=NOW - 1), NOW))

    def test_without_reset_time_an_old_file_says_nothing(self):
        self.assertIsNotNone(qt.parse(saved(50, resets_at=None, saved_at=NOW - 60), NOW))
        self.assertIsNone(qt.parse(saved(50, resets_at=None, saved_at=NOW - 6 * 3600), NOW))

    def test_tolerant_of_names_and_formats(self):
        camel = {"rateLimits": {"fiveHour": {"usedPercentage": "80", "resetsAt": (NOW + 60) * 1000}}, "saved_at": NOW}
        q = qt.parse(camel, NOW)
        self.assertEqual((q["pct"], q["resets_at"], q["level"]), (80.0, NOW + 60, "warn"))
        iso = saved(10, resets_at="2027-02-01T08:00:00Z")
        self.assertIsNotNone(qt.parse(iso, NOW))
        frac = {"rate_limits": {"five_hour": {"utilization": 0.92, "resets_at": NOW + 60}}}
        self.assertEqual(qt.parse(frac, NOW)["level"], "save")

    def test_missing_or_broken_data_says_nothing(self):
        for raw in ({}, {"rate_limits": {}}, {"rate_limits": {"five_hour": {"used_percentage": "x"}}}, [], None):
            self.assertIsNone(qt.parse(raw, NOW))
        self.assertIsNone(qt.read(self.file))          # no file at all
        self.file.write_text("{not json", encoding="utf-8")
        self.assertIsNone(qt.read(self.file))

    def test_record_then_read(self):
        status = {"model": {"display_name": "Opus"}, "rate_limits": {"five_hour": {"used_percentage": 77, "resets_at": NOW + 600}}}
        self.assertTrue(qt.record(status, self.file, now=NOW))
        self.assertEqual(qt.read(self.file, now=NOW)["level"], "warn")
        self.assertFalse(qt.record({"model": {}}, self.file))   # nothing to save keeps the old file
        self.assertEqual(qt.read(self.file, now=NOW)["pct"], 77.0)

    def test_the_line(self):
        self.assertIn("unknown", qt.line(None))
        self.assertIn("warn", qt.line({"pct": 78.4, "resets_at": NOW, "level": "warn"}))
        self.assertIn("/dm:dnd save", qt.line({"pct": 93, "resets_at": None, "level": "save"}))
        self.file.write_text(json.dumps(saved(20, resets_at=None, saved_at=1)), encoding="utf-8")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(qt.main(["--json", "--file", str(self.file)]), 0)
        self.assertEqual(out.getvalue().strip(), "{}")  # saved in 1970: too old to say anything


if __name__ == "__main__":
    unittest.main()
