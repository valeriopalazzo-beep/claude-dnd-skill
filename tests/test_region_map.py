"""
test_region_map.py — the region map grows with what the party discovers.

display/region_map.py keeps places, roads and the party's position in
<campaign>/region.json, places a new place by direction and days of travel,
finds the shortest known route, and draws the whole thing as an SVG. The
display serves where each place sits on the picture and how far it is.

Run from repo root:
    python -m pytest tests/test_region_map.py -q
"""
import importlib.util
import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
import xml.dom.minidom

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"
sys.path.insert(0, str(DISPLAY))

import region_map as rm  # noqa: E402


def _valley() -> dict:
    d = rm.empty()
    d["title"] = "Valle di Cenere"
    rm.add_place(d, "Sarvenna", "città", at="38,58", note="Capitale del ducato")
    rm.add_place(d, "Cripta di Vessar", "cripta", near="Sarvenna", direction="NE", days=2, road="trail")
    rm.add_place(d, "Bivio del Corvo", "villaggio", near="Sarvenna", direction="S", days=1.5)
    rm.add_place(d, "Rocca Brumosa", "castello", near="Bivio", direction="E", days=2)
    rm.add_place(d, "Porto Grigio", "porto", near="Sarvenna", direction="W", days=3, rumor=True)
    rm.add_place(d, "Isola dei Corvi", "torre", at="10,15", rumor=True)
    return d


class PlacingTests(unittest.TestCase):

    def test_kinds_are_understood_in_italian_and_default_to_landmark(self):
        d = _valley()
        self.assertEqual([p["kind"] for p in d["places"]], ["city", "dungeon", "village", "castle", "port", "tower"])
        self.assertEqual(rm.add_place(d, "Pietra Ritta", at="50,50")["kind"], "landmark")

    def test_a_place_near_another_is_linked_to_it_by_a_road_of_that_many_days(self):
        d = _valley()
        self.assertIn({"from": "Sarvenna", "to": "Cripta di Vessar", "days": 2.0, "kind": "trail"}, d["roads"])
        self.assertIn({"from": "Bivio del Corvo", "to": "Rocca Brumosa", "days": 2.0, "kind": "road"}, d["roads"])
        self.assertEqual(len(d["roads"]), 4, "a place given with --at has no road yet")

    def test_one_day_is_as_long_on_the_drawing_in_every_direction(self):
        d = rm.empty()
        rm.add_place(d, "Centro", at="50,50")
        for way in ("N", "NE", "E", "SE", "S", "SW", "W", "NW"):
            rm.add_place(d, "A " + way, near="Centro", direction=way, days=2)
        cx, cy = rm._raw(d["places"][0])
        for p in d["places"][1:]:
            x, y = rm._raw(p)
            with self.subTest(place=p["name"]):
                self.assertAlmostEqual(((x - cx) ** 2 + (y - cy) ** 2) ** 0.5, 200, delta=1.5)
        north = next(p for p in d["places"] if p["name"] == "A N")
        self.assertLess(north["y"], 50)
        self.assertAlmostEqual(north["x"], 50)

    def test_a_short_leg_is_still_drawn_apart(self):
        d = rm.empty()
        rm.add_place(d, "Qui", at="50,50")
        rm.add_place(d, "Vicino", near="Qui", direction="E", days=0.25)
        (x0, _), (x1, _) = rm._raw(d["places"][0]), rm._raw(d["places"][1])
        self.assertGreaterEqual(x1 - x0, rm.MIN_GAP_PX - 1)
        self.assertEqual(d["roads"][0]["days"], 0.25, "the road still says how long it really is")

    def test_italian_west_is_read(self):
        d = rm.empty()
        rm.add_place(d, "Qui", at="50,50")
        self.assertLess(rm.add_place(d, "Ovest", near="Qui", direction="O", days=1)["x"], 50)

    def test_what_cannot_be_placed_is_refused_and_changes_nothing(self):
        d = _valley()
        before = json.dumps(d, sort_keys=True)
        for kwargs in (dict(name="Sarvenna", at="1,1"), dict(name="Senza posto"),
                       dict(name="Lontano", near="Atlantide", direction="N"),
                       dict(name="Storto", near="Sarvenna", direction="SU"),
                       dict(name="Nave", kind="astronave", at="1,1"),
                       dict(name="Zero", near="Sarvenna", direction="N", days=0),
                       dict(name="Male", at="dieci,venti")):
            with self.subTest(kwargs=kwargs), self.assertRaises(rm.RegionError):
                rm.add_place(d, **kwargs)
        self.assertEqual(json.dumps(d, sort_keys=True), before)

    def test_a_place_is_found_by_part_of_its_name_unless_that_is_ambiguous(self):
        d = _valley()
        self.assertEqual(rm.find_place(d, "cripta")["name"], "Cripta di Vessar")
        rm.add_place(d, "Cripta dei Re", at="80,80")
        with self.assertRaises(rm.RegionError) as cm:
            rm.find_place(d, "cripta")
        self.assertIn("Cripta dei Re", str(cm.exception))
        self.assertEqual(rm.find_place(d, "Cripta dei Re")["name"], "Cripta dei Re")

    def test_linking_the_same_two_places_again_replaces_the_road(self):
        d = _valley()
        rm.add_road(d, "Cripta", "Sarvenna", 1, "strada")
        pair = [r for r in d["roads"] if {r["from"], r["to"]} == {"Sarvenna", "Cripta di Vessar"}]
        self.assertEqual(pair, [{"from": "Cripta di Vessar", "to": "Sarvenna", "days": 1.0, "kind": "road"}])

    def test_removing_a_place_takes_its_roads_and_the_party_marker_with_it(self):
        d = _valley()
        rm.set_here(d, "Bivio")
        self.assertEqual(rm.remove_place(d, "Bivio"), "Bivio del Corvo")
        self.assertEqual(d["party"], "")
        self.assertFalse([r for r in d["roads"] if "Bivio del Corvo" in (r["from"], r["to"])])


class TravelTests(unittest.TestCase):

    def test_travel_takes_the_shortest_known_route_and_marks_what_it_passes(self):
        d = _valley()
        rm.set_here(d, "Cripta")
        days, route = rm.travel(d, "Rocca")
        self.assertEqual((days, route), (5.5, ["Cripta di Vessar", "Sarvenna", "Bivio del Corvo", "Rocca Brumosa"]))
        self.assertEqual(d["party"], "Rocca Brumosa")
        rm.add_road(d, "Cripta", "Rocca", 3, "trail")             # a shortcut is learned
        self.assertEqual(rm.travel(d, "Cripta"), (3.0, ["Rocca Brumosa", "Cripta di Vessar"]))

    def test_reaching_a_rumoured_place_makes_it_visited(self):
        d = _valley()
        rm.set_here(d, "Sarvenna")
        self.assertEqual(rm.find_place(d, "Porto")["status"], "known")
        rm.travel(d, "Porto")
        self.assertEqual(rm.find_place(d, "Porto")["status"], "visited")

    def test_no_road_no_start_or_already_there_moves_nobody(self):
        d = _valley()
        with self.assertRaises(rm.RegionError):                    # the map does not know where the party is
            rm.travel(d, "Rocca")
        rm.set_here(d, "Sarvenna")
        for dest in ("Isola dei Corvi", "Sarvenna"):
            with self.subTest(dest=dest), self.assertRaises(rm.RegionError):
                rm.travel(d, dest)
        self.assertEqual(d["party"], "Sarvenna")
        self.assertEqual(rm.find_place(d, "Isola")["status"], "known")

    def test_days_print_as_a_traveller_would_say_them(self):
        self.assertEqual([rm.fmt_days(v) for v in (2, 1.5, 0.5, 2.25)], ["2", "1½", "½", "2.2"])


class DrawingTests(unittest.TestCase):

    def test_the_map_is_well_formed_svg_with_every_name_escaped(self):
        d = _valley()
        d["legend"] = "Giorni di cammino <a piedi>"
        rm.add_place(d, 'Locanda "Al Gallo" & Figli', "paese", at="70,70")
        rm.set_here(d, "Sarvenna")
        svg = rm.render(d)
        xml.dom.minidom.parseString(svg)                            # raises if it is not well-formed
        self.assertIn("Locanda \"Al Gallo\" &amp; Figli", svg)
        self.assertIn("&lt;a piedi&gt;", svg)
        self.assertIn("Porto Grigio ?", svg, "a rumoured place is marked")
        self.assertNotIn("Sarvenna ?", svg)
        self.assertIn("1½", svg)

    def test_an_empty_map_still_draws(self):
        xml.dom.minidom.parseString(rm.render(rm.empty()))

    def test_a_region_that_fits_is_drawn_where_its_coordinates_say(self):
        d = rm.empty()
        rm.add_place(d, "Angolo", at="0,0")
        rm.add_place(d, "Fondo", at="100,100")
        self.assertEqual(rm._transform(d), (1.0, rm.PAD_X, rm.PAD_TOP))
        view = {p["name"]: (p["px"], p["py"]) for p in rm.public_view(d)["places"]}
        self.assertEqual(view["Angolo"], (round(rm.PAD_X / rm.W * 100, 2), round(rm.PAD_TOP / rm.H * 100, 2)))

    def test_a_region_that_outgrew_the_sheet_is_zoomed_out_not_squeezed(self):
        d = rm.empty()
        rm.add_place(d, "Centro", at="50,50")
        last = "Centro"
        for i in range(8):                                          # eight days due south: off the bottom edge
            rm.add_place(d, f"Tappa {i}", near=last, direction="S", days=1)
            last = f"Tappa {i}"
        self.assertGreater(d["places"][-1]["y"], 100)
        scale, _ox, _oy = rm._transform(d)
        self.assertLess(scale, 1.0)
        ys = [p["py"] for p in rm.public_view(d)["places"]]
        self.assertTrue(all(rm.PAD_TOP / rm.H * 100 - 0.1 <= y <= (rm.H - rm.PAD_BOTTOM) / rm.H * 100 + 0.1 for y in ys))
        gaps = [b - a for a, b in zip(ys, ys[1:])]
        self.assertAlmostEqual(min(gaps), max(gaps), places=1, msg="distances stay in proportion")

    def test_names_do_not_sit_on_top_of_each_other(self):
        d = _valley()
        boxes = [spot[3] for spot in rm._layout(d).values()]
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                self.assertEqual(rm._overlap(a, b), 0.0)


class CloseUpTests(unittest.TestCase):
    """A village and what lies an hour around it: counted in hours, not in tenths of a day."""

    def _village(self):
        d = rm.empty()
        rm.add_place(d, "Vallombra", "villaggio", at="50,60")
        rm.add_place(d, "Pozzo", "campo", near="Vallombra", direction="N", days=0.1)
        rm.add_place(d, "Carbonaia", near="Vallombra", direction="W", days=0.1, road="trail")
        rm.add_place(d, "Rocca", "rovine", near="Carbonaia", direction="W", days=0.4, road="trail")
        rm.add_place(d, "Locanda", at="56,68")                       # no road: it is in the village
        return d

    def test_in_days_an_hour_and_half_a_day_are_the_same_stretch_of_paper(self):
        d = self._village()
        x = {p["name"]: p["x"] for p in d["places"]}
        self.assertAlmostEqual(x["Vallombra"] - x["Carbonaia"], x["Carbonaia"] - x["Rocca"], places=1)

    def test_in_hours_the_roads_are_as_long_as_they_take(self):
        d = self._village()
        rm.add_road(d, "Carbonaia", "Rocca", hours=3, kind="trail")
        rm.add_road(d, "Vallombra", "Carbonaia", hours=1, kind="trail")
        rm.add_road(d, "Vallombra", "Pozzo", hours=1)
        before = {p["name"]: (p["x"], p["y"]) for p in d["places"]}
        self.assertEqual(rm.set_scale(d, "ore"), 3)                  # the village stays, and the inn that is in it
        at = {p["name"]: rm._raw(p) for p in d["places"]}
        self.assertEqual((d["unit"], d["places"][0]["x"], d["places"][0]["y"]), ("hours", 50.0, 60.0))
        self.assertAlmostEqual(at["Vallombra"][0] - at["Carbonaia"][0], 110, delta=1)
        self.assertAlmostEqual(at["Carbonaia"][0] - at["Rocca"][0], 330, delta=1)
        self.assertAlmostEqual(at["Vallombra"][1] - at["Pozzo"][1], 110, delta=1)
        self.assertAlmostEqual(at["Rocca"][1], at["Vallombra"][1], delta=1, msg="the direction is kept")
        # the inn has no road: it stays as far from the village as it was
        self.assertAlmostEqual(d["places"][4]["x"] - d["places"][0]["x"], before["Locanda"][0] - before["Vallombra"][0], places=1)
        self.assertEqual(rm.relayout(d), 0, "a second pass moves nothing")
        svg = rm.render(d)
        xml.dom.minidom.parseString(svg)
        self.assertIn(">3h<", svg)
        self.assertIn(">1h<", svg)

    def test_a_close_up_fills_the_sheet_and_keeps_room_for_the_names(self):
        d = self._village()
        rm.set_scale(d, "hours")
        scale, _ox, _oy = rm._transform(d)
        self.assertGreater(scale, 1.0)
        self.assertLessEqual(scale, rm.MAX_ZOOM)
        xs = [p["px"] / 100 * rm.W for p in rm.public_view(d)["places"]]
        self.assertGreaterEqual(min(xs), rm.PAD_X + rm.CLOSE_UP_MARGIN - 1)
        self.assertLessEqual(max(xs), rm.W - rm.PAD_X - rm.CLOSE_UP_MARGIN + 1)
        one = rm.empty()
        one["unit"] = "hours"
        rm.add_place(one, "Solo", at="10,10")
        self.assertEqual(rm._transform(one), (1.0, rm.PAD_X, rm.PAD_TOP))     # nothing to fit yet

    def test_hours_are_said_to_the_quarter(self):
        for days, text in ((1 / 8, "1"), (0.1, "¾"), (1.5 / 8, "1½"), (3.25 / 8, "3¼"), (0.01, "¼"), (0, "0"), (2, "16")):
            with self.subTest(days=days):
                self.assertEqual(rm.fmt_hours(days), text)
        d = self._village()
        self.assertEqual(rm.fmt_dist(d, 0.5), "½")
        d["unit"] = "hours"
        self.assertEqual(rm.fmt_dist(d, 0.5), "4")

    def test_a_road_is_given_in_days_or_in_hours_not_both(self):
        d = self._village()
        self.assertAlmostEqual(rm.add_road(d, "Pozzo", "Carbonaia", hours="2,5")["days"], 2.5 / 8)
        p = rm.add_place(d, "Camino", near="Pozzo", direction="E", hours=1)
        self.assertAlmostEqual(d["roads"][-1]["days"], 1 / 8)
        self.assertEqual(p["status"], "visited")
        for kwargs in ({"days": 1, "hours": 2}, {}, {"hours": 0}, {"hours": "tante"}):
            with self.assertRaises(rm.RegionError):
                rm.add_road(d, "Pozzo", "Rocca", **kwargs)
        with self.assertRaises(rm.RegionError):
            rm.set_scale(d, "leghe")
        with self.assertRaises(rm.RegionError):
            rm.set_scale(d, "hours", hour_px=5)

    def test_a_place_changes_state_without_moving_the_party(self):
        d = self._village()
        rm.set_here(d, "Vallombra")
        rm.add_place(d, "Sarvenna", "città", near="Vallombra", direction="S", days=12, rumor=True)
        self.assertEqual(rm.mark(d, "sarv", "visited")["status"], "visited")
        self.assertEqual(rm.mark(d, "sarv", "rumor")["status"], "known")
        self.assertEqual(d["party"], "Vallombra")
        for name, status in (("sarv", "bruciata"), ("nessuno", "visited")):
            with self.assertRaises(rm.RegionError):
                rm.mark(d, name, status)


class PlayerViewTests(unittest.TestCase):

    def test_each_place_says_how_far_it_is_from_the_party(self):
        d = _valley()
        rm.set_here(d, "Sarvenna")
        view = {p["name"]: p for p in rm.public_view(d)["places"]}
        self.assertTrue(view["Sarvenna"]["here"])
        self.assertEqual(view["Bivio del Corvo"]["days"], "1½")
        self.assertEqual(view["Rocca Brumosa"]["days"], "3½")
        self.assertIsNone(view["Isola dei Corvi"]["days"], "no known road leads there")
        self.assertEqual(view["Porto Grigio"]["status"], "known")
        self.assertEqual(view["Sarvenna"]["note"], "Capitale del ducato")


class CommandLineTests(unittest.TestCase):

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="dnd-test-region-"))
        self._orig = (rm.data_path, rm.write_image, rm._announce)
        self.announced = []
        rm.data_path = lambda campaign="": str(self.dir / "region.json")
        rm.write_image = lambda data, campaign="": "region-test.svg"
        rm._announce = lambda data, story: self.announced.append(story)

    def tearDown(self):
        rm.data_path, rm.write_image, rm._announce = self._orig

    def _data(self):
        return json.loads((self.dir / "region.json").read_text(encoding="utf-8"))

    def test_a_session_of_discovery(self):
        steps = [["init", "Valle di Cenere", "--legend", "Giorni di cammino"],
                 ["add", "Sarvenna", "--kind", "city", "--at", "40,60"],
                 ["add", "Cripta di Vessar", "--kind", "dungeon", "--near", "Sarvenna", "--dir", "NE", "--days", "2"],
                 ["here", "Sarvenna"], ["travel", "Cripta"], ["note", "Cripta", "Sigillata dai monaci"]]
        for argv in steps:
            with self.subTest(argv=argv):
                self.assertEqual(rm.main(argv), 0)
        data = self._data()
        self.assertEqual((data["title"], data["party"], data["file"]), ("Valle di Cenere", "Cripta di Vessar", "region-test.svg"))
        self.assertEqual(data["places"][1]["note"], "Sigillata dai monaci")
        self.assertEqual(self.announced, [False, False, False, True, True, False],
                         "only moving the party puts the map in the story")

    def test_a_small_region_is_kept_in_hours_and_changed_without_postcards(self):
        steps = [["add", "Vallombra", "--kind", "villaggio", "--at", "50,60"], ["scale", "hours"],
                 ["add", "Carbonaia", "--near", "Vallombra", "--dir", "O", "--hours", "1", "--road", "sentiero"],
                 ["add", "Rocca", "--kind", "rovine", "--near", "Carbonaia", "--dir", "O", "--hours", "3", "--rumor"],
                 ["road", "Vallombra", "Rocca", "--hours", "4.5", "--kind", "trail"],
                 ["here", "Vallombra", "--quiet"], ["mark", "Rocca", "visited"], ["travel", "Rocca", "--quiet"], ["relayout"]]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            for argv in steps:
                with self.subTest(argv=argv):
                    self.assertEqual(rm.main(argv), 0)
            rm.main(["list"])
        data = self._data()
        self.assertEqual((data["unit"], data["party"], data["places"][2]["status"]), ("hours", "Rocca", "visited"))
        self.assertEqual(self.announced, [False] * len(steps), "nothing went in the story")
        text = out.getvalue()
        self.assertIn("Vallombra → Carbonaia → Rocca: 4 hours of travel", text)
        self.assertIn("calendar.py -c <campaign> advance 4 hours", text)
        self.assertIn("counted in hours", text)
        self.assertIn("Vallombra ↔ Rocca: 4½ hours (trail)", text)

    def test_a_bad_request_exits_non_zero_and_leaves_the_file_alone(self):
        rm.main(["add", "Sarvenna", "--at", "40,60"])
        before = (self.dir / "region.json").read_text(encoding="utf-8")
        self.announced.clear()
        for argv in (["travel", "Sarvenna"], ["add", "Sarvenna", "--at", "1,1"], ["road", "Sarvenna", "Nulla", "--days", "1"],
                     ["remove", "Nulla"], ["init", "X", "--day-px", "5"], ["scale", "leghe"], ["mark", "Sarvenna", "x"],
                     ["road", "Sarvenna", "Sarvenna", "--hours", "1"], ["add", "Qui", "--near", "Sarvenna", "--dir", "N",
                                                                         "--days", "1", "--hours", "1"]):
            with self.subTest(argv=argv):
                self.assertEqual(rm.main(argv), 1)
        self.assertEqual((self.dir / "region.json").read_text(encoding="utf-8"), before)
        self.assertEqual(self.announced, [])


class ServerTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_region", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()
        cls.dir = pathlib.Path(tempfile.mkdtemp(prefix="dnd-test-region-srv-"))
        cls._orig = (cls.mod._active_campaign, cls.mod._find_campaign, cls.mod._broadcast)
        cls.mod._find_campaign = lambda name: cls.dir

    @classmethod
    def tearDownClass(cls):
        cls.mod._active_campaign, cls.mod._find_campaign, cls.mod._broadcast = cls._orig

    def test_players_get_the_places_and_the_picture(self):
        d = _valley()
        rm.set_here(d, "Sarvenna")
        d["file"] = "region-abc.svg"
        rm.save(str(self.dir / "region.json"), d)
        self.mod._active_campaign = lambda: "demo"
        view = self.client.get("/region").get_json()
        self.assertEqual((view["file"], view["party"], len(view["places"])), ("region-abc.svg", "Sarvenna", 6))

    def test_no_campaign_or_a_broken_file_is_an_empty_map(self):
        self.mod._active_campaign = lambda: ""
        self.assertEqual(self.client.get("/region").get_json()["places"], [])
        (self.dir / "region.json").write_text("{broken", encoding="utf-8")
        self.mod._active_campaign = lambda: "demo"
        self.assertEqual(self.client.get("/region").get_json()["file"], "")

    def test_a_redraw_is_announced_to_every_screen_by_the_dm_only(self):
        sent = []
        self.mod._broadcast = sent.append
        self.assertEqual(self.client.post("/region", json={}).status_code, 204)
        self.assertEqual(list(sent[0]), ["region_changed"])
        self.assertIn("region_changed", self.mod._DM_ENDPOINTS)
        self.assertNotIn("region_get", self.mod._DM_ENDPOINTS)
        self.assertIn("region", self.mod._IMAGE_KINDS)

    def test_strings_exist_in_every_language(self):
        keys = ["region.title", "region.new", "region.hint", "region.here", "region.visited", "region.known",
                "region.days", "region.day1", "region.hours", "region.noroute"]
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
