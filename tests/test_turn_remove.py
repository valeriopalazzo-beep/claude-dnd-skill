"""
test_turn_remove.py — the dead leave the turn order.

A creature killed goes out of the display's order (push_stats.py
--turn-remove, which tracker.py hp / hit sends at 0 HP), and so does a PC
dead on three failed death saves; a dying PC at 0 HP stays, still rolling.
The pointer is not moved: the DM moves it on with --turn-current.

Run from repo root:
    python -m pytest tests/test_turn_remove.py -q
"""
import importlib.util
import io
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

ROOT = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = ROOT / "skills" / "dnd" / "display"
SCRIPTS = ROOT / "skills" / "dnd" / "scripts"
sys.path.insert(0, str(SCRIPTS))


class TurnRemoveServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_turn_remove", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        m = self.mod
        saved = (m._broadcast, m._persist_stats, m._persist_log, m._persist_tail)
        def restore():
            (m._broadcast, m._persist_stats, m._persist_log, m._persist_tail) = saved
        self.addCleanup(restore)
        m._broadcast = lambda payload: None
        m._persist_stats = m._persist_log = m._persist_tail = lambda: None
        with m._stats_lock:
            self._stats = dict(m._current_stats)
            m._current_stats.clear()
            m._current_stats.update({
                "players": [{"name": "Lyra", "hp": {"current": 7, "max": 12}},
                            {"name": "Hernio", "hp": {"current": 9, "max": 9}}],
                "turn_order": {"order": ["Goblin 1", "Lyra", "Goblin 2", "Hernio"], "current": "Lyra", "round": 2,
                               "init": {"Goblin 1": 17, "Lyra": 15, "Goblin 2": 12, "Hernio": 8}},
            })
        self.addCleanup(self._restore_stats)

    def _restore_stats(self):
        with self.mod._stats_lock:
            self.mod._current_stats.clear()
            self.mod._current_stats.update(self._stats)

    def push(self, payload):
        r = self.client.post("/stats", json=payload)
        self.assertLess(r.status_code, 300, r.data)
        return self.mod._current_stats["turn_order"]

    def test_a_killed_monster_leaves_the_order(self):
        to = self.push({"turn_order": {"remove": ["goblin 2"]}})
        self.assertEqual(to["order"], ["Goblin 1", "Lyra", "Hernio"])
        self.assertNotIn("Goblin 2", to["init"])
        self.assertEqual((to["current"], to["round"]), ("Lyra", 2))     # the pointer stays

    def test_remove_with_the_next_turn(self):
        to = self.push({"turn_order": {"remove": ["Goblin 2"], "current": "Hernio"}})
        self.assertEqual(to["order"], ["Goblin 1", "Lyra", "Hernio"])
        self.assertEqual(to["current"], "Hernio")

    def test_a_dying_pc_stays_a_dead_one_leaves(self):
        to = self.push({"players": [{"name": "Lyra", "hp": {"current": 0, "max": 12}}]})
        self.assertIn("Lyra", to["order"])                               # dying: still rolls death saves
        for _ in range(3):
            to = self.push({"players": [{"name": "Lyra", "_death_save": "failure"}]})
        self.assertNotIn("Lyra", to["order"])
        self.assertEqual(to["order"], ["Goblin 1", "Goblin 2", "Hernio"])


class TrackerRemovesAtZeroTests(unittest.TestCase):
    def setUp(self):
        import tracker
        self.tr = tracker
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (pathlib.Path(root) / "campaigns" / "prova").mkdir(parents=True)
        old = os.environ.get("DND_CAMPAIGN_ROOT")
        os.environ["DND_CAMPAIGN_ROOT"] = root
        self.addCleanup(lambda: os.environ.pop("DND_CAMPAIGN_ROOT", None) if old is None
                        else os.environ.__setitem__("DND_CAMPAIGN_ROOT", old))
        saved = (tracker._push_turn_remove, tracker._send_announce)
        def restore():
            (tracker._push_turn_remove, tracker._send_announce) = saved
        self.addCleanup(restore)
        tracker._send_announce = lambda msg: None
        self.removed = []
        tracker._push_turn_remove = self.removed.append

    def test_hit_to_zero_removes_it(self):
        with redirect_stdout(io.StringIO()):
            self.tr.cmd_hp("prova", "Goblin 1", 7, maximum=7)
            self.tr.cmd_hit("prova", "Goblin 1", 4)
        self.assertEqual(self.removed, [])
        out = io.StringIO()
        with redirect_stdout(out):
            self.tr.cmd_hit("prova", "Goblin 1", 5)
        self.assertEqual(self.removed, ["Goblin 1"])
        self.assertIn("out of the turn order", out.getvalue())

    def test_hp_set_to_zero_removes_it(self):
        with redirect_stdout(io.StringIO()):
            self.tr.cmd_hp("prova", "Bandito", 0, maximum=11)
        self.assertEqual(self.removed, ["Bandito"])


if __name__ == "__main__":
    unittest.main()
