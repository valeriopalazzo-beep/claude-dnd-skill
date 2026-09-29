"""Campaign images: ASCII map rendering, the image cache, and the display routes.

WHY
===
The skill never produced pictures. image_gen.py (portraits/monsters/scenes via
Pollinations, a local SD server, or Gemini) and map_render.py (battle maps the
DM draws as text) write into <campaign>/media/, and the display serves that
folder at /media/<file>. The server side is where a mistake would hurt — a
loose filename check there is a path-traversal read — so it is tested here
without any network.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"
sys.path.insert(0, str(DISPLAY))


def _load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


MAP = """\
    title: Cripta
    scale: 1 square = 5 ft
    ---
    ########
    #..A..g#
    #..B..?+
    ########
    ---
    A: Aldric | pc
    B: <script>alert(1)</script> | ally
    g: Goblin | enemy
"""


class MapRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mr = _load(DISPLAY / "map_render.py", "map_render_under_test")

    def test_header_grid_and_legend_are_parsed(self):
        spec = self.mr.parse(MAP)
        self.assertEqual(spec.title, "Cripta")
        self.assertEqual(spec.scale, "1 square = 5 ft")
        self.assertEqual(spec.rows[0], "########", "common indentation is removed")
        self.assertEqual(spec.legend["A"], ("Aldric", "pc"))
        self.assertEqual(spec.legend["g"], ("Goblin", "foe"), "'enemy' is an alias of foe")

    def test_unknown_symbols_warn_instead_of_failing(self):
        spec = self.mr.parse(MAP)
        self.assertEqual(len(spec.warnings), 1)
        self.assertIn("'?'", spec.warnings[0])

    def test_tokens_without_legend_default_by_case(self):
        svg = self.mr.render_svg(self.mr.parse("#Ab#"))
        self.assertIn(self.mr.SIDES["pc"], svg)
        self.assertIn(self.mr.SIDES["foe"], svg)

    def test_names_are_escaped(self):
        svg = self.mr.render_svg(self.mr.parse(MAP))
        self.assertNotIn("<script>", svg)
        self.assertIn("&lt;script&gt;", svg)

    def test_oversized_and_empty_maps_are_rejected(self):
        with self.assertRaises(ValueError):
            self.mr.parse("")
        with self.assertRaises(ValueError):
            self.mr.parse("\n".join(["." * 61] * 3))


class ImageGenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ig = _load(DISPLAY / "image_gen.py", "image_gen_under_test")

    def test_versions_are_found_in_order_and_do_not_bleed_across_subjects(self):
        with tempfile.TemporaryDirectory() as t:
            d = pathlib.Path(t)
            for n in ("portrait-goblin.jpg", "portrait-goblin--v2.png",
                      "portrait-goblin--v10.png", "portrait-goblin-chief.jpg",
                      "monster-goblin.jpg"):
                (d / n).write_bytes(b"x")
            got = [p.name for p in self.ig.existing_versions(d, "portrait", "goblin")]
            self.assertEqual(got, ["portrait-goblin.jpg", "portrait-goblin--v2.png",
                                   "portrait-goblin--v10.png"])

    def test_local_backend_size_is_capped_to_multiples_of_64(self):
        w, h = self.ig._size_for("scene", 768)
        self.assertLessEqual(max(w, h), 768)
        self.assertEqual((w % 64, h % 64), (0, 0))
        self.assertEqual(self.ig._size_for("portrait"), (768, 1024))

    def test_non_image_payloads_are_refused(self):
        self.assertEqual(self.ig._ext_for(b"\x89PNG\r\n\x1a\n...."), "png")
        self.assertEqual(self.ig._ext_for(b"\xff\xd8\xff\xe0"), "jpg")
        with self.assertRaises(self.ig.ImageError):
            self.ig._ext_for(b'{"error": "queue full"}')

    def test_prompt_carries_kind_preset_and_style(self):
        p = self.ig.build_prompt("monster", "a ghoul.", "oil painting")
        self.assertTrue(p.startswith("fantasy creature"))
        self.assertTrue(p.endswith("a ghoul, oil painting"))


class MediaRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _load(DISPLAY / "dnd-display-app.py", "images_display_app_under_test")
        cls.client = cls.app.app.test_client()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        camp_dir = root / "campaigns" / "prova"
        (camp_dir / "media").mkdir(parents=True)
        (camp_dir / "media" / "portrait-vesna.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
        (camp_dir / "secret.md").write_text("do not serve", encoding="utf-8")
        camp_file = root / ".campaign"
        camp_file.write_text("prova", encoding="utf-8")

        a = self.app
        self._saved = (a.CAMP_FILE, a._find_campaign, a._lan_token, a._token_ok,
                       a._persist_log, a._persist_tail, a._broadcast)
        a.CAMP_FILE = str(camp_file)
        a._find_campaign = lambda name: root / "campaigns" / name
        a._lan_token = None
        a._token_ok = lambda: True
        a._persist_log = a._persist_tail = lambda: None
        self.sent = []
        a._broadcast = self.sent.append

    def tearDown(self):
        a = self.app
        (a.CAMP_FILE, a._find_campaign, a._lan_token, a._token_ok,
         a._persist_log, a._persist_tail, a._broadcast) = self._saved
        self.tmp.cleanup()

    def test_active_campaign_media_is_served(self):
        r = self.client.get("/media/portrait-vesna.png")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data.startswith(b"\x89PNG"))
        r.close()

    def test_only_media_names_are_served(self):
        for bad in ("secret.md", "..%2Fsecret.md", "%2E%2E%2Fsecret.md", ".hidden.png"):
            r = self.client.get(f"/media/{bad}")
            self.assertIn(r.status_code, (403, 404), bad)
            r.close()

    def test_lan_mode_needs_the_token_in_header_or_query(self):
        self.app._lan_token = "t" * 64
        self.assertEqual(self.client.get("/media/portrait-vesna.png").status_code, 403)
        r = self.client.get("/media/portrait-vesna.png?t=" + "t" * 64)
        self.assertEqual(r.status_code, 200)
        r.close()

    def test_image_post_broadcasts_and_logs(self):
        r = self.client.post("/image", data=json.dumps(
            {"file": "portrait-vesna.png", "kind": "portrait", "caption": "Vesna", "subject": "Vesna"}),
            content_type="application/json")
        self.assertEqual(r.status_code, 204, r.data)
        self.assertEqual(self.sent[-1], {"image": {"file": "portrait-vesna.png", "kind": "portrait",
                                                   "caption": "Vesna", "subject": "Vesna"}})
        self.assertEqual(self.app._tail_buffer[-1]["image"]["file"], "portrait-vesna.png")
        self.assertEqual(self.app._tail_buffer[-1]["_camp"], "prova")

    def test_image_post_rejects_missing_or_foreign_files(self):
        for name in ("nope.png", "../secret.md", "secret.md"):
            r = self.client.post("/image", data=json.dumps({"file": name}),
                                 content_type="application/json")
            self.assertEqual(r.status_code, 400, name)
        self.assertEqual(self.sent, [])


class FrontendWiringTests(unittest.TestCase):
    def test_image_blocks_render_live_and_on_replay(self):
        src = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("if (payload.image)", src)
        self.assertIn("if (item.image)", src)
        self.assertIn(".image-block", src)


if __name__ == "__main__":
    unittest.main()
