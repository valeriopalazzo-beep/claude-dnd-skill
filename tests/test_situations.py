"""
test_situations.py — the situational sections and their triggers (ADR 0005).

SKILL.md keeps a table of triggers; SKILL-situations.md holds the sections;
the scripts that open a situation print a reminder naming one. All three must
agree, or a reminder points at a section that is not there.

Run from repo root:
    python -m pytest tests/test_situations.py -q
"""
import importlib.util
import io
import pathlib
import re
import unittest
from contextlib import redirect_stderr

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
spec = importlib.util.spec_from_file_location("situations", str(SKILL / "scripts" / "situations.py"))
sit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sit)


def headings(path):
    return [l[3:].strip() for l in path.read_text(encoding="utf-8").splitlines() if l.startswith("## ")]


class SituationTests(unittest.TestCase):
    def test_reminder_only_for_the_opening_command(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertTrue(sit.remind("Chases", ("start",), argv=["start", "--quarry", "x"]))
            self.assertFalse(sit.remind("Chases", ("start",), argv=["act", "Lyra", "dash"]))
            self.assertTrue(sit.remind("Supplies", ("set",), argv=["-c", "camp", "set", "rations=4"]))
            self.assertTrue(sit.remind("Images & Maps", argv=["--anything"]))
        self.assertEqual(err.getvalue().count('>> SKILL-situations.md -> "'), 3)

    def test_every_trigger_names_a_moved_section(self):
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        start = skill.index("## Situations")
        table = skill[start:skill.index("\n## ", start + 5)]
        named = re.findall(r"\|\s*\*([^*|]+)\*\s*\|\s*$", table, re.M)
        moved = headings(SKILL / "SKILL-situations.md")
        self.assertEqual(len(named), len(moved))
        for name in named:
            self.assertTrue(any(h.startswith(name) for h in moved), name)
        core = headings(SKILL / "SKILL.md")
        for h in moved:
            self.assertNotIn(h, core, "a section must live in one file only")

    def test_every_script_reminder_names_a_section(self):
        moved = headings(SKILL / "SKILL-situations.md")
        calls = []
        for f in list((SKILL / "scripts").glob("*.py")) + list((SKILL / "display").glob("*.py")):
            calls += re.findall(r"situations\.remind\(('[^']+'|\"[^\"]+\")", f.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(calls), 10)
        for c in calls:
            name = c[1:-1]
            self.assertTrue(any(h.startswith(name) for h in moved), name)

    def test_the_core_rules_stay_in_skill_md(self):
        core = headings(SKILL / "SKILL.md")
        for must in ("Active DM Mode", "Rules enforcement", "Long sessions", "One call where one is enough", "Situations"):
            self.assertTrue(any(h.startswith(must) for h in core), must)
        self.assertIn("**Reference modules:**", (SKILL / "SKILL.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
