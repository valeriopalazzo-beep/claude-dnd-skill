"""
test_backup.py — a campaign can be put back as it was.

scripts/backup.py zips the campaign folder (without media/ unless asked) into
<DATA root>/backups/<campaign>/, keeps the newest ones and restores one after
saving what is there now.

Run from repo root:
    python -m pytest tests/test_backup.py -q
"""
import datetime
import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest
import zipfile

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
sys.path.insert(0, str(SKILL / "scripts"))


def _load():
    spec = importlib.util.spec_from_file_location("backup_under_test", SKILL / "scripts" / "backup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class BackupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bk = _load()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._old_root = os.environ.get("DND_CAMPAIGN_ROOT")
        self._old_dest = os.environ.pop("DND_BACKUP_DIR", None)
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        self.camp = self.root / "campaigns" / "prova"
        (self.camp / "characters").mkdir(parents=True)
        (self.camp / "media").mkdir()
        (self.camp / "state.md").write_text("# Stato\nsessione 1\n", encoding="utf-8", newline="\n")
        (self.camp / "characters" / "lio.md").write_text("# Lio\nPF 9\n", encoding="utf-8", newline="\n")
        (self.camp / "media" / "scene-vallombra.png").write_bytes(b"\x89PNG" + b"x" * 500)
        (self.root / "characters").mkdir()
        (self.root / "characters" / "lio.md").write_text("# Lio (roster)\n", encoding="utf-8", newline="\n")

    def tearDown(self):
        for key, old in (("DND_CAMPAIGN_ROOT", self._old_root), ("DND_BACKUP_DIR", self._old_dest)):
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        self._tmp.cleanup()

    def _at(self, minute):
        return datetime.datetime(2026, 10, 10, 21, minute, 0)

    def test_save_holds_the_campaign_and_the_roster_but_not_media(self):
        path = self.bk.save("prova", "End of session!", now=self._at(0))
        self.assertEqual(path.name, "prova-20261010-210000-end-of-session.zip")
        self.assertEqual(path.parent, self.root / "backups" / "prova")
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
        self.assertEqual(names, {"state.md", "characters/lio.md", "_roster/lio.md"})

    def test_media_is_included_on_request(self):
        path = self.bk.save("prova", media=True, now=self._at(0))
        with zipfile.ZipFile(path) as z:
            self.assertIn("media/scene-vallombra.png", z.namelist())

    def test_only_the_newest_are_kept(self):
        for minute in range(5):
            self.bk.save("prova", keep=3, now=self._at(minute))
        names = [p.name for p in self.bk.archives("prova")]
        self.assertEqual(names, ["prova-20261010-210400.zip", "prova-20261010-210300.zip",
                                 "prova-20261010-210200.zip"])

    def test_restore_puts_back_the_files_and_removes_the_newer_ones(self):
        self.bk.save("prova", "end", now=self._at(0))
        (self.camp / "state.md").write_text("# Stato\nrovinato\n", encoding="utf-8", newline="\n")
        (self.camp / "characters" / "lio.md").unlink()
        (self.camp / "handouts.json").write_text("[]", encoding="utf-8", newline="\n")

        archive, safety = self.bk.restore("prova", "1")

        self.assertEqual(archive.name, "prova-20261010-210000-end.zip")
        self.assertEqual((self.camp / "state.md").read_text(encoding="utf-8"), "# Stato\nsessione 1\n")
        self.assertEqual((self.camp / "characters" / "lio.md").read_text(encoding="utf-8"), "# Lio\nPF 9\n")
        self.assertFalse((self.camp / "handouts.json").exists())
        # media was not in the archive, so it is left alone
        self.assertTrue((self.camp / "media" / "scene-vallombra.png").exists())
        # what the restore replaced can itself be restored
        self.assertTrue(safety.name.endswith("-pre-restore.zip"))
        with zipfile.ZipFile(safety) as z:
            self.assertEqual(z.read("state.md").decode("utf-8"), "# Stato\nrovinato\n")
            self.assertIn("handouts.json", z.namelist())

    def test_restore_leaves_the_roster_unless_asked(self):
        self.bk.save("prova", now=self._at(0))
        roster = self.root / "characters" / "lio.md"
        roster.write_text("# Lio (dopo)\n", encoding="utf-8", newline="\n")
        self.bk.restore("prova", "1")
        self.assertEqual(roster.read_text(encoding="utf-8"), "# Lio (dopo)\n")
        self.bk.restore("prova", "prova-20261010-210000.zip", roster=True)
        self.assertEqual(roster.read_text(encoding="utf-8"), "# Lio (roster)\n")

    def test_the_command_does_not_restore_without_yes(self):
        self.bk.save("prova", now=self._at(0))
        (self.camp / "state.md").write_text("dopo", encoding="utf-8", newline="\n")
        self.assertEqual(self.bk.main(["restore", "--campaign", "prova", "1"]), 2)
        self.assertEqual((self.camp / "state.md").read_text(encoding="utf-8"), "dopo")
        self.assertEqual(self.bk.main(["restore", "--campaign", "prova", "1", "--yes"]), 0)
        self.assertEqual((self.camp / "state.md").read_text(encoding="utf-8"), "# Stato\nsessione 1\n")

    def test_what_cannot_be_done_changes_nothing(self):
        self.assertEqual(self.bk.main(["save", "--campaign", "non-esiste"]), 1)
        self.assertEqual(self.bk.main(["save", "--campaign", "../prova"]), 1)
        self.assertEqual(self.bk.main(["restore", "--campaign", "prova", "1", "--yes"]), 1)
        self.assertFalse((self.root / "backups").exists())
        self.bk.save("prova", now=self._at(0))
        self.assertEqual(self.bk.main(["restore", "--campaign", "prova", "7", "--yes"]), 1)

    def test_an_archive_with_a_path_outside_the_campaign_is_refused(self):
        folder = self.root / "backups" / "prova"
        folder.mkdir(parents=True)
        with zipfile.ZipFile(folder / "prova-20261010-210000.zip", "w") as z:
            z.writestr("../../fuori.txt", "x")
        with self.assertRaises(self.bk.BackupError):
            self.bk.restore("prova", "1")
        self.assertFalse((self.root / "fuori.txt").exists())
        self.assertEqual((self.camp / "state.md").read_text(encoding="utf-8"), "# Stato\nsessione 1\n")

    def test_backups_can_live_somewhere_else(self):
        other = self.root / "altrove"
        os.environ["DND_BACKUP_DIR"] = str(other)
        path = self.bk.save("prova", now=self._at(0))
        self.assertEqual(path.parent, other / "prova")
        self.assertEqual([p.name for p in self.bk.archives("prova")], [path.name])


if __name__ == "__main__":
    unittest.main()
