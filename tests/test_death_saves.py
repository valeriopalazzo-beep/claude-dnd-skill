"""
test_death_saves.py — death saving throws on the dying player's phone.

A PC at 0 HP gets a death-save track that the whole table sees. The player
rolls on their own turn (the DM's screen can roll for them); the server rolls
the d20 and applies the rules: 10+ a success, below a failure, a natural 1 two
failures, a natural 20 back up with 1 HP; three successes stable, three
failures dead. The DM adds failures for damage taken while down.

Run from repo root:
    python -m pytest tests/test_death_saves.py -q
"""
import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"
HTML = DISPLAY / "templates" / "index.html"
NODE = shutil.which("node")
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(SKILL / "scripts"))

ORDER = ["Goblin", "Aldric", "Mira"]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load("dnd_display_app_death_saves", DISPLAY / "dnd-display-app.py")
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        self.mod._current_stats = {"players": [{"name": "Aldric", "hp": {"current": 9, "max": 20}},
                                               {"name": "Mira", "hp": {"current": 14, "max": 14}}]}
        self.sent = []
        self._saved = (self.mod._broadcast, self.mod._persist_log, self.mod._persist_tail,
                       self.mod._persist_stats, self.mod._is_other_character, self.mod.secrets.randbelow)
        self.mod._broadcast = self.sent.append
        self.mod._persist_log = self.mod._persist_tail = self.mod._persist_stats = lambda: None
        self.dice = []

    def tearDown(self):
        (self.mod._broadcast, self.mod._persist_log, self.mod._persist_tail,
         self.mod._persist_stats, self.mod._is_other_character, self.mod.secrets.randbelow) = self._saved

    def post(self, payload):
        self.assertIn(self.client.post("/stats", json=payload).status_code, (200, 204))

    def hp(self, name, cur):
        self.post({"players": [{"name": name, "hp": {"current": cur}}]})

    def step(self, name, action):
        self.post({"players": [{"name": name, "_death_save": action}]})

    def player(self, name="Aldric"):
        return next(p for p in self.mod._current_stats["players"] if p["name"] == name)

    def track(self, name="Aldric"):
        ds = self.player(name).get("death_saves")
        return None if ds is None else (ds["successes"], ds["failures"], ds["stable"], ds["dead"])

    def roll(self, name, d20):
        self.mod.secrets.randbelow = lambda n: d20 - 1
        r = self.client.post("/death-save/roll", json={"character": name})
        return r.status_code, r.get_json(silent=True)

    def test_dropping_to_zero_starts_the_track_and_healing_ends_it(self):
        self.assertIsNone(self.track())
        self.hp("Aldric", 0)
        self.assertEqual(self.track(), (0, 0, False, False))
        self.assertIsNone(self.track("Mira"))
        self.step("Aldric", "failure")
        self.hp("Aldric", 0)                                  # sending 0 again does not start over
        self.assertEqual(self.track(), (0, 1, False, False))
        self.hp("Aldric", 4)
        self.assertIsNone(self.track())

    def test_the_dms_steps_follow_the_rules(self):
        self.hp("Aldric", 0)
        self.step("Aldric", "success")
        self.step("Aldric", "failure")
        self.assertEqual(self.track(), (1, 1, False, False))
        self.step("Aldric", "crit")                           # a critical hit while down: two failures
        self.assertEqual(self.track(), (1, 3, False, True))
        self.step("Aldric", "success")                        # the dead make no more saves
        self.assertEqual(self.track(), (1, 3, False, True))
        self.hp("Aldric", 5)                                  # nor does healing raise them
        self.assertEqual(self.track(), (1, 3, False, True))
        self.step("Aldric", "reset")
        self.assertIsNone(self.track())

    def test_three_successes_or_a_helping_hand_stabilise_and_new_damage_starts_over(self):
        self.hp("Aldric", 0)
        self.step("Aldric", "failure")
        for _ in range(3):
            self.step("Aldric", "success")
        self.assertEqual(self.track(), (0, 0, True, False))
        self.step("Aldric", "failure")                        # hit again while stable
        self.assertEqual(self.track(), (0, 1, False, False))
        self.step("Aldric", "stable")                         # Spare the Dying
        self.assertEqual(self.track(), (0, 0, True, False))
        self.step("Aldric", "nonsense")
        self.assertEqual(self.track(), (0, 0, True, False))

    def test_a_roll_is_ten_or_more_to_succeed(self):
        self.hp("Aldric", 0)
        code, res = self.roll("aldric", 10)
        self.assertEqual((code, res["outcome"], res["roll"], res["successes"], res["failures"]), (200, "success", 10, 1, 0))
        code, res = self.roll("Aldric", 9)
        self.assertEqual((res["outcome"], res["successes"], res["failures"]), ("failure", 1, 1))
        self.assertEqual(self.sent[-1], {"death_save": res})
        self.assertEqual(self.sent[-2]["stats"]["players"][0]["death_saves"]["failures"], 1)
        self.assertEqual(self.sent[-3], {"text": "☠ Aldric: d20 = 9 · ✓○○ ✗○○", "dice": True})
        self.assertEqual(self.mod._text_log[-1]["text"], "☠ Aldric: d20 = 9 · ✓○○ ✗○○")

    def test_a_natural_one_is_two_failures_and_a_natural_twenty_gets_up(self):
        self.hp("Aldric", 0)
        _, res = self.roll("Aldric", 1)
        self.assertEqual((res["outcome"], res["failures"]), ("fail2", 2))
        _, res = self.roll("Aldric", 20)
        self.assertEqual(res["outcome"], "up")
        self.assertEqual(self.player()["hp"]["current"], 1)
        self.assertIsNone(self.track())
        self.assertTrue(self.sent[-3]["text"].endswith("d20 = 20 · → 1 HP"))

    def test_the_third_mark_ends_it_either_way(self):
        self.hp("Aldric", 0)
        self.roll("Aldric", 15)
        self.roll("Aldric", 15)
        _, res = self.roll("Aldric", 11)
        self.assertEqual(res["outcome"], "stable")
        self.assertEqual(self.track(), (0, 0, True, False))
        self.assertEqual(self.sent[-3]["text"], "☠ Aldric: d20 = 11 · ✓✓✓ ○○○ ⚕")
        self.assertEqual(self.roll("Aldric", 15)[0], 409)      # stable: nothing to roll

        self.hp("Mira", 0)
        self.roll("Mira", 2)
        self.roll("Mira", 1)
        self.assertEqual(self.track("Mira"), (0, 3, False, True))
        self.assertEqual(self.sent[-3]["text"], "☠ Mira: d20 = 1 · ○○○ ✗✗✗ ☠")
        self.assertEqual(self.roll("Mira", 20)[0], 409)

    def test_in_a_fight_it_is_one_roll_on_your_own_turn(self):
        self.hp("Aldric", 0)
        self.post({"turn_order": {"order": ORDER, "current": "Goblin", "round": 1}})
        code, err = self.roll("Aldric", 15)
        self.assertEqual((code, err["error"]), (409, "not your turn"))
        self.post({"turn_order": {"current": "Aldric"}})
        self.assertEqual(self.roll("Aldric", 15)[0], 200)
        code, err = self.roll("Aldric", 15)
        self.assertEqual((code, err["error"]), (409, "already rolled this turn"))
        self.assertEqual(self.track(), (1, 0, False, False))
        self.post({"turn_order": {"current": "Goblin", "round": 2}})
        self.post({"turn_order": {"current": "Aldric"}})
        self.assertEqual(self.roll("Aldric", 15)[0], 200)
        self.assertEqual(self.track(), (2, 0, False, False))

    def test_only_the_dying_roll_and_only_for_themselves(self):
        self.assertEqual(self.roll("Aldric", 15)[0], 409)       # on their feet
        self.assertEqual(self.roll("Nessuno", 15)[0], 409)
        self.assertEqual(self.roll("", 15)[0], 400)
        self.hp("Aldric", 0)
        self.mod._is_other_character = lambda name: name.lower() != "mira"
        self.assertEqual(self.roll("Aldric", 15)[0], 403)
        self.assertEqual(self.track(), (0, 0, False, False))

    def test_the_table_sees_the_track_on_a_friends_card(self):
        self.hp("Aldric", 0)
        self.step("Aldric", "failure")
        self.player()["sheet"] = {"inventory": ["Chiave"]}
        seen = self.mod._for_viewer({"stats": self.mod._current_stats}, "mira")["stats"]["players"][0]
        self.assertEqual(seen["death_saves"]["failures"], 1)
        self.assertNotIn("sheet", seen)
        self.assertNotIn("death_save_roll", self.mod._DM_ENDPOINTS)


class CommandTests(unittest.TestCase):
    def test_send_py_carries_a_step_with_the_narration(self):
        send = _load("death_send", DISPLAY / "send.py")

        class Args:
            stat_hp = stat_temp_hp = stat_slot_use = stat_slot_restore = stat_condition_add = None
            stat_condition_remove = stat_concentrate = stat_inventory_add = stat_inventory_remove = None
            effect_start = effect_end = None
            stat_death_save = ["Aldric:failure", "Mira Ombra:CRIT", "Lio:dance", ":stable"]
        self.assertEqual(send._build_stats_payload(Args)["players"],
                         [{"name": "Aldric", "_death_save": "failure"}, {"name": "Mira Ombra", "_death_save": "crit"}])

    def test_the_tracker_mirrors_its_steps_on_the_display(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        import os
        old = os.environ.get("DND_CAMPAIGN_ROOT")
        os.environ["DND_CAMPAIGN_ROOT"] = tmp.name
        self.addCleanup(lambda: os.environ.pop("DND_CAMPAIGN_ROOT", None) if old is None
                        else os.environ.__setitem__("DND_CAMPAIGN_ROOT", old))
        (pathlib.Path(tmp.name) / "campaigns" / "prova").mkdir(parents=True)
        tr = _load("tracker_death_saves", SKILL / "scripts" / "tracker.py")
        pushed = []
        tr._push_death_save = lambda name, action: pushed.append((name, action))
        tr._send_announce = lambda msg: None
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            for action in ("failure", "success", "stable", "reset"):
                tr.cmd_saves("prova", "Aldric", action)
        self.assertEqual(pushed, [("Aldric", "failure"), ("Aldric", "success"), ("Aldric", "stable"), ("Aldric", "reset")])


_SLICE = re.compile(r"(function _deathState\(p, me, turn\).*?)\n// ── end death-save decision ──", re.S)


class PageTests(unittest.TestCase):
    def state(self, p, me, turn):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the death-save block is no longer where this test slices it")
        script = m.group(1) + "\nconsole.log(JSON.stringify([_deathState(%s, %s, %s), _deathPips(2, 'x'), _deathPips(7, 'x')]));\n" % (
            json.dumps(p), json.dumps(me), json.dumps(turn))
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            return json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink(missing_ok=True)

    def dying(self, **ds):
        return {"name": "Aldric", "death_saves": {"successes": 1, "failures": 2, "stable": False, "dead": False, **ds}}

    def turn(self, current, rnd=1):
        return {"order": ORDER, "current": current, "round": rnd}

    def test_a_character_on_their_feet_has_no_track(self):
        self.assertIsNone(self.state({"name": "Aldric"}, "Aldric", None)[0])

    def test_outside_a_fight_the_dying_player_may_roll_any_time(self):
        d, two, capped = self.state(self.dying(), "aldric", None)
        self.assertEqual((d["successes"], d["failures"], d["mine"], d["canRoll"]), (1, 2, True, True))
        self.assertEqual((two, capped), ("xx○", "xxx"))

    def test_in_a_fight_only_on_their_turn_and_only_once(self):
        self.assertFalse(self.state(self.dying(), "Aldric", self.turn("Goblin"))[0]["canRoll"])
        self.assertTrue(self.state(self.dying(), "Aldric", self.turn("Aldric"))[0]["canRoll"])
        self.assertFalse(self.state(self.dying(rolled="1:aldric"), "Aldric", self.turn("Aldric"))[0]["canRoll"])
        self.assertTrue(self.state(self.dying(rolled="1:aldric"), "Aldric", self.turn("Aldric", 2))[0]["canRoll"])

    def test_another_players_phone_never_rolls_but_the_table_screen_does(self):
        self.assertFalse(self.state(self.dying(), "Mira", self.turn("Aldric"))[0]["canRoll"])
        d = self.state(self.dying(), "", self.turn("Aldric"))[0]
        self.assertEqual((d["mine"], d["canRoll"]), (False, True))

    def test_the_stable_and_the_dead_do_not_roll(self):
        for flag in ("stable", "dead"):
            d = self.state(self.dying(**{flag: True}), "Aldric", None)[0]
            self.assertTrue(d[flag])
            self.assertFalse(d["canRoll"])

    def test_the_page_draws_the_track_and_forgets_it_when_the_server_does(self):
        src = HTML.read_text(encoding="utf-8")
        for needle in ("const _WHOLE_KEYS = ['condition_timers', 'death_saves'];", 'id="death-card"',
                       "fetch('/death-save/roll'", "window._onDeathSaves()", "payload.death_save &&",
                       "row.dataset.role = 'death-saves';"):
            self.assertIn(needle, src)

    def test_strings_exist_in_every_language(self):
        keys = ["death.title", "death.roll", "death.rollFor", "death.stable", "death.dead"]
        keys += ["death.res." + k for k in ("success", "failure", "fail2", "up", "stable", "dead")]
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
