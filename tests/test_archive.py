"""
test_archive.py — state.md's cold half in state-archive.md (ADR 0003, 0004).

Run from repo root:
    python -m pytest tests/test_archive.py -q
"""
import importlib.util
import io
import os
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
sys.path.insert(0, str(SKILL / "scripts"))
spec = importlib.util.spec_from_file_location("archive", str(SKILL / "scripts" / "archive.py"))
ar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ar)

STATE = """# Campaign: prova

## Current Situation
Al ponte.

## Faction Moves
*Updated at the end of each session.*
- **Sessione 5 (fine) — Figli dell'Inverno (Solmund):** aspettano alla cengia.
- **Sessione 4 — Corvi Grigi:** la jarl aspetta.
- **Sessione 3 — Figli dell'Inverno:** comprano prigionieri.
- **Sessione 2 — Gilda degli Ingranaggi:** prende la Torre.
  e mette i Birri.
- **Sessione 1 — Figli dell'Inverno:** nascono.

## Recent Events
- **Sessione 5 (3 Ventaio):** imboscata vinta.
- **Sessione 4:** vena domata.
- **Sessione 2 (29 Nebbiaio):** Pontile dei magli.
- **Secret rivelati dal prep (s2):** il registro.

## Live State Flags
**Cover:**
- Berta: vista dai goblin.
- Finocchio: sabotatore delle barche — INTACT.

## Continuity Archive
*Compressed.*

### Session 2 — la Torre
- Torre presa.

### Session 1 — Sottoponte
- Lampionaio 117.

## Campaign Arc
```yaml
## not a heading inside the arc
type: dynamic
```

## Session Flags
autorun: true
"""


class ArchiveTests(unittest.TestCase):
    def rotated(self, keep=2):
        return ar.rotate(STATE, "", keep)

    def test_rotate_keeps_the_last_sessions_hot(self):
        state, archive, report = self.rotated()
        self.assertIn("Sessione 5 (fine)", state)
        self.assertIn("Sessione 4 — Corvi Grigi", state)
        self.assertNotIn("Sessione 3", state)
        self.assertNotIn("Gilda degli Ingranaggi", state)
        self.assertIn("Secret rivelati dal prep (s2)", state)     # no session number: stays
        self.assertNotIn("### Session 1", state)
        self.assertIn("## Continuity Archive", state)
        self.assertIn("```yaml\n## not a heading inside the arc", state)
        self.assertIn("autorun: true", state)
        self.assertEqual(report, ["Faction Moves: 3 moved", "Recent Events: 1 moved", "Continuity Archive: 2 entries moved"])

    def test_nothing_is_lost(self):
        state, archive, _ = self.rotated()
        for line in STATE.splitlines():
            if line.startswith(("- ", "  e mette")):
                self.assertTrue(line in state or line in archive, line)

    def test_faction_moves_filed_by_faction_newest_first(self):
        _, archive, _ = self.rotated()
        figli = ar.show(archive, "Faction Moves", "Figli dell'Inverno")
        self.assertLess(figli.index("Sessione 3"), figli.index("Sessione 1"))
        self.assertIn("e mette i Birri", ar.show(archive, "Faction Moves", "Gilda"))
        self.assertIn("### Sessione 2", archive)
        cont = ar.show(archive, "Continuity Archive")
        self.assertLess(cont.index("Session 2"), cont.index("Session 1"))

    def test_the_pointer_comes_after_the_italic_note(self):
        state, _, _ = self.rotated()
        fm = state[state.index("## Faction Moves"):]
        lines = fm.splitlines()
        self.assertTrue(lines[1].startswith("*Updated"))
        self.assertIn("state-archive.md", lines[2])

    def test_rotate_twice_files_into_existing_entries(self):
        state, archive, _ = self.rotated()
        state = state.replace("## Recent Events\n", "## Recent Events\n- **Sessione 7:** nuova.\n")
        state = state.replace("*Updated at the end of each session.*\n", "*Updated at the end of each session.*\n- **Sessione 7 — Figli dell'Inverno:** salgono.\n")
        state2, archive2, report = ar.rotate(state, archive, 2)
        figli = ar.show(archive2, "Faction Moves", "Figli dell'Inverno")
        self.assertLess(figli.index("Sessione 5"), figli.index("Sessione 3"))
        self.assertEqual(archive2.count("### Figli dell'Inverno"), 1)
        self.assertEqual(archive2.count("state-archive.md"), 0)   # the pointer lives in state.md only
        self.assertEqual(state2.count("Older entries"), 3)
        again = ar.rotate(state2, archive2, 2)
        self.assertEqual(again[2], [])

    def test_move_a_flag_and_add_text(self):
        state, archive, report = ar.move(STATE, "", "Live State Flags", bullet="sabotatore")
        self.assertNotIn("sabotatore", state)
        self.assertIn("sabotatore", ar.show(archive, "Live State Flags"))
        state, archive, _ = ar.move(state, archive, "Continuity Archive", entry="Session 1")
        self.assertIn("Lampionaio 117", archive)
        self.assertIn("Torre presa", state)
        archive = ar.file_into(archive, "Campaign Arc", ["Dopo la sessione 5: il 1a non atterra.\n"], entry="Steering notes s1-s5")
        self.assertIn("1a non atterra", ar.show(archive, "Campaign Arc", "Steering"))
        with self.assertRaises(SystemExit):
            ar.move(STATE, "", "Live State Flags", bullet="nessuno ha questo")

    def test_grep_and_index(self):
        _, archive, _ = self.rotated()
        hits = ar.grep(archive, ["torre"])
        self.assertIn("[Faction Moves › Gilda degli Ingranaggi]", hits)
        self.assertIn("[Continuity Archive › Session 2 — la Torre] - Torre presa.", hits)
        self.assertIn("Recent Events", ar.index(archive))

    def test_cli_on_a_campaign(self):
        with tempfile.TemporaryDirectory() as tmp:
            camp = pathlib.Path(tmp) / "campaigns" / "prova"
            camp.mkdir(parents=True)
            (camp / "state.md").write_text(STATE, encoding="utf-8")
            old = os.environ.get("DND_CAMPAIGN_ROOT")
            os.environ["DND_CAMPAIGN_ROOT"] = tmp
            try:
                out = io.StringIO()
                with redirect_stdout(out):
                    ar.main(["-c", "prova", "show", "Faction Moves"])
                    ar.main(["-c", "prova", "rotate", "--dry-run"])
                self.assertIn("has no state-archive.md yet", out.getvalue())
                self.assertFalse((camp / "state-archive.md").exists())
                with redirect_stdout(io.StringIO()):
                    ar.main(["-c", "prova", "rotate"])
                self.assertIn("Gilda", (camp / "state-archive.md").read_text(encoding="utf-8"))
                self.assertNotIn("Gilda", (camp / "state.md").read_text(encoding="utf-8"))
            finally:
                if old is None:
                    os.environ.pop("DND_CAMPAIGN_ROOT", None)
                else:
                    os.environ["DND_CAMPAIGN_ROOT"] = old


if __name__ == "__main__":
    unittest.main()
