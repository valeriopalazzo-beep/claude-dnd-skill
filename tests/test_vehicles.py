"""
test_vehicles.py — carts, barges and ships (PHB 2014 pp. 155-157, DMG 2014 p. 119).

display/vehicles.py keeps the party's vehicles: a boat or a ship with its own
speed, hit points behind a damage threshold, crew and hold; a cart pulled by
animals at their pace, carrying five times what they could. Aboard, the region
map counts a journey at the vessel's speed — the current and a headwind
counted, a full crew sailing day and night, oars only 8 hours a day.

Run from repo root:
    python -m pytest tests/test_vehicles.py -q
"""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
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

import region_map  # noqa: E402
import vehicles  # noqa: E402


class RuleTests(unittest.TestCase):
    def test_the_books_numbers(self):
        self.assertEqual((vehicles.KINDS["keelboat"]["kmh"], vehicles.KINDS["keelboat"]["threshold"]), (1.5, 10))
        self.assertEqual(vehicles.kind_of("nave a vela"), "sailing-ship")
        self.assertEqual(vehicles.kind_of("Chiatta"), "keelboat")
        with self.assertRaises(vehicles.VehicleError):
            vehicles.kind_of("sottomarino")

    def test_a_wagon_carries_five_times_what_its_horses_could(self):
        animals, kg = vehicles.team("2 cavalli da tiro, 1 mulo")
        self.assertEqual((animals, kg), ([{"animal": "draft horse", "n": 2}, {"animal": "mule", "n": 1}], 3750))
        d = vehicles.blank()
        cart = vehicles.add(d, "Carro di Ugo", "carro", pulled_by="2 cavalli da tiro")
        self.assertEqual(cart["capacity_kg"], 2500)                 # 2 × 270 × 5, less the wagon's 200 kg
        self.assertNotIn("hp", cart)
        with self.assertRaises(vehicles.VehicleError):
            vehicles.add(d, "Barca", "barca a remi", pulled_by="1 mulo")

    def test_below_the_threshold_nothing_happens(self):
        d = vehicles.blank()
        boat = vehicles.add(d, "Chiatta di Bortolo", "keelboat", owner="Bortolo")
        self.assertEqual(vehicles.hit(boat, 9)["taken"], 0)
        self.assertEqual(vehicles.hit(boat, 10), {"taken": 10, "threshold": 10, "current": 90, "sunk": False})
        self.assertTrue(vehicles.hit(boat, 200)["sunk"])
        self.assertEqual(vehicles.repair(boat, 500), 100)
        self.assertEqual(boat["hp"]["current"], 100)

    def test_how_far_it_goes(self):
        d = vehicles.blank()
        boat = vehicles.add(d, "Chiatta", "keelboat")
        ship = vehicles.add(d, "Aurora", "sailing-ship")
        self.assertEqual(vehicles.speed(d, boat)["km_day"], 12)            # rowed: 8 hours a day
        self.assertEqual(vehicles.speed(d, ship)["km_day"], 72)            # a full crew: day and night
        d["current"] = "down"
        self.assertEqual(vehicles.speed(d, boat)["kmh"], 6)
        d["current"] = "up"
        self.assertEqual(vehicles.speed(d, boat)["kmh"], 0)                # it cannot row against it
        d.update(current="none", headwind=True)
        self.assertEqual(vehicles.speed(d, ship, wind_strong=True)["kmh"], 1.5)
        self.assertEqual(vehicles.speed(d, ship)["kmh"], 3)                # no strong wind today: no harm
        ship["crew"] = 5
        self.assertEqual(vehicles.speed(d, ship)["hours_day"], 8)

    def test_a_journey_aboard(self):
        d = vehicles.blank()
        vehicles.add(d, "Chiatta", "keelboat")
        self.assertIsNone(vehicles.journey(d, 1))
        d.update(aboard="Chiatta", current="down")
        jr = vehicles.journey(d, 1)                                     # a day on foot is 36 km
        self.assertEqual((jr["hours"], jr["days"]), (6, 0.75))


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._old = os.environ.get("DND_CAMPAIGN_ROOT")
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        (self.root / "campaigns" / "prova" / "characters").mkdir(parents=True)
        saved = vehicles._announce
        vehicles._announce = lambda event=None: None
        self.addCleanup(setattr, vehicles, "_announce", saved)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("DND_CAMPAIGN_ROOT", None)
        else:
            os.environ["DND_CAMPAIGN_ROOT"] = self._old
        self._tmp.cleanup()

    def run_v(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = vehicles.main(["--campaign", "prova", *argv])
        return code, out.getvalue() + err.getvalue()

    def test_the_command_line(self):
        code, out = self.run_v("add", "Chiatta di Bortolo", "--kind", "barcone", "--owner", "Bortolo")
        self.assertEqual(code, 0, out)
        self.assertIn("damage threshold 10", out)
        code, out = self.run_v("board", "chiatta", "--current", "giù")
        self.assertIn("aboard Chiatta di Bortolo: 6 km/h, 8 h a day (48 km)", out)
        code, out = self.run_v("hit", "chiatta", "8")
        self.assertIn("below its damage threshold", out)
        code, out = self.run_v("cargo", "chiatta", "add", "10 barili di vino")
        self.assertIn("· 10 barili di vino", out)
        code, out = self.run_v("board", "chiatta", "--current", "up")
        self.assertEqual(code, 1)
        self.assertIn("cannot go against this current", out)
        data = vehicles.load(vehicles.data_path("prova"))
        self.assertEqual((data["aboard"], data["current"]), ("Chiatta di Bortolo", "down"))     # the refused board changed nothing
        code, out = self.run_v("leave")
        self.assertIn("on foot again", out)
        self.assertEqual(self.run_v("leave")[0], 1)

    def test_the_region_map_counts_the_journey_aboard(self):
        data = region_map.empty()
        region_map.add_place(data, "Ferrovalle", "city", at="300,300")
        region_map.add_place(data, "Isola dei Ricambi", "landmark", near="Ferrovalle", direction="E", days=1, road="river")
        region_map.add_place(data, "Torre", "tower", near="Isola dei Ricambi", direction="N", days=0.5, road="road")
        self.assertEqual(region_map._aboard(data, ["Ferrovalle", "Isola dei Ricambi"], 1, "prova"), "")
        self.run_v("add", "Chiatta", "--kind", "keelboat")
        self.run_v("board", "Chiatta", "--current", "down")
        line = region_map._aboard(data, ["Ferrovalle", "Isola dei Ricambi"], 1, "prova")
        self.assertEqual(line, " — aboard Chiatta: 6 hours; downstream: +4.5 km/h [aboard: calendar.py advance 6 hours]")
        line = region_map._aboard(data, ["Ferrovalle", "Isola dei Ricambi", "Torre"], 1.5, "prova")
        self.assertIn("⚠ by land, where the keelboat does not go: Isola dei Ricambi–Torre", line)
        self.run_v("board", "Chiatta")
        line = region_map._aboard(data, ["Ferrovalle", "Isola dei Ricambi"], 1, "prova")
        self.assertIn("24 hours (3 days at 8 h a day)", line)
        self.assertIn("advance 3 days", line)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_vehicles", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def test_every_screen_gets_the_list(self):
        m = self.mod
        sent = []
        saved = (m._broadcast, m._public_vehicles)
        self.addCleanup(lambda: (setattr(m, "_broadcast", saved[0]), setattr(m, "_public_vehicles", saved[1])))
        m._broadcast = sent.append
        m._public_vehicles = lambda: [{"id": "a1", "name": "Chiatta", "kind": "keelboat", "aboard": True}]
        self.assertEqual(self.client.get("/vehicles").get_json()[0]["name"], "Chiatta")
        self.assertEqual(self.client.post("/vehicles", json={"event": {"name": "Chiatta", "what": "sunk"}}).status_code, 204)
        self.assertEqual(sent[-1]["vehicle_event"], {"name": "Chiatta", "what": "sunk"})
        self.client.post("/vehicles", json={"event": {"name": "Chiatta", "what": "<script>"}})
        self.assertNotIn("vehicle_event", sent[-1])
        self.assertIn("vehicles_changed", m._DM_ENDPOINTS)
        self.assertNotIn("vehicles_get", m._DM_ENDPOINTS)


_SLICE = __import__("re").compile(r"(function _vehicleLines\(v\).*?)\n// ── end vehicle lines ──", __import__("re").S)


class PageTests(unittest.TestCase):
    def test_the_sheet_lines(self):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the vehicle block is no longer where this test slices it")
        d = vehicles.blank()
        vehicles.add(d, "Chiatta", "keelboat")
        vehicles.add(d, "Carro", "wagon", pulled_by="2 cavalli da tiro")
        d.update(aboard="Chiatta", current="down")
        rows = vehicles.public_view(d)
        script = ("const window = {};\nconst _t = (k, v, fb) => k + (v ? ' ' + JSON.stringify(v) : '');\n" + m.group(1)
                  + "\nconsole.log(JSON.stringify(%s.map(_vehicleLines)));\n" % json.dumps(rows))
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            boat, cart = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        self.assertEqual(boat[:2], ['veh.speed {"kmh":"6","km":"48","h":8}', "veh.current.down"])
        self.assertTrue(boat[2].startswith('veh.ac {"ac":15} · veh.threshold'))
        self.assertEqual(cart[0], "veh.pace")
        self.assertIn('veh.carries {"kg":"2500"}', cart[1])

    def test_the_panel_and_every_way_in(self):
        src = HTML.read_text(encoding="utf-8")
        for needle in ('id="veh-modal"', 'id="ln-veh"', 'data-ln-proxy="ln-veh"', 'id="ph-veh-opt"', 'id="ln-i-boat"',
                       "window._onVehicles(payload.vehicles, payload.vehicle_event)", "fetch('/vehicles')"):
            self.assertIn(needle, src)

    def test_strings_exist_in_every_language(self):
        keys = ["veh.title", "veh.all", "veh.empty", "veh.aboard", "veh.owner", "veh.party", "veh.speed", "veh.pace",
                "veh.current.down", "veh.current.up", "veh.ac", "veh.threshold", "veh.crew", "veh.passengers", "veh.hold",
                "veh.team", "veh.carries", "veh.cargo"] + [f"veh.kind.{k}" for k in vehicles.KINDS] \
            + [f"veh.animal.{a}" for a in vehicles.ANIMALS] + [f"veh.toast.{w}" for w in ("new", "board", "leave", "hit", "sunk", "repair")]
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
