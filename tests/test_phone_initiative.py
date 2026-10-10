"""
test_phone_initiative.py — the players roll their own initiative.

`combat.py init '<JSON>' --phones` opens a roll on the display. Every PC gets a
button (their own phone, or the table screen for a player without one); the
monsters are rolled at once and kept hidden; when all PCs are in, or the time
is up, the server settles the order and shows it. The dice are the server's.

Run from repo root:
    python -m pytest tests/test_phone_initiative.py -q
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

FIGHT = [{"name": "Aldric", "type": "pc", "dex_mod": 2}, {"name": "Mira", "type": "pc", "dex_mod": 3},
         {"name": "Goblin 1", "type": "npc", "dex_mod": 2}, {"name": "Goblin 2", "type": "npc", "dex_mod": 2}]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load("dnd_display_app_initiative", DISPLAY / "dnd-display-app.py")
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        self.mod._initiative = None
        self.sent = []
        self._saved = (self.mod._broadcast, self.mod._persist_log, self.mod._persist_tail, self.mod._is_other_character)
        self.mod._broadcast = self.sent.append
        self.mod._persist_log = lambda: None
        self.mod._persist_tail = lambda: None
        self.timers = []
        self._timer = self.mod.threading.Timer
        outer = self

        class NoTimer:                                   # the deadline is fired by hand in these tests
            def __init__(self, seconds, fn, args=()):
                outer.timers.append((seconds, fn, args))
                self.daemon = True

            def start(self):
                pass
        self.mod.threading.Timer = NoTimer

    def tearDown(self):
        (self.mod._broadcast, self.mod._persist_log, self.mod._persist_tail, self.mod._is_other_character) = self._saved
        self.mod.threading.Timer = self._timer
        self.mod._initiative = None

    def start(self, combatants=FIGHT, **extra):
        r = self.client.post("/initiative", json={"combatants": combatants, **extra})
        return r.status_code, r.get_json()

    def roll(self, name):
        r = self.client.post("/initiative/roll", json={"character": name})
        return r.status_code, r.get_json(silent=True)

    def state(self):
        return self.client.get("/initiative").get_json()

    def test_opening_shows_the_pcs_waiting_and_hides_the_monsters(self):
        code, state = self.start(timeout=45)
        self.assertEqual(code, 200)
        self.assertFalse(state["done"])
        self.assertIsNone(state["order"])
        self.assertEqual(state["entries"], [{"name": "Aldric", "type": "pc", "modifier": 2, "rolled": False},
                                            {"name": "Mira", "type": "pc", "modifier": 3, "rolled": False}])
        self.assertNotIn("Goblin", json.dumps(self.sent))
        self.assertEqual(self.sent[-1]["initiative"]["id"], state["id"])
        self.assertEqual(self.timers[0][0], 45)

    def test_each_roll_is_the_servers_d20_plus_the_modifier_and_everyone_sees_it(self):
        self.start()
        code, mine = self.roll("aldric")
        self.assertEqual(code, 200)
        self.assertEqual(mine["name"], "Aldric")
        self.assertTrue(1 <= mine["roll"] <= 20)
        self.assertEqual(mine["total"], mine["roll"] + 2)
        shown = self.sent[-1]["initiative"]
        self.assertFalse(shown["done"])
        self.assertEqual([(e["name"], e["rolled"]) for e in shown["entries"]], [("Aldric", True), ("Mira", False)])
        self.assertEqual(shown["entries"][0]["total"], mine["total"])
        self.assertEqual(self.roll("Aldric")[0], 409)        # once each

    def test_when_the_last_pc_rolls_the_order_is_settled_and_told(self):
        self.start()
        self.roll("Aldric")
        self.roll("Mira")
        state = self.state()
        self.assertTrue(state["done"])
        self.assertEqual(sorted(state["order"]), sorted(c["name"] for c in FIGHT))
        totals = {e["name"]: e["total"] for e in state["entries"]}
        self.assertEqual(len(totals), 4)
        self.assertEqual([totals[n] for n in state["order"]], sorted(totals.values(), reverse=True))
        self.assertFalse(any(e["auto"] for e in state["entries"]))
        line = next(m["text"] for m in self.sent if m.get("dice"))
        self.assertTrue(line.startswith("⚔ "))
        self.assertEqual(line[2:].split(" · "), [f"{n} {totals[n]}" for n in state["order"]])
        self.assertEqual(self.mod._text_log[-1]["text"], line)
        self.assertEqual(self.sent[-1]["initiative"]["order"], state["order"])
        self.assertEqual(self.roll("Mira")[0], 409)          # nothing is open any more

    def test_when_the_time_is_up_the_missing_are_rolled_for(self):
        _, opened = self.start()
        self.roll("Mira")
        _, fn, args = self.timers[0]
        fn(*args)
        state = self.state()
        self.assertTrue(state["done"])
        auto = {e["name"]: e["auto"] for e in state["entries"]}
        self.assertEqual(auto, {"Aldric": True, "Mira": False, "Goblin 1": False, "Goblin 2": False})
        announced = len(self.sent)
        fn(*args)                                            # a late timer changes nothing
        self.assertEqual(len(self.sent), announced)

    def test_a_timer_from_a_replaced_roll_does_not_close_the_new_one(self):
        self.start()
        old_timer = self.timers[0]
        _, second = self.start()
        old_timer[1](*old_timer[2])
        self.assertFalse(self.state()["done"])
        self.assertEqual(self.state()["id"], second["id"])

    def test_ties_go_to_the_better_modifier_then_to_the_pc(self):
        state = {"id": "x", "deadline": 0, "done": False, "order": None, "entries": [
            {"name": "Goblin", "type": "npc", "modifier": 2, "roll": 10, "total": 12, "tiebreak": 9},
            {"name": "Aldric", "type": "pc", "modifier": 2, "roll": 10, "total": 12, "tiebreak": 1},
            {"name": "Mira", "type": "pc", "modifier": 3, "roll": 9, "total": 12, "tiebreak": 0},
            {"name": "Ogre", "type": "npc", "modifier": -1, "roll": 20, "total": 19, "tiebreak": 0}]}
        self.mod._initiative_settle(state)
        self.assertEqual(state["order"], ["Ogre", "Mira", "Aldric", "Goblin"])

    def test_a_player_rolls_only_for_their_own_character(self):
        self.start()
        self.mod._is_other_character = lambda name: name.lower() != "mira"
        self.assertEqual(self.roll("Aldric")[0], 403)
        self.assertEqual(self.roll("Mira")[0], 200)
        self.assertEqual([(e["name"], e["rolled"]) for e in self.state()["entries"]],
                         [("Aldric", False), ("Mira", True)])

    def test_only_a_pc_in_the_fight_can_roll(self):
        self.assertEqual(self.roll("Aldric")[0], 409)        # nothing open
        self.start()
        self.assertEqual(self.roll("Lio")[0], 404)
        self.assertEqual(self.roll("Goblin 1")[0], 404)
        self.assertEqual(self.roll("")[0], 400)

    def test_a_fight_with_no_pc_to_wait_for_is_settled_at_once(self):
        code, state = self.start([{"name": "Wolf", "type": "npc", "dex_mod": 2}, {"name": "Bear", "dex_mod": 0}])
        self.assertEqual(code, 200)
        self.assertTrue(state["done"])
        self.assertEqual(sorted(state["order"]), ["Bear", "Wolf"])
        self.assertEqual(self.timers, [])

    def test_init_mod_replaces_dex_and_bad_input_is_tolerated(self):
        _, state = self.start([{"name": "Aldric", "type": "pc", "dex_mod": 2, "init_mod": 7},
                              {"name": "aldric", "type": "pc"}, {"name": ""}, "nonsense",
                              {"name": "Mira", "type": "PC", "dex_mod": "x"}], timeout="soon")
        self.assertEqual([(e["name"], e["modifier"]) for e in state["entries"]], [("Aldric", 7), ("Mira", 0)])
        self.assertEqual(self.timers[0][0], 60)
        self.assertEqual(self.start([])[0], 400)
        self.assertEqual(self.start([{"name": " "}])[0], 400)

    def test_cancel_closes_it_for_everyone(self):
        self.start()
        self.assertEqual(self.client.delete("/initiative").status_code, 204)
        self.assertEqual(self.state(), {})
        self.assertEqual(self.sent[-1], {"initiative": None})
        self.assertEqual(self.roll("Aldric")[0], 409)

    def test_opening_and_closing_are_the_dms_rolling_is_not(self):
        self.assertIn("initiative_open", self.mod._DM_ENDPOINTS)
        self.assertIn("initiative_cancel", self.mod._DM_ENDPOINTS)
        self.assertNotIn("initiative_roll", self.mod._DM_ENDPOINTS)
        self.assertNotIn("initiative_state", self.mod._DM_ENDPOINTS)


class CombatScriptTests(unittest.TestCase):
    def setUp(self):
        self.combat = _load("combat_phone_initiative", SKILL / "scripts" / "combat.py")
        self.fight = [{"name": "Aldric", "dex_mod": 2, "hp": 20, "ac": 16, "type": "pc"},
                      {"name": "Mira", "dex_mod": 3, "init_mod": 5, "hp": 14, "ac": 14, "type": "pc"},
                      {"name": "Goblin", "dex_mod": 2, "hp": 7, "ac": 15, "type": "npc"}]

    def test_the_displays_order_and_rolls_become_the_combat_state(self):
        asked = []

        def collect(combatants, seconds):
            asked.append((combatants, seconds))
            return [{"name": "Goblin", "roll": 18, "total": 20, "auto": False},
                    {"name": "Mira", "roll": 9, "total": 14, "auto": False},
                    {"name": "Aldric", "roll": 3, "total": 5, "auto": True}]
        ordered = self.combat.phone_initiative(self.fight, 30, collect)
        self.assertEqual(asked, [([{"name": "Aldric", "type": "pc", "init_mod": 2},
                                   {"name": "Mira", "type": "pc", "init_mod": 5},
                                   {"name": "Goblin", "type": "npc", "init_mod": 2}], 30)])
        self.assertEqual([c["name"] for c in ordered], ["Goblin", "Mira", "Aldric"])
        self.assertEqual([(c["initiative_roll"], c["initiative"], c["initiative_auto"]) for c in ordered],
                         [(18, 20, False), (9, 14, False), (3, 5, True)])
        self.assertEqual(ordered[2]["hp"], 20)
        self.assertEqual(ordered[0]["conditions"], [])

    def test_no_display_or_a_half_answer_means_roll_here(self):
        self.assertIsNone(self.combat.phone_initiative(self.fight, 30, lambda c, s: None))
        self.assertIsNone(self.combat.phone_initiative(
            self.fight, 30, lambda c, s: [{"name": "Goblin", "roll": 1, "total": 3}]))

    def test_the_client_waits_for_the_order_and_gives_up_when_the_roll_disappears(self):
        client = _load("initiative_client", DISPLAY / "initiative.py")
        open_state = {"id": "abc", "done": False, "entries": [{"name": "Aldric", "type": "pc", "rolled": False}]}
        done_state = {"id": "abc", "done": True, "order": ["Goblin", "Aldric"], "entries": [
            {"name": "Aldric", "type": "pc", "rolled": True, "roll": 4, "total": 6, "modifier": 2, "auto": False},
            {"name": "Goblin", "type": "npc", "rolled": True, "roll": 12, "total": 14, "modifier": 2, "auto": False}]}
        said = []

        def fake(answers):
            answers = list(answers)
            return lambda path, body=None, method=None: answers.pop(0)
        client._request = fake([(200, open_state), (200, open_state), (200, done_state)])
        got = client.collect(FIGHT, 5, poll=0, say=said.append)
        self.assertEqual([e["name"] for e in got], ["Goblin", "Aldric"])
        self.assertEqual(said, ["initiative: waiting for Aldric"])
        client._request = fake([(0, {})])                                   # no display
        self.assertIsNone(client.collect(FIGHT, 5, poll=0, say=said.append))
        client._request = fake([(404, {})])                                 # a display from before this feature
        self.assertIsNone(client.collect(FIGHT, 5, poll=0, say=said.append))
        client._request = fake([(200, open_state), (200, {})])              # cancelled meanwhile
        self.assertIsNone(client.collect(FIGHT, 5, poll=0, say=said.append))
        client._request = fake([(200, open_state), (200, dict(done_state, id="other"))])   # replaced
        self.assertIsNone(client.collect(FIGHT, 5, poll=0, say=said.append))


_SLICE = re.compile(r"(function _initRows\(state, me\).*?)\n// ── end initiative decision ──", re.S)


class PageTests(unittest.TestCase):
    def rows(self, state, me):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the initiative block is no longer where this test slices it")
        script = m.group(1) + "\nconsole.log(JSON.stringify(_initRows(%s, %s)));\n" % (json.dumps(state), json.dumps(me))
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            return json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink(missing_ok=True)

    OPEN = {"id": "a", "done": False, "order": None, "entries": [
        {"name": "Aldric", "type": "pc", "modifier": 2, "rolled": False},
        {"name": "Mira", "type": "pc", "modifier": 3, "rolled": True, "roll": 10, "total": 13, "auto": False}]}
    DONE = {"id": "a", "done": True, "order": ["Goblin", "Mira", "Aldric"], "entries": [
        {"name": "Aldric", "type": "pc", "modifier": 2, "rolled": True, "roll": 1, "total": 3, "auto": True},
        {"name": "Mira", "type": "pc", "modifier": 3, "rolled": True, "roll": 10, "total": 13, "auto": False},
        {"name": "Goblin", "type": "npc", "modifier": 2, "rolled": True, "roll": 15, "total": 17, "auto": False}]}

    def test_a_phone_may_roll_only_for_its_own_character(self):
        rows = self.rows(self.OPEN, " aldric ")
        self.assertEqual([(r["name"], r["mine"], r["canRoll"], r["total"]) for r in rows],
                         [("Aldric", True, True, None), ("Mira", False, False, 13)])
        rows = self.rows(self.OPEN, "Mira")
        self.assertEqual([(r["name"], r["mine"], r["canRoll"]) for r in rows],
                         [("Aldric", False, False), ("Mira", True, False)])

    def test_the_table_screen_may_roll_for_anyone_still_waiting(self):
        rows = self.rows(self.OPEN, "")
        self.assertEqual([(r["name"], r["mine"], r["canRoll"]) for r in rows],
                         [("Aldric", False, True), ("Mira", False, False)])

    def test_once_settled_everyone_is_listed_in_turn_order_and_nobody_can_roll(self):
        rows = self.rows(self.DONE, "Aldric")
        self.assertEqual([(r["name"], r["npc"], r["total"], r["canRoll"], r["auto"]) for r in rows],
                         [("Goblin", True, 17, False, False), ("Mira", False, 13, False, False),
                          ("Aldric", False, 3, False, True)])
        self.assertEqual(self.rows(None, "Aldric"), [])

    def test_the_page_listens_and_has_the_card(self):
        src = HTML.read_text(encoding="utf-8")
        for needle in ('id="init-card"', 'id="init-mine"', "'initiative' in payload", "fetch('/initiative/roll'"):
            self.assertIn(needle, src)

    def test_strings_exist_in_every_language(self):
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("init.title", "init.order", "init.roll", "init.rollFor", "init.waiting", "init.auto", "init.seconds"):
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
