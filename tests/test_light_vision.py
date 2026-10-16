"""
test_light_vision.py — light and darkvision on the battle map (PHB 2014 pp. 183-185).

With a `light:` line, display/map_render.py works out how lit every square is
(the place's light, raised by torches, lanterns, fires and light spells that
reach it in a straight line), shades what is dim or dark, and — with fog on —
shows only what the party's light and eyes reach. The PCs' darkvision comes
from their sheets, the torches burning in supplies.json move with whoever
holds them, and the DM is told who sees whom.

Run from repo root:
    python -m pytest tests/test_light_vision.py -q
"""
import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(SKILL / "scripts"))

import map_render as mr  # noqa: E402
import supplies  # noqa: E402

HALL = """title: Corridoio
light: dark
---
A..............g
---
A: Aldric | pc
g: Goblin | foe
light A torch
"""


class ParseTests(unittest.TestCase):
    def test_the_header_and_the_legend_lines(self):
        spec = mr.parse(HALL)
        self.assertEqual(spec.light, "dark")
        src = spec.lights[0]
        self.assertEqual((src.x, src.y, src.token, src.kind, src.bright, src.dim), (0, 0, "A", "torch", 4, 4))
        it = mr.parse(HALL.replace("light: dark", "luce: fioca").replace("light A torch", "luce 3,1 lanterna schermabile"))
        self.assertEqual(it.light, "dim")
        self.assertEqual((it.lights[0].x, it.lights[0].kind, it.lights[0].bright), (2, "lantern", 6))
        self.assertEqual(mr.parse(HALL.replace("light: dark\n", "")).light, "")

    def test_sizes_cones_and_mistakes(self):
        spec = mr.parse(HALL.replace("light A torch", "light 9,1 6m/3m\nlight A bullseye E\nlight 2,1 candela\n"
                                                      "light Z torch\nlight 99,1 torch\nlight A lume strano"))
        sizes = [(s.kind, s.bright, s.dim, s.direction) for s in spec.lights]
        self.assertEqual(sizes[0], ("custom", 4, 2, None))
        self.assertEqual(sizes[1], ("bullseye", 12, 12, (1, 0)))
        self.assertEqual(sizes[2], ("candle", 1, 1, None))
        self.assertEqual(sizes[3][0], "torch", "an unknown kind is a torch, and said")
        self.assertEqual(len(sizes), 4, "a light carried by nobody on the map and one off the map are left out")
        text = " ".join(spec.warnings)
        for needle in ("'Z'", "outside the map", "lume-strano"):
            self.assertIn(needle, text)

    def test_senses_after_the_side_and_without_it(self):
        spec = mr.parse(HALL.replace("A: Aldric | pc", "A: Aldric | pc | darkvision 18m, blindsight 3m")
                        .replace("g: Goblin | foe", "g: Goblin | scurovisione"))
        self.assertEqual(spec.legend["A"], ("Aldric", "pc"))
        self.assertEqual((spec.vision["A"].dark, spec.vision["A"].blind), (12, 2))
        self.assertEqual((spec.legend["g"], spec.vision["g"].dark), (("Goblin", "foe"), 12), "no range: 60 ft")
        odd = mr.parse(HALL.replace("g: Goblin | foe", "g: Goblin | foe | occhi di gatto"))
        self.assertNotIn("g", odd.vision)
        self.assertTrue(any("occhi di gatto" in w for w in odd.warnings))

    def test_lights_without_a_light_line_are_flagged(self):
        spec = mr.parse(HALL.replace("light: dark\n", ""))
        self.assertTrue(any("no `light:` line" in w for w in spec.warnings))
        self.assertEqual(mr.light_levels(spec), {})


class LightTests(unittest.TestCase):
    def test_a_torch_is_bright_then_dim_then_dark(self):
        level = mr.light_levels(mr.parse(HALL))
        self.assertEqual([level[(x, 0)] for x in range(10)], [2, 2, 2, 2, 2, 1, 1, 1, 1, 0])

    def test_walls_stop_light_and_take_the_light_of_their_room(self):
        room = "light: dark\n---\n#####\n#A#.#\n#####\n---\nA: Aldric | pc\nlight A torch\n"
        level = mr.light_levels(mr.parse(room))
        self.assertEqual((level[(1, 1)], level[(3, 1)]), (2, 0), "behind the wall it stays dark")
        self.assertEqual((level[(0, 0)], level[(2, 1)]), (2, 2), "the walls of the lit room are lit")

    def test_dim_ambient_a_fire_square_and_magical_darkness(self):
        spec = mr.parse("light: dim\n---\n..!......\n.........\n---\n@ 8,1 sphere 5ft dark | Oscurità\n")
        level = mr.light_levels(spec)
        self.assertEqual((level[(2, 0)], level[(4, 0)], level[(5, 0)], level[(6, 1)]), (2, 2, 1, 1))
        self.assertEqual(level[(8, 0)], -1, "the Darkness spell is magical darkness even in dim light")
        self.assertEqual(mr.light_levels(mr.parse("light: bright\n---\n.....\n"))[(4, 0)], 2)

    def test_a_bullseye_lights_a_cone(self):
        spec = mr.parse("light: dark\n---\n" + "\n".join(["." * 12] * 5) + "\n---\nlight 1,3 bullseye E\n")
        level = mr.light_levels(spec)
        self.assertEqual(level[(6, 2)], 2)
        self.assertEqual(level[(0, 0)], 0, "behind the lantern it is dark")


class SeeingTests(unittest.TestCase):
    def test_what_eyes_make_of_each_light(self):
        V = mr.Vision
        self.assertEqual(mr.sees(None, 3, 2), mr.CLEAR)
        self.assertEqual(mr.sees(None, 3, 1), mr.DIM_SEEN)
        self.assertIsNone(mr.sees(None, 3, 0))
        self.assertEqual(mr.sees(V(dark=12), 3, 1), mr.CLEAR, "darkvision: dim as bright")
        self.assertEqual(mr.sees(V(dark=12), 3, 0), mr.GREY_SEEN, "darkvision: dark as dim, in grey")
        self.assertIsNone(mr.sees(V(dark=2), 3, 0), "beyond its range darkvision does nothing")
        self.assertIsNone(mr.sees(V(dark=12), 3, -1), "magical darkness stops darkvision")
        self.assertEqual(mr.sees(V(devil=24), 3, -1), mr.CLEAR)
        self.assertEqual(mr.sees(V(true=24), 3, 0), mr.CLEAR)
        self.assertEqual(mr.sees(V(blind=2), 1, -1), mr.SENSED)

    def test_fog_shows_what_the_light_and_the_eyes_reach(self):
        text = HALL.replace("light A torch", "").replace("title: Corridoio", "title: Corridoio\nfog: on")
        self.assertEqual(mr.visible_cells(mr.parse(text)), {(0, 0)}, "in the dark, only where you stand")
        lit = mr.visible_cells(mr.parse(text + "light A torch\n"))
        self.assertEqual(sorted(x for x, _ in lit), list(range(9)))
        dv = mr.parse(text.replace("A: Aldric | pc", "A: Aldric | pc | darkvision 18m"))
        view = mr.party_view(dv, mr.light_levels(dv))
        self.assertEqual((view[(12, 0)], (13, 0) in view), (mr.GREY_SEEN, False))
        far = mr.parse(text + "light 16,1 torch\n")
        self.assertIn((15, 0), mr.visible_cells(far), "a lit place is seen from afar")

    def test_the_dm_is_told_who_sees_whom(self):
        spec = mr.parse(HALL.replace("A: Aldric | pc", "A: Aldric | pc | darkvision 18m")
                        .replace("A..............g", "A.........g....h")
                        .replace("g: Goblin | foe", "g: Goblin | foe | darkvision 60ft\nh: Hobgoblin | foe"))
        lines = mr.sight_report(spec, mr.light_levels(spec))
        self.assertIn("Aldric (A, in bright light, darkvision 60 ft) sees: g (darkvision, grey); does not see: h", lines)
        self.assertIn("Goblin (g, in darkness, darkvision 60 ft) sees: A", lines)
        self.assertIn("Hobgoblin (h, in darkness) sees: A", lines, "a torch is seen from afar")
        self.assertTrue(any("no stat block found for h" in l for l in lines))
        self.assertEqual(mr.sight_report(spec, {}), [])


class DrawingTests(unittest.TestCase):
    def test_shades_markers_and_the_key(self):
        spec = mr.parse(HALL.replace("light A torch", "light A torch\nlight 12,1 candle"))
        svg = mr.render_svg(spec, lang="it")
        self.assertIn('class="light"', svg)
        self.assertIn("luce fioca", svg)
        self.assertEqual(svg.count("✹"), 2, "a flame on Aldric, one on the candle")
        self.assertNotIn("scurovisione", svg, "the darkvision swatch is for fog maps")
        plain = mr.render_svg(mr.parse(HALL.replace("light: dark\n", "")))
        self.assertNotIn('class="light"', plain)
        self.assertNotIn("bright light", plain)

    def test_with_fog_the_unseen_is_left_out(self):
        text = HALL.replace("title: Corridoio", "title: Corridoio\nfog: on")
        spec = mr.parse(text)
        seen = mr.visible_cells(spec)
        svg = mr.render_svg(spec, fog=(seen, seen), lang="en")
        self.assertNotIn(">g<", svg, "the goblin in the dark is not drawn")
        self.assertIn("darkvision", svg)


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._old = os.environ.get("DND_CAMPAIGN_ROOT")
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        self.camp = self.root / "campaigns" / "prova"
        (self.camp / "characters").mkdir(parents=True)
        (self.camp / "characters" / "lyra.md").write_text(
            "# Lyra Ombrafina\n\n- **Scurovisione:** Vede in luce fioca entro 18 m come se fosse luce intensa.\n", encoding="utf-8")
        (self.camp / "characters" / "vardamir.md").write_text(
            "# Vardamir ma Feyn\n\n- **Scurovisione superiore:** Vedi al buio entro 36 m come se fosse luce soffusa\n", encoding="utf-8")
        (self.camp / "characters" / "hernio.md").write_text("# Hernio\n\nUmano.\n", encoding="utf-8")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("DND_CAMPAIGN_ROOT", None)
        else:
            os.environ["DND_CAMPAIGN_ROOT"] = self._old
        self._tmp.cleanup()

    def test_darkvision_comes_from_the_sheets(self):
        self.assertEqual(mr.sheet_senses("**Darkvision.** You can see in dim light within 60 feet", 0.2).dark, 12)
        spec = mr.parse("light: dark\n---\nLVH\n---\nL: Lyra | pc\nV: Vardamir | pc | blindsight 3m\nH: Hernio | pc\n")
        self.assertEqual(mr.add_sheet_senses(spec, "prova"), ["L"])
        self.assertEqual(spec.vision["L"].dark, 12)
        self.assertEqual((spec.vision["V"].dark, spec.vision["V"].blind), (0, 2), "what the legend says wins")
        self.assertNotIn("H", spec.vision)

    def test_the_torches_in_the_supplies_go_with_whoever_holds_them(self):
        data = supplies.blank()
        data["stock"]["torches"] = 3
        data["stock"]["oil"] = 1
        supplies.light(data, "torcia", by="Lyra")
        supplies.light(data, "lantern")
        supplies.save(str(self.camp / "supplies.json"), data)
        spec = mr.parse("light: dark\n---\n..L..H\n---\nL: Lyra Ombrafina | pc\nH: Hernio | pc\n")
        news = mr.add_supply_lights(spec, "prova")
        self.assertEqual([(s.token, s.kind, s.x) for s in spec.lights], [("L", "torch", 2)])
        self.assertTrue(any("nobody carries it" in n for n in news))
        supplies.carry(data, "lanterna", "Hernio")
        supplies.save(str(self.camp / "supplies.json"), data)
        spec = mr.parse("light: dark\n---\n..L..H\n---\nL: Lyra Ombrafina | pc\nH: Hernio | pc\nlight L torch\n")
        self.assertEqual(mr.add_supply_lights(spec, "prova"), [])
        self.assertEqual(sorted((s.token, s.kind) for s in spec.lights), [("H", "lantern"), ("L", "torch")],
                         "a torch the legend already gives Lyra is not doubled")

    def test_supplies_say_who_holds_a_light(self):
        data = supplies.blank()
        data["stock"]["torches"] = 2
        supplies.light(data, "torch", by="  Lyra  ")
        self.assertEqual(data["lights"][0]["by"], "Lyra")
        self.assertIn("lit: torch (Lyra)", supplies.describe(data))
        self.assertEqual(supplies.public_view(data)["lights"][0]["by"], "Lyra")
        supplies.light(data, "torch")
        self.assertEqual(supplies.carry(data, "torch", "Hernio")["by"], "Hernio", "the one nobody holds first")
        for bad in (("torch", ""), ("candle", "Lyra")):
            with self.assertRaises(supplies.SupplyError):
                supplies.carry(data, *bad)

    def test_the_command_line_reports_sight(self):
        out = self.root / "m.svg"
        err = io.StringIO()
        text = "title: T\nlight: dark\n---\nL...g\n---\nL: Lyra | pc\ng: Goblin | foe\n"
        import sys as _sys
        old = _sys.argv, _sys.stdin
        try:
            _sys.argv = ["map_render.py", "--campaign", "prova", "--out", str(out)]
            _sys.stdin = io.StringIO(text)
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mr.main(), 0)
        finally:
            _sys.argv, _sys.stdin = old
        self.assertIn("map_render: sight — Lyra (L, in darkness, darkvision 60 ft) sees: g (darkvision, grey)", err.getvalue())
        self.assertIn("darkness", out.read_text(encoding="utf-8"), "the key, in the campaign's language (English here)")
        self.assertIn("map_render: senses — g = Goblin (SRD): darkvision 60 ft", err.getvalue())


class BookSensesTests(unittest.TestCase):
    """A creature's senses come from its stat block: the SRD, then the DM's Monster Manual notes."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._old = os.environ.get("DND_CAMPAIGN_ROOT")
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)       # no notes here unless a test writes them

    def tearDown(self):
        if self._old is None:
            os.environ.pop("DND_CAMPAIGN_ROOT", None)
        else:
            os.environ["DND_CAMPAIGN_ROOT"] = self._old
        self._tmp.cleanup()

    def test_srd_creatures_by_their_english_or_italian_name(self):
        spec = mr.parse("light: dark\n---\nA.g.w.k.x.y\n---\nA: Aldric | pc\ng: Goblin arciere 2 | foe\n"
                        "w: Lupo crudele | foe\nk: Ser Bertrand | ally | come Cavaliere\nx: Grak il Rosso | foe\n"
                        "y: Ombra | foe | none\n")
        lines = mr.add_book_senses(spec)
        self.assertIn("g = Goblin (SRD): darkvision 60 ft", lines)
        self.assertIn("w = Dire Wolf (SRD): no darkvision", lines, "not in its stat block: it has none")
        self.assertIn("k = Knight (SRD): no darkvision", lines)
        self.assertEqual(spec.vision["g"].dark, 12)
        self.assertFalse(spec.vision["w"])
        self.assertNotIn("x", spec.vision, "nowhere in the books: left to the DM")
        self.assertIn("y", spec.vision, "`| none` is a decision, not a gap")
        report = mr.sight_report(spec, mr.light_levels(spec))
        self.assertTrue(any(l.startswith("no stat block found for x —") for l in report))
        self.assertEqual(mr.srd_senses("Goblinoide"), None, "only an exact name, never a guess")

    def test_the_monster_manual_notes_when_they_are_on_this_pc(self):
        self.assertIsNone(mr.mm_senses("Belva Distorcente"))
        notes = self.root / "riferimenti" / "mm"
        notes.mkdir(parents=True)
        (notes / "INDICE.md").write_text("| Nome italiano | Nome inglese | GS | Tipo | Pagina | File |\n|---|---|---|---|---|---|\n"
                                         "| Belva Distorcente | Displacer Beast | 3 | Mostruosità | 31 | [displacer-beast.md](displacer-beast.md) |\n"
                                         "| Senza Sensi | Senseless | 1 | Prova | 1 | [senseless.md](senseless.md) |\n", encoding="utf-8")
        (notes / "displacer-beast.md").write_text("# Belva\n- **Sensi** scurovisione 18 m, Percezione passiva 11 · **Linguaggi** —\n",
                                                  encoding="utf-8")
        (notes / "senseless.md").write_text("# Senza\nSensi come un altro.\n", encoding="utf-8")
        spec = mr.parse("light: dark\n---\nA.d.z\n---\nA: Aldric | pc\nd: Displacer Beast | foe\nz: Senza sensi | foe\n")
        lines = mr.add_book_senses(spec)
        self.assertIn("d = Belva Distorcente (Monster Manual notes): darkvision 60 ft", lines)
        self.assertTrue(any(l.startswith("z = Senza Sensi (Monster Manual notes): the note has no Sensi line") for l in lines))
        self.assertNotIn("z", spec.vision)

    def test_the_dataset_and_lookup_carry_the_senses(self):
        sys.path.insert(0, str(SKILL / "scripts"))
        import build_srd
        import lookup
        self.assertEqual(build_srd._senses_5ebits({"darkvision": "60 ft.", "blindsight": "10 ft.", "passive_perception": 9}),
                         "darkvision 60 ft., blindsight 10 ft., passive Perception 9")
        self.assertEqual(build_srd._senses_5ebits(None), "")
        self.assertIn("Senses: darkvision 60 ft., passive Perception 9", lookup.lookup("goblin", "monsters"))
        self.assertEqual(lookup._senses_text("darkvision 60 ft., passive Perception 9", lookup.LABELS["it"]),
                         "scurovisione 18 m, Percezione passiva 9")
        with_dv = [m for m in json.loads((SKILL / "data" / "dnd5e_srd.json").read_text(encoding="utf-8"))["monsters"]
                   if "darkvision" in m.get("senses", "")]
        self.assertEqual(len(with_dv), 181)


if __name__ == "__main__":
    unittest.main()
