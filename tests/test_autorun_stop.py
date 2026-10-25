"""
test_autorun_stop.py — a wait that is replaced or stopped takes nothing.

Before a checkpoint the conversation is cleared: a wait still running then would
take a player's action and print it where nobody reads it. `--stop` ends the
running wait, and a replaced wait leaves the queue and the notes for the next one.

Run from repo root:
    python -m pytest tests/test_autorun_stop.py -q
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
WAIT = SKILL / "display" / "autorun_wait.py"


class StopTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.rt = pathlib.Path(tmp.name)
        self.env = dict(os.environ, DND_RUNTIME_DIR=str(self.rt), PYTHONUTF8="1")

    def start_wait(self):
        p = subprocess.Popen([sys.executable, str(WAIT)], env=self.env,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        for _ in range(100):
            if (self.rt / ".autorun-session").exists():
                break
            time.sleep(0.1)
        return p

    def test_stop_ends_the_wait_and_leaves_the_queue(self):
        p = self.start_wait()
        subprocess.run([sys.executable, str(WAIT), "--stop"], env=self.env, check=True,
                       capture_output=True, timeout=30)
        out, _ = p.communicate(timeout=30)
        self.assertEqual(out, "")
        (self.rt / ".input_queue").write_text("[Aldric]: apro la porta", encoding="utf-8")
        time.sleep(0.5)
        self.assertTrue((self.rt / ".input_queue").exists())
        self.assertFalse((self.rt / ".autorun-session").exists())

    def test_the_next_wait_gets_what_came_in_meanwhile(self):
        p = self.start_wait()
        subprocess.run([sys.executable, str(WAIT), "--stop"], env=self.env, check=True,
                       capture_output=True, timeout=30)
        p.communicate(timeout=30)
        (self.rt / ".input_queue").write_text("[Aldric]: apro la porta", encoding="utf-8")
        nxt = subprocess.run([sys.executable, str(WAIT)], env=self.env, capture_output=True,
                             text=True, timeout=60)
        self.assertIn("[Aldric]: apro la porta", nxt.stdout)

    def test_a_replaced_wait_prints_nothing(self):
        p = self.start_wait()
        q = self.start_wait()
        out, _ = p.communicate(timeout=30)
        self.assertEqual(out, "")
        (self.rt / ".input_queue").write_text("[Lyra]: ascolto", encoding="utf-8")
        out, _ = q.communicate(timeout=60)
        self.assertIn("[Lyra]: ascolto", out)


if __name__ == "__main__":
    unittest.main()
