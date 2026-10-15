"""
test_check_input.py — what check_input.py says about the roll preferences.

roll_prefs.json keeps every character's "who rolls" choice, of any campaign.
Only the characters at the table now (the display's stats.json) are reported.

Run from repo root:
    python -m pytest tests/test_check_input.py -q
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
CHECK = SKILL / "display" / "check_input.py"


class RollPrefTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.rt = pathlib.Path(tmp.name)
        (self.rt / "roll_prefs.json").write_text(json.dumps({"Ajeje": "players", "Thorin": "auto", "Lyra": "auto"}), encoding="utf-8")

    def run_check(self):
        (self.rt / ".input_queue").write_text("[Ajeje]: apro la porta", encoding="utf-8")
        env = dict(os.environ, DND_RUNTIME_DIR=str(self.rt), PYTHONUTF8="1")
        r = subprocess.run([sys.executable, str(CHECK)], capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_only_the_characters_at_the_table_are_reported(self):
        (self.rt / "stats.json").write_text(json.dumps({"players": [{"name": "Ajeje"}, {"name": "lyra"}]}), encoding="utf-8")
        out = self.run_check()
        self.assertIn("[[Ajeje roll mode: players]]", out)
        self.assertIn("[[Lyra roll mode: auto]]", out)
        self.assertNotIn("Thorin", out)
        self.assertIn("[Ajeje]: apro la porta", out)

    def test_with_no_party_known_every_preference_is_reported(self):
        out = self.run_check()
        for name in ("Ajeje", "Thorin", "Lyra"):
            self.assertIn(f"[[{name} roll mode:", out)


if __name__ == "__main__":
    unittest.main()
