"""
test_companions.py — small sheets for the animals and followers with the party.

display/companion.py keeps mounts, familiars, pets and allies in
<campaign>/companions.json: hit points, Armour Class, speed, attacks, traits,
optionally copied from an SRD creature. On the display each one is a chip on
its owner's card and has a small sheet; they are never players.

Run from repo root:
    python -m pytest tests/test_companions.py -q
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
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"
HTML = DISPLAY / "templates" / "index.html"
NODE = shutil.which("node")
sys.path.insert(0, str(DISPLAY))

import companion as cp  # noqa: E402

HORSE = {"name": "Riding Horse", "index": "riding-horse", "ac": 10, "hp": 13, "speed": "walk 60 ft.",
         "description": "Action — Hooves: Melee Weapon Attack: +5 to hit. Hit: 8 (2d4 + 3) bludgeoning damage."}
RAVEN = {"name": "Raven", "index": "raven", "ac": 12, "hp": 1, "speed": "walk 10 ft., fly 50 ft.",
         "description": "Mimicry: The raven can mimic simple sounds.\n\nAction — Beak: Melee Weapon Attack: +4 to hit. Hit: 1 piercing damage."}


class CompanionTests(unittest.TestCase):
    def test_an_srd_creature_gives_numbers_attacks_and_traits(self):
        base = cp.from_record(RAVEN)
        self.assertEqual((base["species"], base["ac"], base["hp"], base["speed"]), ("Raven", 12, 1, "walk 10 ft., fly 50 ft."))
        self.assertEqual(base["attacks"], ["Beak: Melee Weapon Attack: +4 to hit. Hit: 1 piercing damage."])
        self.assertEqual(base["traits"], ["Mimicry: The raven can mimic simple sounds."])
        self.assertEqual(cp.from_record(HORSE)["traits"], [])

    def test_adding_one_by_copy_or_by_hand(self):
        party = []
        horse = cp.add(party, "Fulmine", "cavalcatura", "Lyra", cp.from_record(HORSE))
        self.assertEqual((horse["kind"], horse["owner"], horse["species"], horse["hp"], horse["ac"]),
                         ("mount", "Lyra", "Riding Horse", {"current": 13, "max": 13}, 10))
        guard = cp.add(party, "Ser Bertrand", "alleato", hp=27, ac=16, speed="9 m",
                       attacks=["Spada lunga: +5, 1d8+3"], traits=["Non abbandona mai un ferito"])
        self.assertEqual((guard["kind"], guard["owner"], guard["species"], guard["hp"]["max"]), ("ally", "", "", 27))
        tough = cp.add(party, "Brutus", "pet", "Lio", cp.from_record(HORSE), hp=20, attacks=["Morso: +4, 1d6+2"])
        self.assertEqual((tough["hp"]["max"], tough["attacks"]), (20, ["Morso: +4, 1d6+2"]))   # what is given wins

    def test_what_is_not_a_companion_is_refused(self):
        party = []
        cp.add(party, "Fulmine", "mount", hp=13)
        for args, kwargs in ((("",), {"hp": 5}), (("fulmine",), {"hp": 5}), (("Senza PF",), {}),
                             (("Strano",), {"kind": "drago", "hp": 5}), (("Zero",), {"hp": 0})):
            with self.assertRaises(cp.CompanionError):
                cp.add(party, *args, **kwargs)
        self.assertEqual(len(party), 1)

    def test_hit_points_go_down_up_and_stay_in_range(self):
        party = []
        horse = cp.add(party, "Fulmine", "mount", hp=13)
        self.assertEqual((cp.change_hp(horse, "-5"), horse["hp"]["current"]), (-5, 8))
        self.assertEqual((cp.change_hp(horse, "+3"), horse["hp"]["current"]), (3, 11))
        self.assertEqual((cp.change_hp(horse, "+99"), horse["hp"]["current"]), (2, 13))
        self.assertEqual((cp.change_hp(horse, "-99"), horse["hp"]["current"]), (-13, 0))
        self.assertEqual((cp.change_hp(horse, "=9"), horse["hp"]["current"]), (9, 9))
        self.assertEqual((cp.change_hp(horse, "full"), horse["hp"]["current"]), (4, 13))
        with self.assertRaises(cp.CompanionError):
            cp.change_hp(horse, "tanti")

    def test_fields_can_be_changed_afterwards(self):
        party = []
        horse = cp.add(party, "Fulmine", "mount", "Lyra", hp=13)
        cp.add(party, "Nebbia", "familiar", hp=1)
        cp.change_hp(horse, "-2")
        changed = cp.update(horse, party, owner="", ac=12, max_hp=9, note="ferrato di fresco", kind=None, speed=None)
        self.assertEqual(sorted(changed), ["ac", "hp", "note", "owner"])
        self.assertEqual((horse["owner"], horse["ac"], horse["hp"], horse["note"]),
                         ("", 12, {"current": 9, "max": 9}, "ferrato di fresco"))
        self.assertEqual(cp.update(horse, party, ac=12), [])
        with self.assertRaises(cp.CompanionError):
            cp.update(horse, party, name="nebbia")

    def test_the_players_get_every_companion_without_bookkeeping_fields(self):
        party = []
        cp.add(party, "Fulmine", "mount", "Lyra", cp.from_record(HORSE))
        view = cp.public_view(party)
        self.assertEqual(set(view[0]), {"id", "name", "kind", "owner", "species", "hp", "ac", "speed", "attacks", "traits", "note"})


class CommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._old = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_LANG")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        os.environ.pop("DND_LANG", None)
        camp = self.root / "campaigns" / "prova"
        camp.mkdir(parents=True)
        (camp / "state.md").write_text("# Prova\n**Ruleset:** 2014\n\n## Session Flags\n- language: it\n", encoding="utf-8")
        self.events = []
        self._announce = cp._announce
        cp._announce = self.events.append

    def tearDown(self):
        cp._announce = self._announce
        for key, old in self._old.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        self._tmp.cleanup()

    def run_cp(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cp.main(["--campaign", "prova", *argv])
        return code, out.getvalue() + err.getvalue()

    def saved(self):
        return cp.load(str(self.root / "campaigns" / "prova" / "companions.json"))

    def test_from_the_real_srd_in_the_campaign_language(self):
        code, out = self.run_cp("add", "Fulmine", "--owner", "Lyra", "--kind", "mount", "--from", "cavallo da sella")
        self.assertEqual(code, 0, out)
        horse = self.saved()[0]
        self.assertEqual((horse["species"], horse["ac"], horse["hp"]["max"]), ("Cavallo da Sella", 10, 13))
        self.assertTrue(horse["attacks"][0].startswith("Zoccoli:"))
        self.assertEqual(self.events[-1], {"name": "Fulmine", "what": "new"})

    def test_damage_written_with_a_minus_is_not_taken_for_an_option(self):
        self.run_cp("add", "Fulmine", "--hp", "13")
        code, out = self.run_cp("hp", "Fulmine", "-5")
        self.assertEqual(code, 0, out)
        self.assertIn("HP 8/13", out)
        self.assertEqual(self.events[-1], {"name": "Fulmine", "what": "hp", "delta": -5, "current": 8, "max": 13})
        code, out = self.run_cp("hp", "fulmine", "-20")
        self.assertIn("DOWN", out)
        self.run_cp("hp", "fulmine", "=0")
        self.assertIsNone(self.events[-1])                    # no change, nothing announced

    def test_set_remove_list_and_show(self):
        self.run_cp("add", "Nebbia", "--kind", "famiglio", "--hp", "1", "--owner", "Vardamir")
        self.assertEqual(self.run_cp("set", "nebbia", "--owner", "", "--note", "libera")[0], 0)
        self.assertEqual((self.saved()[0]["owner"], self.saved()[0]["note"]), ("", "libera"))
        self.assertIn("with the party", self.run_cp("show", "nebbia")[1])
        self.assertIn("Nebbia — familiar", self.run_cp("list")[1])
        self.assertEqual(self.run_cp("remove", "nebbia")[0], 0)
        self.assertEqual(self.saved(), [])

    def test_a_mistake_changes_nothing(self):
        self.run_cp("add", "Fulmine", "--hp", "13")
        before, announced = self.saved(), len(self.events)
        for argv in (("add", "Fulmine", "--hp", "3"), ("add", "Altro"), ("add", "Altro", "--from", "creatura inesistente"),
                     ("hp", "fulmine", "boh"), ("hp", "nessuno", "+1"), ("set", "fulmine"), ("remove", "nessuno")):
            code, out = self.run_cp(*argv)
            self.assertEqual(code, 1, argv)
            self.assertIn("Nothing was changed", out)
        self.assertEqual(self.saved(), before)
        self.assertEqual(len(self.events), announced)


class ServerTests(unittest.TestCase):
    def test_every_screen_reads_them_and_only_the_dm_writes(self):
        spec = importlib.util.spec_from_file_location("dnd_display_app_companions", str(DISPLAY / "dnd-display-app.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod._token_ok = lambda: True
        client = mod.app.test_client()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        camp = pathlib.Path(tmp.name)
        party = []
        cp.add(party, "Fulmine", "mount", "Lyra", cp.from_record(HORSE))
        cp.save(str(camp / "companions.json"), party)
        sent = []
        mod._active_campaign, mod._find_campaign, mod._broadcast = (lambda: "prova"), (lambda n: camp), sent.append
        self.assertEqual(client.get("/companions").get_json()[0]["name"], "Fulmine")
        event = {"name": "Fulmine", "what": "hp", "delta": -5, "current": 8, "max": 13, "extra": 1}
        self.assertEqual(client.post("/companions", json={"event": event}).status_code, 204)
        self.assertEqual(sent[-1]["companion_event"], {"name": "Fulmine", "what": "hp", "delta": -5, "current": 8, "max": 13})
        self.assertEqual(sent[-1]["companions"][0]["owner"], "Lyra")
        self.assertIn("companions_changed", mod._DM_ENDPOINTS)
        self.assertNotIn("companions_get", mod._DM_ENDPOINTS)
        mod._active_campaign = lambda: ""
        self.assertEqual(client.get("/companions").get_json(), [])


_SLICE = re.compile(r"(function _compChips\(companions, ownerName\).*?)\n// ── end companion chips ──", re.S)


class PageTests(unittest.TestCase):
    def test_a_card_gets_the_chips_of_its_own_companions(self):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the companion-chips block is no longer where this test slices it")
        party = [{"id": "a", "name": "Fulmine", "kind": "mount", "owner": "Lyra", "hp": {"current": 13, "max": 13}},
                 {"id": "b", "name": "Nebbia", "kind": "familiar", "owner": "lyra ", "hp": {"current": 0, "max": 1}},
                 {"id": "c", "name": "Brutus", "kind": "pet", "owner": "Lio", "hp": {"current": 3, "max": 7}},
                 {"id": "d", "name": "Ser Bertrand", "kind": "ally", "owner": "", "hp": {"current": 27, "max": 27}}]
        script = m.group(1) + "\nconsole.log(JSON.stringify(['Lyra', 'Lio', 'Aldric', ''].map(n => _compChips(%s, n))));\n" % json.dumps(party)
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            lyra, lio, aldric, nobody = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        self.assertEqual([(c["name"], c["hp"], c["state"]) for c in lyra], [("Fulmine", "13/13", ""), ("Nebbia", "0/1", "down")])
        self.assertEqual([(c["name"], c["state"]) for c in lio], [("Brutus", "hurt")])
        self.assertEqual((aldric, nobody), ([], []))            # the party's own are in the panel, on nobody's card

    def test_the_page_has_the_panel_the_chips_and_every_way_in(self):
        src = HTML.read_text(encoding="utf-8")
        for needle in ('id="comp-modal"', 'id="ln-comp"', 'data-ln-proxy="ln-comp"', 'id="ph-comp-opt"',
                       "window._onCompanionCards()", "(payload.companions || payload.companion_event)"):
            self.assertIn(needle, src)

    def test_strings_exist_in_every_language(self):
        keys = ["comp.title", "comp.empty", "comp.owner", "comp.party", "comp.speed", "comp.attacks", "comp.traits",
                "comp.down", "comp.toastNew", "comp.toastDown"] + ["comp.kind." + k for k in cp.KINDS]
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
