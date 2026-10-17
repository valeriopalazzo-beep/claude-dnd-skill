"""
test_travel.py — the pace of the march, the roles on the road, getting lost.

display/travel.py keeps a journey in the campaign's travel.json: the DM sets
the pace (PHB 2014 p. 182), each player picks a role on the road from the
phone (guide, lookout, mapper, forager; only who is not busy keeps watch, -5
at a fast pace — p. 183), the guide's Survival check can get the party lost
(DMG 2014 p. 111) — a secret until the DM reveals it, then the region map
shows it — and foragers fill the supplies.

Run from repo root:
    python -m pytest tests/test_travel.py -q
"""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import random
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(SKILL / "scripts"))

import travel as tv  # noqa: E402
import region_map as rm  # noqa: E402

LYRA = """# Lyra
| STR | DEX | CON | INT | WIS | CHA |
|-----|-----|-----|-----|-----|-----|
| 10 (+0) | 14 (+2) | 12 (+1) | 10 (+0) | 16 (+3) | 8 (-1) |
- **Conditions:** none
- **Passive Perception:** 15
| Survival | WIS | +5 | ✓ |
"""
HERNIO = """# Hernio
| STR | DEX | CON | INT | WIS | CHA |
|-----|-----|-----|-----|-----|-----|
| 8 (-1) | 17 (+3) | 13 (+1) | 10 (+0) | 12 (+1) | 15 (+2) |
- **Conditions:** Indebolimento 1
| Perception | WIS | +3 | ✓ |
"""
PARTY = "Lyra, Hernio"      # runtime_paths keeps the first runtime folder it sees: name them


def _journey(*players, pace="normal"):
    data = tv.blank()
    tv.start(data, list(players or ("Lyra", "Hernio", "Ajeje")), pace, "Cripta", "foresta")
    return data


class RuleTests(unittest.TestCase):
    def test_paces_roles_and_terrains_in_both_languages(self):
        self.assertEqual((tv.pace_of("veloce"), tv.pace_of("Lento"), tv.pace_of("normal")), ("fast", "slow", "normal"))
        self.assertEqual((tv.role_of("guida"), tv.role_of("Vedetta"), tv.role_of("cartografo"), tv.role_of("foraggiatore")),
                         ("guide", "lookout", "mapper", "forager"))
        self.assertEqual((tv.terrain_dc("foresta"), tv.terrain_dc("colline"), tv.terrain_dc("prateria")), (15, 10, 5))
        for bad in (lambda: tv.pace_of("galoppo"), lambda: tv.role_of("cuoco"), lambda: tv.terrain_dc("luna")):
            with self.assertRaises(tv.TravelError):
                bad()

    def test_one_guide_and_everybody_told_once(self):
        data = _journey()
        self.assertEqual(tv.set_role(data, "lyra", "guida"), ("Lyra", False))
        with self.assertRaises(tv.TravelError):
            tv.set_role(data, "Hernio", "guide")
        self.assertEqual(tv.set_role(data, "Hernio", "vedetta"), ("Hernio", False))
        self.assertEqual(tv.set_role(data, "Ajeje", "forager"), ("Ajeje", True))
        self.assertIn("Lyra: guide", tv.ready_line(data))
        tv.set_role(data, "Lyra", "none")
        self.assertNotIn("Lyra", data["roles"])
        with self.assertRaises(tv.TravelError):
            tv.set_role(data, "Gandalf", "lookout")

    def test_only_who_is_not_busy_keeps_watch_and_a_fast_pace_costs_five(self):
        data = _journey()
        tv.set_role(data, "Lyra", "guide")
        tv.set_role(data, "Hernio", "lookout")
        passives = {"Lyra": 15, "Hernio": 13, "Ajeje": 11}
        w = tv.watch(data, passives)
        self.assertEqual((w["best"], w["by"], sorted(w["lookouts"])), (13, "Hernio", ["Ajeje", "Hernio"]))
        data["pace"] = "fast"
        self.assertEqual(tv.watch(data, passives)["best"], 8)
        tv.set_role(data, "Hernio", "mapper")
        tv.set_role(data, "Ajeje", "forager")
        self.assertIsNone(tv.watch(data, passives)["best"], "everybody busy: nobody watches")

    def test_the_navigation_dc(self):
        data = _journey()
        self.assertEqual(tv.navigation_dc(data), 15)
        self.assertEqual(tv.navigation_dc(data, terrain="deserto"), 10)
        self.assertEqual(tv.navigation_dc(data, dc=12), 12)
        data["terrain"] = ""
        with self.assertRaises(tv.TravelError):
            tv.navigation_dc(data)

    def test_lost_is_a_secret_until_revealed(self):
        data = _journey()
        hours = tv.get_lost(data, "Carbonaia", 100, random.Random(3))
        self.assertTrue(1 <= hours <= 6)
        self.assertEqual(data["lost"]["retry_h"], 100 + hours)
        self.assertFalse(tv.public_view(data, "Lyra")["lost"])
        self.assertTrue(tv.public_view(data, dm=True)["lost"])
        data["lost"]["known"] = True
        self.assertTrue(tv.public_view(data, "Lyra")["lost"])
        tv.found(data)
        self.assertIsNone(data["lost"])

    def test_foraging_finds_food_and_water_in_person_days(self):
        got = tv.forage_yield(3, random.Random(1))
        self.assertTrue(4 <= got["rations"] <= 9 and 4 <= got["water"] <= 9)
        self.assertEqual((got["kg"], got["litres"]), (got["rations"] * 0.5, got["water"] * 4))
        self.assertEqual(tv.forage_yield(-9, random.Random(1))["rations"], 0)

    def test_a_screen_sees_its_own_role(self):
        data = _journey()
        tv.set_role(data, "Hernio", "lookout")
        view = tv.public_view(data, "hernio", passives={"Hernio": 13})
        self.assertEqual((view["mine"], view["asked"], view["km_day"], view["watch"]["best"]), ("lookout", True, 36, 13))
        self.assertNotIn("lost_detail", view)
        self.assertIsNone(tv.public_view(tv.blank(), "Hernio"))


class RegionTests(unittest.TestCase):
    def _region(self):
        data = rm.empty()
        rm.add_place(data, "Vallombra", "village", "40,40")
        rm.add_place(data, "Carbonaia", "village", near="Vallombra", direction="W", days=1, road="trail")
        rm.set_here(data, "Vallombra")
        return data

    def test_a_party_that_knows_it_is_lost_is_nowhere(self):
        data = self._region()
        data["lost"] = {"from": "Vallombra", "known": False}
        view = rm.public_view(data)
        self.assertEqual((view["party"], view["lost"]), ("Vallombra", False))
        self.assertNotIn(">?</text>", rm.render(data))
        data["lost"]["known"] = True
        view = rm.public_view(data)
        self.assertEqual((view["party"], view["lost"]), ("", True))
        self.assertTrue(all(p["days"] is None and not p["here"] for p in view["places"]))
        self.assertTrue(0 < view["lost_at"]["px"] < 100 and 0 < view["lost_at"]["py"] < 100, "the phone centres on it")
        self.assertIn(">?</text>", rm.render(data))
        with self.assertRaises(rm.RegionError):
            rm.travel(data, "Carbonaia")
        rm.set_here(data, "Carbonaia")
        self.assertIsNone(data["lost"])


class CommandTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        chars = self.root / "campaigns" / "prova" / "characters"
        chars.mkdir(parents=True)
        (chars / "lyra.md").write_text(LYRA, encoding="utf-8")
        (chars / "hernio.md").write_text(HERNIO, encoding="utf-8")
        (self.root / "rt").mkdir()
        (self.root / "rt" / ".campaign").write_text("prova", encoding="utf-8")
        for var, value in (("DND_CAMPAIGN_ROOT", str(self.root)), ("DND_RUNTIME_DIR", str(self.root / "rt"))):
            old = os.environ.get(var)
            os.environ[var] = value
            self.addCleanup(lambda var=var, old=old: os.environ.pop(var, None) if old is None else os.environ.__setitem__(var, old))
        self.events = []
        saved = (tv._announce, tv._story, tv._ask, tv._active_campaign)
        tv._announce, tv._story = self.events.append, lambda line: self.events.append(line)
        tv._active_campaign = lambda: "prova"      # media keeps the first runtime folder it sees
        self.rolls = []
        tv._ask = lambda name, mod, adv, label, dc, timeout: (self.rolls.append((name, mod, adv, dc)) or self.next_total)
        self.next_total = 20
        self.addCleanup(lambda: (setattr(tv, "_announce", saved[0]), setattr(tv, "_story", saved[1]), setattr(tv, "_ask", saved[2]),
                                 setattr(tv, "_active_campaign", saved[3])))

    def run_tv(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = tv.main(["--campaign", "prova", *argv])
        return code, out.getvalue() + err.getvalue()

    def saved(self):
        return tv.load(str(self.root / "campaigns" / "prova" / "travel.json"))

    def test_a_journey_from_start_to_lost_and_found(self):
        self.assertEqual(self.run_tv("start", "--only", PARTY, "--pace", "lento", "--to", "Cripta", "--terrain", "foresta")[0], 0)
        self.assertEqual(self.events[-1], {"what": "start", "pace": "slow"})
        self.assertEqual(self.run_tv("navigate")[0], 1, "no guide yet")
        self.run_tv("role", "Lyra", "guida")
        code, out = self.run_tv("navigate", "--map")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.rolls[-1], ("Lyra", 10, "advantage", 15), "Survival +5, +5 at a slow pace, a map")
        self.assertIn("on course", out)
        self.run_tv("pace", "fast")
        self.next_total = 4
        code, out = self.run_tv("navigate")
        self.assertIn("LOST", out)
        self.assertEqual(self.rolls[-1][1], 0, "+5 Survival, -5 at a fast pace")
        lost = self.saved()["lost"]
        self.assertFalse(lost["known"])
        self.assertEqual(self.run_tv("reveal")[0], 0)
        self.assertTrue(self.saved()["lost"]["known"])
        self.assertTrue(any(isinstance(e, str) and "lost" in e.lower() for e in self.events), "said in the story")
        self.next_total = 18
        code, out = self.run_tv("navigate")
        self.assertIn("back on course", out)
        self.assertIsNone(self.saved()["lost"])

    def test_hernio_rolls_with_disadvantage_when_exhausted(self):
        self.run_tv("start", "--only", PARTY, "--terrain", "hills")
        self.run_tv("role", "Hernio", "guide")
        self.run_tv("navigate")
        self.assertEqual(self.rolls[-1], ("Hernio", 1, "disadvantage", 10))

    def test_foraging_fills_the_supplies_but_not_at_a_fast_pace(self):
        import supplies
        supplies.save(supplies.data_path("prova"), supplies.blank())
        saved = supplies._announce
        supplies._announce = lambda data, news=None: None
        self.addCleanup(lambda: setattr(supplies, "_announce", saved))
        self.run_tv("start", "--only", PARTY, "--pace", "fast")
        self.run_tv("role", "Lyra", "forager")
        self.assertEqual(self.run_tv("forage", "--dc", "limited")[0], 1)
        self.run_tv("pace", "normale")
        code, out = self.run_tv("forage", "--dc", "limitate")
        self.assertEqual(code, 0, out)
        stock = supplies.load(supplies.data_path("prova"))["stock"]
        self.assertTrue(4 <= stock["rations"] <= 9 and 4 <= stock["water"] <= 9, stock)

    def test_the_watch_and_the_calendar(self):
        self.run_tv("start", "--only", PARTY)
        self.run_tv("role", "Lyra", "mapper")
        code, out = self.run_tv("watch")
        self.assertIn("Hernio: passive Perception 13", out)
        self.assertNotIn("Lyra", out)
        data = self.saved()
        tv.get_lost(data, "Vallombra", 50, random.Random(2))
        tv.save(str(self.root / "campaigns" / "prova" / "travel.json"), data)
        self.assertEqual(tv.tick("prova", 50), [])
        self.assertIn("may try again", tv.tick("prova", 56)[0])
        self.assertEqual(tv.tick("prova", 57), [], "said once")


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_travel", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.mod
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.camp = pathlib.Path(tmp.name)
        m = self.mod
        saved = (m._active_campaign, m._find_campaign, m._broadcast, m._dm_sheet_numbers, tv.notes_add)
        self.addCleanup(lambda: (setattr(m, "_active_campaign", saved[0]), setattr(m, "_find_campaign", saved[1]),
                                 setattr(m, "_broadcast", saved[2]), setattr(m, "_dm_sheet_numbers", saved[3]),
                                 setattr(tv, "notes_add", saved[4])))
        self.sent, self.notes = [], []
        m._active_campaign, m._find_campaign, m._broadcast = (lambda: "prova"), (lambda n: self.camp), self.sent.append
        m._dm_sheet_numbers = lambda n: {"passive": {"perception": {"Lyra": 15, "Hernio": 13}.get(n, 10)}}
        tv.notes_add = self.notes.append

    def test_players_pick_their_roles_and_the_dm_is_told(self):
        self.assertIsNone(self.client.get("/travel").get_json()["travel"])
        data = _journey("Lyra", "Hernio")
        tv.get_lost(data, "Vallombra", None, random.Random(1))
        tv.save(str(self.camp / "travel.json"), data)
        r = self.client.post("/travel/role", json={"character": "Lyra", "role": "guide"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["travel"]["mine"], "guide")
        self.assertFalse(r.get_json()["travel"]["lost"], "the players do not know")
        r = self.client.post("/travel/role", json={"character": "Hernio", "role": "guide"})
        self.assertEqual((r.status_code, r.get_json()["code"]), (409, "taken"))
        self.client.post("/travel/role", json={"character": "Hernio", "role": "lookout"})
        self.assertEqual(len(self.notes), 1)
        self.assertIn("[[MARCH:", self.notes[0])
        self.assertEqual(self.sent[-1], {"travel_event": {"what": "role", "character": "Hernio"}})
        view = self.client.get("/travel?character=Hernio").get_json()["travel"]
        self.assertEqual(view["watch"]["best"], 13)
        dm = self.client.get("/dm-screen/data").get_json()["travel"]
        self.assertTrue(dm["lost"] and dm["lost_detail"]["from"] == "Vallombra")
        self.assertIn("travel_changed", self.mod._DM_ENDPOINTS)

    def test_the_script_tells_every_screen(self):
        r = self.client.post("/travel", json={"event": {"what": "pace", "pace": "slow"}})
        self.assertEqual(r.status_code, 204)
        self.assertEqual(self.sent[-1], {"travel_event": {"what": "pace", "pace": "slow"}})


class PageTests(unittest.TestCase):
    def test_the_page_has_the_panel(self):
        html = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")
        for needle in ('id="trv-modal"', 'id="ln-travel"', 'id="ph-travel-opt"', "window._onTravel(", "region.lost"):
            self.assertIn(needle, html)
        self.assertIn('id="s-travel"', (DISPLAY / "templates" / "dm.html").read_text(encoding="utf-8"))

    def test_strings_exist_in_every_language(self):
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("trv.title", "trv.pace.fast", "trv.role.guide", "trv.desc.forager", "trv.lost", "region.lost",
                        "dms.travel"):
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
