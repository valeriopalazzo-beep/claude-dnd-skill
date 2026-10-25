"""
test_context_size.py — the conversation's size, read from the transcript.

Run from repo root:
    python -m pytest tests/test_context_size.py -q
"""
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest
from contextlib import redirect_stdout

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
spec = importlib.util.spec_from_file_location("context_size", str(SKILL / "scripts" / "context_size.py"))
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)


def call(fresh, read, write):
    return {"type": "assistant", "message": {"usage": {
        "input_tokens": fresh, "cache_read_input_tokens": read, "cache_creation_input_tokens": write}}}


class ContextSizeTests(unittest.TestCase):
    def transcript(self, rows):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = pathlib.Path(tmp.name) / "t.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        return p

    def run_main(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cs.main(list(argv))
        return code, out.getvalue().strip()

    def test_the_last_call_counts(self):
        p = self.transcript([call(1, 100_000, 5_000), {"type": "user", "message": {}}, call(2, 410_000, 3_000)])
        self.assertEqual(cs.last_context(p), 413_002)
        code, line = self.run_main("--transcript", str(p))
        self.assertEqual(code, 0)
        self.assertIn("413k tokens — checkpoint due", line)

    def test_under_the_limit_is_fine(self):
        p = self.transcript([call(1, 120_000, 0)])
        _, line = self.run_main("--transcript", str(p), "--limit", "300000")
        self.assertIn("fine", line)

    def test_no_call_yet(self):
        p = self.transcript([{"type": "user", "message": {}}])
        self.assertIsNone(cs.last_context(p))


if __name__ == "__main__":
    unittest.main()
