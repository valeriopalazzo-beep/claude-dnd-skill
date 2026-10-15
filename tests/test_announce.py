"""
test_announce.py — tracker.py and calendar.py really reach the display.

Both announce through send.py --dice. They used subprocess.Popen with
capture_output/timeout, which Popen refuses: the TypeError was swallowed and
no announcement was ever sent. A stand-in send.py records what arrives.

Run from repo root:
    python -m pytest tests/test_announce.py -q
"""
import importlib.util
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
sys.path.insert(0, str(SKILL / "display"))
sys.path.insert(0, str(SKILL / "scripts"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FAKE_SEND = """import sys
data = sys.stdin.buffer.read().decode("utf-8")
with open(sys.argv[0] + ".out", "a", encoding="utf-8") as f:
    f.write(" ".join(sys.argv[1:]) + "|" + data + "\\n")
"""


class AnnounceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.fake = pathlib.Path(tmp.name) / "send.py"
        self.fake.write_text(FAKE_SEND, encoding="utf-8")

    def received(self):
        out = pathlib.Path(str(self.fake) + ".out")
        return out.read_text(encoding="utf-8").splitlines() if out.exists() else []

    def test_the_tracker_announces(self):
        tracker = _load("dnd_tracker_announce", SKILL / "scripts" / "tracker.py")
        tracker.SEND_PY = str(self.fake)
        tracker._send_announce("Lyra — Avvelenato termina")
        self.assertEqual(self.received(), ["--dice|Lyra — Avvelenato termina"])

    def test_the_calendar_announces_the_date(self):
        cal = _load("dnd_calendar_announce", SKILL / "scripts" / "calendar.py")
        cal.SEND_PY = str(self.fake)
        cal._format_date = lambda c: "15 Brumosa 1147"
        cal._send_date({})
        self.assertEqual(self.received(), ["--dice|📅 15 Brumosa 1147"])


if __name__ == "__main__":
    unittest.main()
