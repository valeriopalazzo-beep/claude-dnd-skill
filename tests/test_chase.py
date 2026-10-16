"""
test_chase.py — a chase on the players' phones (DMG 2014 pp. 252-255).

chase.py keeps a quarry and its pursuers on one line: initiative, moves and
dashes (3 + CON free, then a DC 10 CON save or exhaustion), complications at
the end of each turn for the next runner, escapes at the end of the round,
caught when a pursuer reaches the quarry. The server holds it; a player acts
on their own turn from the phone; the DM gets [[CHASE: …]] lines.

Run from repo root:
    python -m pytest tests/test_chase.py -q
"""
import importlib.util
import json
import os
import pathlib
import random
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

import chase  # noqa: E402


class Dice(random.Random):
    """A d20 that rolls what the test says, in order; everything else from a seed."""

    def __init__(self, d20s=()):
        super().__init__(7)
        self.d20s = list(d20s)

    def randint(self, a, b):
        if (a, b) == (1, 20) and self.d20s:
            return self.d20s.pop(0)
        return super().randint(a, b)


def new_chase(d20s=(), gap=12, setting="urban"):
    thief = chase.runner("Ladro", "", speed=9, con=1, dex=3, stealth=5)
    lyra = chase.runner("Lyra", "", speed=9, con=0, dex=2, passive=13, pc=True)
    guard = chase.runner("Guardia", "", speed=9, con=1, dex=0, passive=12)
    # initiative d20s: Ladro 15+3, Lyra 10+2, Guardia 5+0
    return chase.start([thief], [lyra, guard], gap, setting, Dice([15, 10, 5, *d20s]))


class RuleTests(unittest.TestCase):
    def test_start_and_the_order(self):
        st = new_chase()
        self.assertEqual([r["name"] for r in st["runners"]], ["Ladro", "Lyra", "Guardia"])
        self.assertEqual([r["pos"] for r in st["runners"]], [12, 0, 0])
        self.assertEqual(chase.current(st)["name"], "Ladro")
        self.assertEqual(chase.find(st, "lyra")["free"], 3)
        for bad in (([], [chase.runner("A", "")], 5), ([chase.runner("A", "")], [chase.runner("a", "")], 5),
                    ([chase.runner("A", "")], [chase.runner("B", "")], 0)):
            with self.assertRaises(chase.ChaseError):
                chase.start(*bad)

    def test_turns_moves_and_complications(self):
        st = new_chase()
        with self.assertRaises(chase.ChaseError):
            chase.act(st, "Lyra", "move", Dice([20]))           # not her turn
        ev = chase.act(st, "Ladro", "move", Dice([3]))           # complication 3 (urban: window) for Lyra
        self.assertEqual([e["key"] for e in ev], ["move", "complication"])
        self.assertEqual(chase.find(st, "Ladro")["pos"], 21)
        self.assertEqual(chase.find(st, "Lyra")["complication"]["key"], "urban.window")
        self.assertEqual(chase.current(st)["name"], "Lyra")
        chase.act(st, "Lyra", "dash", Dice([20]))
        self.assertEqual(chase.find(st, "Lyra")["pos"], 18)
        self.assertNotIn("complication", chase.find(st, "Lyra"), "it was for this turn")
        self.assertEqual(chase.gap_of(st, chase.find(st, "Lyra")), 3)

    def test_dashes_beyond_the_free_ones_cost_a_save(self):
        st = new_chase()
        r = chase.find(st, "Ladro")
        r["dashes"] = r["free"]                                   # 4 used already
        ev = chase.act(st, "Ladro", "dash", Dice([5, 20]))        # CON 5+1 = 6 < 10
        self.assertEqual([e["key"] for e in ev], ["dash", "dash.fail"])
        self.assertEqual(r["exhaustion"], 1)
        r["exhaustion"] = 1
        self.assertEqual(chase.speed_now(r), 9)
        r["exhaustion"] = 2
        self.assertEqual(chase.speed_now(r), 4.5)

    def test_five_levels_and_you_drop_out(self):
        st = new_chase()
        r = chase.find(st, "Ladro")
        r["dashes"], r["exhaustion"] = 9, 4
        ev = chase.act(st, "Ladro", "dash", Dice([1, 20]))
        self.assertIn("out", [e["key"] for e in ev])
        self.assertTrue(st["over"], "the only quarry is out: the chase ends")

    def test_caught(self):
        st = new_chase(gap=6)
        chase.act(st, "Ladro", "stop", Dice([20]))
        self.assertTrue(st["over"], "the only quarry gave up")
        with self.assertRaises(chase.ChaseError):
            chase.act(st, "Lyra", "move", Dice([20]))
        st = new_chase(gap=6)
        chase.act(st, "Ladro", "move", Dice([20]))
        chase.act(st, "Lyra", "dash", Dice([20]))
        self.assertEqual(chase.find(st, "Ladro")["status"], "caught")
        self.assertTrue(st["over"])

    def test_escape_at_the_end_of_the_round(self):
        st = new_chase(gap=30)
        chase.act(st, "Ladro", "dash", Dice([20]))
        st["sight"] = False
        chase.act(st, "Lyra", "move", Dice([20]))
        ev = chase.act(st, "Guardia", "move", Dice([20, 9]))     # no complication; Ladro's Stealth 9+5 = 14 ≥ 13
        self.assertIn("escaped", [e["key"] for e in ev])
        self.assertTrue(st["over"])
        st = new_chase(gap=30)
        chase.act(st, "Ladro", "dash", Dice([20]))
        chase.act(st, "Lyra", "move", Dice([20]))
        ev = chase.act(st, "Guardia", "move", Dice([20, 1]))     # in sight: no escape check at all
        self.assertNotIn("spotted", [e["key"] for e in ev])
        self.assertEqual((st["round"], chase.current(st)["name"], st["sight"]), (2, "Ladro", True))

    def test_a_pc_quarry_is_asked(self):
        lyra = chase.runner("Lyra", "", 9, 0, 2, 4, pc=True)
        st = chase.start([lyra], [chase.runner("Thug", "", 9, passive=10)], 20, "wild", Dice([18, 2]))
        chase.act(st, "Lyra", "move", Dice([20]))
        st["sight"] = False
        ev = chase.act(st, "Thug", "move", Dice([20]))
        self.assertEqual(ev[-1]["key"], "escape.ask")
        self.assertIn("Stealth) against 10", ev[-1]["text"])
        self.assertEqual(chase.mark(st, "Lyra", "escaped")["key"], "mark.escaped")
        self.assertTrue(st["over"])

    def test_the_dm_line(self):
        st = new_chase()
        ev = chase.act(st, "Ladro", "move", Dice([20]))
        ev = chase.act(st, "Lyra", "move", Dice([20]))
        line = chase.format_note(ev, st)
        self.assertEqual(line, '[[CHASE: Lyra moves 9 m. Now Guardia\'s turn: chase.py act "Guardia" move|dash|stop.]]')


class WhoRunsTests(unittest.TestCase):
    def test_a_sheet_and_the_srd(self):
        import inventory
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="dnd-test-chase-"))
        (tmp / "hernio.md").write_text("# Hernio\n| STR | DEX | CON | INT | WIS | CHA |\n|---|---|---|---|---|---|\n"
                                       "| 8 (-1) | 17 (+3) | 13 (+1) | 10 (+0) | 12 (+1) | 15 (+2) |\n"
                                       "- **AC:** 14 | **Initiative:** +3 | **Speed:** 25 ft\n- **Passive Perception:** 11\n"
                                       "| Stealth | +5 |\n", encoding="utf-8")
        old = (inventory.paths.characters_dir, inventory.active_campaign)
        inventory.paths.characters_dir = lambda: tmp
        inventory.active_campaign = lambda: ""
        try:
            h = chase.from_sheet("hernio")
        finally:
            inventory.paths.characters_dir, inventory.active_campaign = old
        self.assertEqual((h["name"], h["speed"], h["con"], h["dex"], h["stealth"], h["passive"], h["pc"]),
                         ("Hernio", 7.5, 1, 3, 5, 11, True))
        thugs = chase.from_spec("Thug:2")
        self.assertEqual([t["name"] for t in thugs], ["Thug 1", "Thug 2"])
        self.assertEqual((thugs[0]["speed"], thugs[0]["con"], thugs[0]["pc"]), (9.0, 2, False))
        thief = chase.from_spec("Ladro:speed=12,con=1,stealth=6")
        self.assertEqual((thief[0]["speed"], thief[0]["stealth"]), (12.0, 6))
        with self.assertRaises(chase.ChaseError):
            chase.from_spec("Ladro:veloce")


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_chase", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.mod._rate_ok = lambda ip: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        m = self.mod
        saved = (m._broadcast, m._persist_chase, m._active_campaign, m._chase, m._chase_lib.notes_add)
        self.addCleanup(lambda: setattr(m, "_broadcast", saved[0]))
        self.addCleanup(lambda: setattr(m, "_persist_chase", saved[1]))
        self.addCleanup(lambda: setattr(m, "_active_campaign", saved[2]))
        self.addCleanup(lambda: setattr(m, "_chase", saved[3]))
        self.addCleanup(lambda: setattr(m._chase_lib, "notes_add", saved[4]))
        self.sent, self.notes = [], []
        m._broadcast = self.sent.append
        m._persist_chase = lambda: None
        m._active_campaign = lambda: "prova"
        m._chase = None
        m._chase_lib.notes_add = self.notes.append

    def start(self):
        body = {"op": "start", "gap": 12, "setting": "urban",
                "quarry": [chase.runner("Ladro", "", 9, 1, 30, 5)],
                "pursuers": [chase.runner("Lyra", "", 9, 0, -5, 2, 13, pc=True), chase.runner("Guardia", "", 9, 0, -5)]}
        r = self.client.post("/chase", json=body)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()["chase"]

    def test_a_chase_from_start_to_a_players_turn(self):
        view = self.start()
        self.assertEqual(self.sent[-1]["chase"]["id"], view["id"])
        while view["turn"] != "Lyra":                              # the DM plays the creatures before her
            r = self.client.post("/chase/act", json={"character": "Lyra", "action": "move"})
            self.assertEqual((r.status_code, r.get_json()["code"]), (409, "turn"))
            r = self.client.post("/chase", json={"op": "act", "name": view["turn"], "action": "move"})
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
            view = r.get_json()["chase"]
        self.assertEqual(self.notes, [], "the DM's own moves are printed to them, not queued")
        r = self.client.post("/chase/act", json={"character": "lyra", "action": "dash"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertTrue(self.notes[-1].startswith("[[CHASE: Lyra dashes 18 m"), self.notes[-1])
        self.assertEqual(self.sent[-1]["chase_events"][0]["key"], "dash")
        r = self.client.post("/chase/act", json={"character": "Guardia", "action": "move"})
        self.assertEqual(r.status_code, 403, "a creature is the DM's")

    def test_the_dm_ops_and_refusals(self):
        self.assertEqual(self.client.post("/chase", json={"op": "act", "name": "X", "action": "move"}).status_code, 409)
        self.assertEqual(self.client.post("/chase", json={"op": "start", "gap": 0, "quarry": [{"name": "A"}],
                                                          "pursuers": [{"name": "B"}]}).status_code, 400)
        self.start()
        for body in ({"op": "adjust", "name": "Lyra", "metres": 3}, {"op": "sight", "on": False},
                     {"op": "cover", "cover": "crowded"}, {"op": "mark", "name": "Guardia", "status": "out"}):
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/chase", json=body).status_code, 200)
        view = self.client.get("/chase").get_json()["chase"]
        self.assertEqual((view["sight"], view["cover"]), (False, "crowded"))
        self.assertEqual({r["name"]: (r["pos"], r["status"]) for r in view["runners"]}["Lyra"], (3, "in"))
        self.assertEqual(self.client.post("/chase", json={"op": "cover", "cover": "fitto"}).status_code, 400)
        self.assertEqual(self.client.post("/chase", json={"op": "end"}).status_code, 200)
        self.assertIsNone(self.client.get("/chase").get_json()["chase"])
        self.assertIn("chase_set", self.mod._DM_ENDPOINTS)
        self.assertNotIn("chase_act", self.mod._DM_ENDPOINTS)
        for script in ("check_input.py", "autorun_wait.py"):
            self.assertIn("chase", (DISPLAY / script).read_text(encoding="utf-8"))


_SLICE = re.compile(r"(function _chasePlan\(chase, me\).*?)\n// ── end chase plan ──", re.S)


class PageTests(unittest.TestCase):
    def test_the_plan_of_one_screen(self):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the chase block is no longer where this test slices it")
        st = new_chase()
        chase.act(st, "Ladro", "move", Dice([20]))
        chase.find(st, "Lyra")["dashes"] = 3
        view = chase.public_view(st)
        script = m.group(1) + "\nconsole.log(JSON.stringify([_chasePlan(%s, 'lyra'), _chasePlan(%s, 'Guardia'), _chasePlan(null, 'x')]));\n" % (
            json.dumps(view), json.dumps(view))
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            lyra, guard, none = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        self.assertEqual([(x["name"], x["pct"], x["turn"]) for x in lyra["rows"]],
                         [("Ladro", 100, False), ("Lyra", 0, True), ("Guardia", 0, False)])
        self.assertEqual((lyra["myTurn"], lyra["move"], lyra["dash"], lyra["freeLeft"], lyra["dashSave"]), (True, 9, 18, 0, True))
        self.assertEqual((guard["myTurn"], guard["freeLeft"]), (False, 4))
        self.assertIsNone(none)

    def test_the_page_has_the_panel_and_every_way_in(self):
        src = HTML.read_text(encoding="utf-8")
        for needle in ('id="chase-modal"', 'id="ln-chase"', 'data-ln-proxy="ln-chase"', 'id="ph-chase-opt"',
                       "window._onChase(payload)", 'id="ln-i-run"'):
            self.assertIn(needle, src)

    def test_strings_exist_in_every_language(self):
        src = (DISPLAY / "chase.py").read_text(encoding="utf-8")
        keys = {f"chase.cx.{s}.{k}" for s, rows in chase.COMPLICATIONS.items() for k, _, _ in rows}
        keys |= {"chase.ev." + k for k in re.findall(r'_say\(state, "([\w.]*\w)"', src)}
        keys |= {"chase.ev." + k for k in ("dash", "move", "mark.escaped", "mark.caught", "mark.out", "mark.in",
                                           "sight.on", "sight.off", "cover.crowded", "cover.normal", "cover.open", "start")}
        keys |= set(re.findall(r"_t\('(chase\.[\w.]*\w)'", HTML.read_text(encoding="utf-8")))
        keys |= {"chase.status." + s for s in ("caught", "escaped", "out", "stopped")}
        keys |= {"chase.setting." + s for s in chase.SETTINGS}
        self.assertGreater(len(keys), 60)
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in sorted(keys):
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
