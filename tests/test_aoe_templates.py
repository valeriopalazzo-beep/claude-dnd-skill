"""
test_aoe_templates.py — spell areas drawn on the battle map, and who is in them.

`@ x,y sphere|cone|line|cube <size> [direction] [kind] | label` in the legend
section of a display/map_render.py map draws a 5e area of effect: the squares
whose centre falls inside are tinted, the shape is outlined, the tokens caught
are listed on the map and printed for the DM. Sizes in feet, metres or squares.

Run from repo root:
    python -m pytest tests/test_aoe_templates.py -q
"""
import contextlib
import io
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
sys.path.insert(0, str(SKILL / "display"))

import map_render as mr  # noqa: E402

FIELD = """title: Prova aree
scale: 1 quadretto = 1,5 m
---
##############
#............#
#..A.....g...#
#........g...#
#..B.....h...#
#............#
##############
---
A: Aldric | pc
B: Mira | pc
g: Goblin | foe
h: Hobgoblin | foe
{areas}
"""


def spec(*areas, text=FIELD):
    return mr.parse(text.format(areas="\n".join(areas)))


class GeometryTests(unittest.TestCase):
    def test_a_sphere_takes_the_squares_whose_centre_is_inside(self):
        cells, outline = mr.template_cells("sphere", 5, 5, 2)
        self.assertEqual(len(cells), 13)
        self.assertIn((5, 3), cells)
        self.assertNotIn((4, 3), cells)
        self.assertEqual(outline, ("circle", 5.5, 5.5, 2))

    def test_a_cone_widens_as_it_goes(self):
        cells, outline = mr.template_cells("cone", 0, 0, 3, direction=(1, 0))
        rows = {}
        for i, j in cells:
            rows.setdefault(i, []).append(j)
        self.assertEqual({i: sorted(js) for i, js in rows.items()}, {1: [0], 2: [0], 3: [-1, 0, 1]})
        self.assertEqual(outline[0], "poly")
        self.assertEqual(len(outline[1]), 3)

    def test_a_line_and_a_cube(self):
        cells, _ = mr.template_cells("line", 0, 0, 4, 1, direction=(0, 1))
        self.assertEqual(sorted(cells), [(0, 1), (0, 2), (0, 3), (0, 4)])
        cells, _ = mr.template_cells("cube", 0, 0, 2, direction=(1, 1))
        self.assertEqual(sorted(cells), [(1, 1), (1, 2), (2, 1), (2, 2)])
        cells, _ = mr.template_cells("cube", 0, 0, 3, direction=(1, 0))
        self.assertEqual(sorted(cells), [(i, j) for i in (1, 2, 3) for j in (-1, 0, 1)])

    def test_sizes_in_feet_metres_and_squares(self):
        s = spec()
        per_foot = mr.squares_per_foot(s)
        self.assertEqual([mr._size(t, per_foot) for t in ("20ft", "6m", "4q", "10", "x5ft")], [4, 4, 4, 2, 1])
        self.assertIsNone(mr._size("tanto", per_foot))
        self.assertEqual(mr.squares_per_foot(mr.parse("scale: 1 square = 10 ft\n---\n...\n")), 1 / 10)
        self.assertEqual(mr.squares_per_foot(mr.parse("...\n")), 1 / 5)


class MapTests(unittest.TestCase):
    def test_templates_are_read_and_say_who_is_inside(self):
        s = spec("@ 10,4 sfera 3m fuoco | Palla di fuoco", "@ 2,2 cubo 3m SE magia | Onda tonante",
                 "@ 4,3 cone 15ft E cold | Cono di freddo", "@ 4,5 line 30ft x5ft NE lightning | Fulmine")
        self.assertEqual(s.warnings, [])
        self.assertEqual([(e.kind, e.shape, len(e.cells)) for e in s.effects],
                         [("fire", "sphere 3m", 13), ("magic", "cube 3m", 4), ("cold", "cone 15ft", 5),
                          ("lightning", "line 30ft", 4)])
        self.assertEqual([n for _c, n, _s in mr.tokens_in(s, s.effects[0])], ["Goblin", "Goblin", "Hobgoblin"])
        lines = mr.describe_templates(s)
        self.assertEqual(lines[0], "Palla di fuoco [sphere 3m, 13 squares]: Goblin (foe), Goblin (foe), Hobgoblin (foe)")
        self.assertEqual(lines[1], "Onda tonante [cube 3m, 4 squares]: Aldric (pc)")
        self.assertTrue(lines[2].endswith(": nobody"))

    def test_the_drawing_has_the_outline_and_the_legend_names_the_caught(self):
        svg = mr.render_svg(spec("@ 10,4 sfera 3m fuoco | Palla di fuoco", "@ 4,3 cono 4,5m E | Mani brucianti"))
        self.assertIn('stroke-dasharray="8 4"', svg)
        self.assertIn("<circle", svg)
        self.assertIn("<polygon", svg)
        self.assertIn("Palla di fuoco: g, h", svg)

    def test_old_rectangles_still_work_and_mistakes_are_reported(self):
        s = spec("@ 9,8 r1 fire | Esplosione", "@ 4,3 cono E fuoco | senza misura", "@ 4,3 cone 15ft fire")
        self.assertEqual(len(s.effects), 2)
        self.assertIsNone(s.effects[0].cells)
        self.assertTrue(any("without a size" in w for w in s.warnings))
        self.assertTrue(any("without a direction" in w for w in s.warnings))

    def test_a_template_off_the_map_is_dropped(self):
        s = spec("@ 1,1 sphere 5ft fire | Lontano".replace("1,1", "40,40"))
        self.assertEqual(s.effects, [])
        self.assertTrue(any("outside the map" in w for w in s.warnings))

    def test_fog_hides_the_unseen_from_the_legend(self):
        text = FIELD.replace("scale:", "fog: on\nsight: 3\nscale:")
        s = mr.parse(text.format(areas="@ 10,4 sfera 3m fuoco | Palla di fuoco"))
        fog = (mr.visible_cells(s), mr.visible_cells(s))
        svg = mr.render_svg(s, fog=fog)
        self.assertNotIn("Palla di fuoco: g", svg)


if __name__ == "__main__":
    unittest.main()
