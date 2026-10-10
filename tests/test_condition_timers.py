"""
test_condition_timers.py — a condition can end by itself.

`--stat-condition-add "Aldric:Poisoned:3r"` gives the condition a duration.
Rounds count down when the initiative tracker reaches the character (once per
turn, however many times that turn is sent); minutes and hours run on the
clock. When it is over the display removes the condition and says so.
tracker.py keeps the same count for a table with no display.

Run from repo root:
    python -m pytest tests/test_condition_timers.py -q
"""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"
HTML = DISPLAY / "templates" / "index.html"
NODE = shutil.which("node")
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(SKILL / "scripts"))

ORDER = ["Goblin 1", "Aldric", "Mira"]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load("dnd_display_app_condition_timers", DISPLAY / "dnd-display-app.py")
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        self.mod._current_stats = {"players": [{"name": "Aldric", "conditions": []},
                                               {"name": "Mira", "conditions": ["Prone"]}]}
        self.sent = []
        self._broadcast = self.mod._broadcast
        self.mod._broadcast = self.sent.append
        self.turn({"order": ORDER, "current": "Goblin 1", "round": 1})

    def tearDown(self):
        self.mod._broadcast = self._broadcast

    def post(self, payload):
        r = self.client.post("/stats", json=payload)
        self.assertIn(r.status_code, (200, 204))

    def turn(self, turn_order):
        self.post({"turn_order": turn_order})

    def player(self, name="Aldric"):
        return next(p for p in self.mod._current_stats["players"] if p["name"] == name)

    def add(self, cond, name="Aldric"):
        self.post({"players": [{"name": name, "_conditions_add": cond}]})

    def expired(self):
        return [m["effect_expired"] for m in self.sent if "effect_expired" in m]

    def rounds(self, n, name="Poisoned"):
        return {"name": name, "duration_type": "rounds", "duration_remaining": n}

    def test_a_plain_condition_has_no_timer_and_never_ends_by_itself(self):
        self.add("Poisoned")
        self.assertEqual(self.player()["conditions"], ["Poisoned"])
        self.assertEqual(self.player().get("condition_timers"), {})
        for who in ("Aldric", "Mira", "Goblin 1", "Aldric"):
            self.turn({"current": who})
        self.assertEqual(self.player()["conditions"], ["Poisoned"])

    def test_rounds_count_down_on_the_owners_turns_and_then_the_condition_goes(self):
        self.add(self.rounds(2))
        self.assertEqual(self.player()["condition_timers"], {"poisoned": {"duration_type": "rounds", "duration_remaining": 2}})
        self.turn({"current": "Aldric"})
        self.assertEqual(self.player()["condition_timers"]["poisoned"]["duration_remaining"], 1)
        self.turn({"current": "Mira"})                       # somebody else's turn: nothing moves
        self.assertEqual(self.player()["condition_timers"]["poisoned"]["duration_remaining"], 1)
        self.assertEqual(self.expired(), [])
        self.turn({"current": "Goblin 1", "round": 2})
        self.turn({"current": "Aldric"})
        self.assertEqual(self.player()["conditions"], [])
        self.assertEqual(self.player()["condition_timers"], {})
        self.assertEqual(self.expired(), [{"owner": "Aldric", "name": "Poisoned",
                                           "was_concentration": False, "condition": True}])

    def test_sending_the_same_turn_again_is_not_another_round(self):
        self.add(self.rounds(3))
        self.turn({"current": "Aldric"})
        self.turn({"current": "Aldric"})
        self.turn({"order": ORDER + ["Orc"], "current": "Aldric", "round": 1})   # a creature joins mid-turn
        self.assertEqual(self.player()["condition_timers"]["poisoned"]["duration_remaining"], 2)

    def test_timed_effects_get_the_same_protection(self):
        self.post({"players": [{"name": "Aldric", "_effect_start":
                                {"name": "Bless", "duration_type": "rounds", "duration_remaining": 3, "concentration": False}}]})
        self.turn({"current": "Aldric"})
        self.turn({"current": "Aldric"})
        self.assertEqual(self.player()["effects"][0]["duration_remaining"], 2)

    def test_the_only_combatant_still_counts_each_round(self):
        self.mod._current_stats["turn_order"] = None
        self.turn({"order": ["Aldric"], "current": "Aldric", "round": 1})
        self.add(self.rounds(2))
        self.turn({"current": "Aldric", "round": 2})
        self.assertEqual(self.player()["condition_timers"]["poisoned"]["duration_remaining"], 1)
        self.turn({"current": "Aldric", "round": 3})
        self.assertEqual(self.player()["conditions"], [])

    def test_removing_or_replacing_conditions_drops_their_timers(self):
        self.add(self.rounds(5))
        self.add(self.rounds(5, "Frightened"))
        self.post({"players": [{"name": "Aldric", "_conditions_remove": "poisoned"}]})
        self.assertEqual(list(self.player()["condition_timers"]), ["frightened"])
        self.post({"players": [{"name": "Aldric", "conditions": ["Prone"]}]})
        self.assertEqual(self.player()["condition_timers"], {})
        self.assertEqual(self.player()["conditions"], ["Prone"])

    def test_adding_it_again_resets_the_count_and_a_plain_add_makes_it_open_ended(self):
        self.add(self.rounds(1))
        self.add(self.rounds(4))
        self.assertEqual(self.player()["conditions"], ["Poisoned"])
        self.assertEqual(self.player()["condition_timers"]["poisoned"]["duration_remaining"], 4)
        self.add("poisoned")
        self.assertEqual(self.player()["conditions"], ["Poisoned"])
        self.assertEqual(self.player()["condition_timers"], {})

    def test_a_condition_on_the_clock_ends_when_its_time_is_up_and_not_before(self):
        timer = {"name": "Charmed", "duration_type": "minutes", "duration_seconds": 600, "started_at": time.time()}
        self.add(timer, "Mira")
        r = self.client.post("/effects/expire", json={"owner": "Mira", "name": "Charmed", "condition": True})
        self.assertEqual(r.status_code, 204)
        self.assertIn("Charmed", self.player("Mira")["conditions"])          # a hasty browser ends nothing
        self.player("Mira")["condition_timers"]["charmed"]["started_at"] = time.time() - 601
        self.client.post("/effects/expire", json={"owner": "Mira", "name": "Charmed", "condition": True})
        self.assertEqual(self.player("Mira")["conditions"], ["Prone"])
        self.assertEqual(self.player("Mira")["condition_timers"], {})
        self.assertEqual(self.expired()[-1], {"owner": "Mira", "name": "Charmed",
                                              "was_concentration": False, "condition": True})
        # turns do not touch a condition that runs on the clock
        self.add(timer, "Mira")
        self.turn({"current": "Mira"})
        self.assertIn("Charmed", self.player("Mira")["conditions"])

    def test_the_other_players_see_how_long_it_lasts_too(self):
        self.add(self.rounds(3))
        self.player()["sheet"] = {"inventory": ["Chiave"]}
        seen = self.mod._for_viewer({"stats": self.mod._current_stats}, "mira")["stats"]["players"][0]
        self.assertEqual(seen["condition_timers"], {"poisoned": {"duration_type": "rounds", "duration_remaining": 3}})
        self.assertNotIn("sheet", seen)

    def test_a_malformed_duration_is_just_a_condition(self):
        for bad in ({"name": "Stunned", "duration_type": "rounds", "duration_remaining": 0},
                    {"name": "Stunned", "duration_type": "rounds", "duration_remaining": "x"},
                    {"name": "Stunned", "duration_type": "years", "duration_remaining": 2},
                    {"name": "Stunned"}):
            self.mod._current_stats["players"][0] = {"name": "Aldric", "conditions": []}
            self.add(bad)
            self.assertEqual(self.player()["conditions"], ["Stunned"], bad)
            self.assertEqual(self.player()["condition_timers"], {}, bad)
        self.add({"name": "  ", "duration_type": "rounds", "duration_remaining": 2})
        self.assertEqual(self.player()["conditions"], ["Stunned"])


class CommandTests(unittest.TestCase):
    def test_the_duration_is_the_last_part_of_the_flag(self):
        for script in ("send.py", "push_stats.py"):
            mod = _load("cond_cli_" + script[:-3], DISPLAY / script)
            self.assertEqual(mod._condition_value("Poisoned"), "Poisoned")
            self.assertEqual(mod._condition_value("Poisoned:3r"),
                             {"name": "Poisoned", "duration_type": "rounds", "duration_remaining": 3})
            minutes = mod._condition_value("Exhaustion (2):10M")
            self.assertEqual((minutes["name"], minutes["duration_type"], minutes["duration_seconds"]),
                             ("Exhaustion (2)", "minutes", 600))
            self.assertAlmostEqual(minutes["started_at"], time.time(), delta=5)
            self.assertEqual(mod._condition_value("Charmed:1h")["duration_seconds"], 3600)
            for plain in ("Odd:name", "Poisoned:0r", "Poisoned:3x", ":3r"):
                self.assertEqual(mod._condition_value(plain), plain)

    def test_send_py_bundles_a_timed_condition_with_the_narration(self):
        send = _load("cond_send", DISPLAY / "send.py")

        class Args:
            stat_hp = stat_temp_hp = stat_slot_use = stat_slot_restore = stat_condition_remove = None
            stat_concentrate = stat_inventory_add = stat_inventory_remove = effect_start = effect_end = None
            stat_condition_add = ["Aldric:Poisoned:3r", "Mira:Prone"]
        payload = send._build_stats_payload(Args)
        self.assertEqual(payload["players"], [
            {"name": "Aldric", "_conditions_add": {"name": "Poisoned", "duration_type": "rounds", "duration_remaining": 3}},
            {"name": "Mira", "_conditions_add": "Prone"}])


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("DND_CAMPAIGN_ROOT")
        os.environ["DND_CAMPAIGN_ROOT"] = self._tmp.name
        (pathlib.Path(self._tmp.name) / "campaigns" / "prova").mkdir(parents=True)
        self.tr = _load("tracker_condition_timers", SKILL / "scripts" / "tracker.py")
        self.pushes = []
        self.tr._push_conditions = lambda name, conds: self.pushes.append((name, list(conds)))
        self.tr._push_timed_condition = lambda name, cond, dur: self.pushes.append((name, cond, dur))
        self.tr._send_announce = lambda msg: None

    def tearDown(self):
        if self._old is None:
            os.environ.pop("DND_CAMPAIGN_ROOT", None)
        else:
            os.environ["DND_CAMPAIGN_ROOT"] = self._old
        self._tmp.cleanup()

    def run_tr(self, *argv):
        out = io.StringIO()
        old = sys.argv
        sys.argv = ["tracker.py", "-c", "prova", *argv]
        try:
            with contextlib.redirect_stdout(out):
                self.tr.main()
        finally:
            sys.argv = old
        return out.getvalue()

    def ent(self):
        return self.tr._load("prova")["aldric"]

    def test_a_timed_condition_counts_down_with_the_ticks_and_ends(self):
        self.assertIn("poisoned · 2 rnd", self.run_tr("condition", "add", "Aldric", "Poisoned", "2r"))
        self.assertEqual(self.pushes[-1], ("Aldric", "poisoned", "2r"))
        self.run_tr("condition", "add", "Aldric", "prone")
        self.assertIn("Poisoned · 2 rnd, Prone", self.run_tr("status", "Aldric"))
        self.assertIn("Poisoned · 1 rnd", self.run_tr("effect", "tick", "Aldric"))
        out = self.run_tr("effect", "tick", "Aldric")
        self.assertIn("Poisoned ENDS", out)
        self.assertEqual(self.ent()["conditions"], ["prone"])
        self.assertEqual(self.ent()["condition_timers"], {})
        self.assertEqual(self.pushes[-1], ("Aldric", ["prone"]))
        self.assertEqual(self.run_tr("effect", "tick", "Aldric"), "")

    def test_removing_and_clearing_forget_the_timer(self):
        self.run_tr("condition", "add", "Aldric", "poisoned", "5r")
        self.run_tr("condition", "remove", "Aldric", "poisoned")
        self.assertEqual(self.ent()["condition_timers"], {})
        self.run_tr("condition", "add", "Aldric", "stunned", "1m")
        self.assertEqual(self.ent()["condition_timers"]["stunned"]["duration_seconds"], 60)
        self.run_tr("clear")
        self.assertEqual((self.ent()["conditions"], self.ent()["condition_timers"]), ([], {}))

    def test_a_bad_duration_adds_nothing(self):
        for bad in ("soon", "indef", "3"):
            self.assertIn("bad duration", self.run_tr("condition", "add", "Aldric", "poisoned", bad))
        self.assertEqual(self.tr._load("prova"), {})

    def test_an_entity_saved_before_timers_existed_still_loads(self):
        self.tr._save("prova", {"aldric": {"name": "Aldric", "conditions": ["prone"], "concentration": None,
                                           "death_saves": {"successes": 0, "failures": 0, "stable": False}}})
        self.assertIn("Prone", self.run_tr("status"))
        self.run_tr("condition", "add", "Aldric", "poisoned", "1r")
        self.assertIn("Poisoned ENDS", self.run_tr("effect", "tick", "Aldric"))


_SLICE = re.compile(r"(// A condition's name in the display language.*?)\n// ── end timed conditions ──", re.S)


class PageTests(unittest.TestCase):
    def test_the_pill_says_what_is_left(self):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the timed-condition block is no longer where this test slices it")
        cases = [None, {"duration_type": "rounds", "duration_remaining": 3},
                 {"duration_type": "rounds", "duration_remaining": 1},
                 {"duration_type": "minutes", "duration_seconds": 600, "started_at": 1000},
                 {"duration_type": "hours", "duration_seconds": 7200, "started_at": 1000},
                 {"duration_type": "indefinite"}]
        script = ("const _t = (k, v, fb) => k === 'cond.poisoned' ? 'Avvelenato' : (fb !== undefined ? fb : k);\n"
                  "function _fmtDuration(ms) { return Math.floor(ms / 1000) + 's'; }\n" + m.group(1)
                  + "\nconsole.log(JSON.stringify({labels: ['Poisoned', 'Exhaustion (2)', 'Cursed'].map(_condLabel),"
                  " left: %s.map(t => _condTimeLeft(t, 1000 * 1000 + 590 * 1000))}));\n" % json.dumps(cases))
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            got = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        self.assertEqual(got["labels"], ["Avvelenato", "Exhaustion (2)", "Cursed"])
        self.assertEqual(got["left"], [
            None,
            {"text": "3 rnd", "warn": False, "expireAt": 0},
            {"text": "1 rnd", "warn": True, "expireAt": 0},
            {"text": "10s", "warn": True, "expireAt": 1600000},
            {"text": "6610s", "warn": False, "expireAt": 8200000},
            None])

    def test_the_page_draws_counts_down_and_announces(self):
        src = HTML.read_text(encoding="utf-8")
        for needle in ("_condTimeLeft((p.condition_timers || {})[c.toLowerCase()])",
                       ".sb-condition-pill[data-duration-type=\"time\"]",
                       "condition: true}",
                       "ev.condition ? _condLabel(ev.name) : ev.name"):
            self.assertIn(needle, src)


if __name__ == "__main__":
    unittest.main()
