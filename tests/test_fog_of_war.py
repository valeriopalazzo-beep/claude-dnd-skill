"""
test_fog_of_war.py — the battle map shows only what the party can see.

With `fog: on`, display/map_render.py works out what the pc and ally tokens
see from where they stand (walls and doors stop the eye), remembers what they
saw on earlier sends, covers the rest, and leaves out of the picture any
creature standing where nobody is looking.

Run from repo root:
    python -m pytest tests/test_fog_of_war.py -q
"""
import contextlib
import io
import os
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"
sys.path.insert(0, str(DISPLAY))

import map_render as mr  # noqa: E402

CRYPT = """title: Cripta di Vessar
fog: on
sight: 6
---
###########
#..A#..g..#
#...+.....#
#...#..h..#
###########
---
A: Aldric | pc
g: Goblin arciere | foe
h: Hobgoblin | foe
"""


def _spec(text=CRYPT):
    return mr.parse(text)


class SightTests(unittest.TestCase):
    def test_the_header_turns_fog_on_and_sets_how_far_they_see(self):
        spec = _spec()
        self.assertEqual((spec.fog, spec.fog_reset, spec.sight), (True, False, 6))
        self.assertFalse(_spec(CRYPT.replace("fog: on\nsight: 6\n", "")).fog)
        self.assertFalse(_spec(CRYPT.replace("fog: on", "fog: off")).fog)
        again = _spec(CRYPT.replace("fog: on", "nebbia: reset").replace("sight: 6", "vista: 3 quadretti"))
        self.assertEqual((again.fog, again.fog_reset, again.sight), (True, True, 3))
        odd = _spec(CRYPT.replace("sight: 6", "sight: lontano"))
        self.assertEqual(odd.sight, 0)
        self.assertTrue(any("sight" in w for w in odd.warnings))

    def test_walls_and_a_closed_door_stop_the_eye(self):
        seen = mr.visible_cells(_spec())
        room = {(x, y) for x in (1, 2, 3) for y in (1, 2, 3)}
        self.assertTrue(room <= seen)
        self.assertIn((4, 2), seen)                              # the door itself
        self.assertIn((0, 0), seen)                              # and the walls of the room
        self.assertFalse(any(x >= 5 for x, y in seen), "nothing beyond the door or the wall")

    def test_an_open_door_lets_them_look_through(self):
        seen = mr.visible_cells(_spec(CRYPT.replace("#...+.....#", "#.........#")))
        self.assertIn((5, 2), seen)                              # through the doorway
        self.assertNotIn((7, 1), seen)                           # but not round the corner
        self.assertIn((7, 3), seen)                              # the hobgoblin stands in view

    def test_sight_has_a_length(self):
        hall = "fog: on\nsight: 3\n---\nA.........\n---\nA: Aldric | pc\n"
        self.assertEqual(sorted(x for x, y in mr.visible_cells(mr.parse(hall))), [0, 1, 2, 3])
        far = mr.visible_cells(mr.parse(hall.replace("sight: 3\n", "")))
        self.assertEqual(len(far), 10)                           # the default reaches the end of this hall

    def test_allies_see_and_foes_do_not(self):
        text = "fog: on\n---\n...#...#...\n.A.#.b.#.g.\n...#...#...\n---\nA: Aldric | pc\nb: Ser Bertrand | ally\ng: Goblin | foe\n"
        seen = mr.visible_cells(mr.parse(text))
        self.assertIn((5, 1), seen)
        self.assertNotIn((9, 1), seen)

    def test_the_dm_can_light_an_area_whatever_the_tokens_see(self):
        spec = _spec(CRYPT + "? 7,2-9,3\n? 10,4 r0\n")
        self.assertEqual(spec.reveal, [(6, 1, 8, 2), (9, 3, 9, 3)])
        seen = mr.visible_cells(spec)
        self.assertTrue({(6, 1), (8, 2), (9, 3)} <= seen)
        self.assertNotIn((7, 3), seen)

    def test_with_no_one_to_look_everything_is_dark(self):
        self.assertEqual(mr.visible_cells(_spec(CRYPT.replace("#..A#", "#...#"))), set())


class PictureTests(unittest.TestCase):
    def test_a_map_without_fog_is_drawn_as_before(self):
        spec = _spec(CRYPT.replace("fog: on\nsight: 6\n", ""))
        svg = mr.render_svg(spec)
        self.assertNotIn('class="fog', svg)
        self.assertIn("Goblin arciere", svg)
        self.assertEqual(svg, mr.render_svg(spec, fog=None))

    def test_what_nobody_sees_is_not_in_the_picture_at_all(self):
        spec = _spec()
        now = mr.visible_cells(spec)
        svg = mr.render_svg(spec, fog=(now, set(now)))
        self.assertIn("Aldric", svg)
        for hidden in ("Goblin arciere", "Hobgoblin"):
            self.assertNotIn(hidden, svg)                        # neither the token nor its legend line
        self.assertEqual(sorted(mr.hidden_tokens(spec, (now, now))), ["g", "h"])
        self.assertIn('class="fog2"', svg)
        self.assertNotIn('class="fog1"', svg)

    def test_a_foe_in_view_is_drawn(self):
        spec = _spec(CRYPT.replace("#...+.....#", "#.........#"))
        now = mr.visible_cells(spec)
        svg = mr.render_svg(spec, fog=(now, set(now)))
        self.assertIn("Hobgoblin", svg)
        self.assertNotIn("Goblin arciere", svg)                  # behind the wall beside the doorway
        self.assertEqual(mr.hidden_tokens(spec, (now, now)), ["g"])

    def test_what_was_seen_before_stays_dimmed(self):
        spec = _spec()
        now = mr.visible_cells(spec)
        before = now | {(6, 1), (7, 1), (8, 1)}
        svg = mr.render_svg(spec, fog=(now, before))
        self.assertIn('class="fog1"', svg)
        self.assertIn('fill-opacity="0.5"', svg)
        self.assertNotIn("Goblin arciere", svg)                  # remembered ground, but nobody is looking now

    def test_an_effect_out_of_sight_is_not_named_in_the_legend(self):
        spec = _spec(CRYPT + "@ 8,2 fire | Braciere rovesciato\n@ 2,2 magic | Glifo\n")
        now = mr.visible_cells(spec)
        svg = mr.render_svg(spec, fog=(now, set(now)))
        self.assertIn("Glifo", svg)
        self.assertNotIn("Braciere rovesciato</text>", svg)


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._old = os.environ.get("DND_CAMPAIGN_ROOT")
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        (self.root / "campaigns" / "prova").mkdir(parents=True)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("DND_CAMPAIGN_ROOT", None)
        else:
            os.environ["DND_CAMPAIGN_ROOT"] = self._old
        self._tmp.cleanup()

    def render(self, text):
        path = self.root / "map.txt"
        path.write_text(text, encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        old = sys.argv
        sys.argv = ["map_render.py", "--campaign", "prova", "--art", "off", "--no-send", "--file", str(path)]
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = mr.main()
        finally:
            sys.argv = old
        self.assertEqual(code, 0, err.getvalue())
        svg_path = pathlib.Path(out.getvalue().strip().splitlines()[-1])
        return svg_path.read_text(encoding="utf-8"), err.getvalue()

    def test_explored_ground_is_remembered_from_one_send_to_the_next(self):
        svg, err = self.render(CRYPT)
        self.assertIn("fog hides 2 token(s)", err)
        self.assertNotIn('class="fog1"', svg)
        media = self.root / "campaigns" / "prova" / "media"
        self.assertTrue((media / "fog-cripta-di-vessar.json").exists())
        # Aldric goes through the door: the first room is remembered, the second is in view.
        moved = CRYPT.replace("#..A#..g..#", "#...#..g..#").replace("#...+.....#", "#...+...A.#")
        svg, err = self.render(moved)
        self.assertIn('class="fog1"', svg)
        self.assertIn("Goblin arciere", svg)
        self.assertNotIn("fog hides", err)
        seen = mr.load_explored(media, "cripta-di-vessar", 11, 5)
        self.assertTrue({(1, 1), (9, 3)} <= seen)

    def test_reset_and_a_map_of_another_size_start_from_nothing(self):
        self.render(CRYPT.replace("#...+.....#", "#.........#"))
        media = self.root / "campaigns" / "prova" / "media"
        self.assertIn((7, 3), mr.load_explored(media, "cripta-di-vessar", 11, 5))
        self.render(CRYPT.replace("fog: on", "fog: reset"))
        self.assertNotIn((7, 3), mr.load_explored(media, "cripta-di-vessar", 11, 5))
        self.assertEqual(mr.load_explored(media, "cripta-di-vessar", 12, 5), set())
        self.assertEqual(mr.load_explored(media, "un-altra-mappa", 11, 5), set())

    def test_a_map_without_fog_remembers_nothing(self):
        self.render(CRYPT.replace("fog: on\nsight: 6\n", ""))
        self.assertEqual(list((self.root / "campaigns" / "prova" / "media").glob("fog-*.json")), [])


if __name__ == "__main__":
    unittest.main()
