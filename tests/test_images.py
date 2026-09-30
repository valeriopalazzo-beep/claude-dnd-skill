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

    def test_effects_are_parsed_with_radius_rectangle_and_clipping(self):
        spec = self.mr.parse("######\n#A...#\n#..g.#\n######\n---\n"
                             "@ 4,3 r1 explosion | Boom\n@ 2,2-3,2 portal\n@ 1,1 r9 cold\n"
                             "@ 30,30 fire\n@ nonsense")
        boom, portal, cold = spec.effects
        self.assertEqual((boom.x0, boom.y0, boom.x1, boom.y1, boom.kind, boom.label),
                         (2, 1, 4, 3, "fire", "Boom"), "r1 = one square around; 'explosion' is fire")
        self.assertEqual((portal.x0, portal.x1, portal.kind), (1, 2, "magic"))
        self.assertEqual((cold.x0, cold.y0, cold.x1, cold.y1), (0, 0, 5, 3), "clipped to the map")
        self.assertEqual(len(spec.warnings), 2, "off-map and unreadable lines warn, never fail")

    def test_effects_do_not_change_terrain_so_painted_art_stays_cached(self):
        plain = self.mr.parse("#####\n#A..#\n#####")
        burst = self.mr.parse("#####\n#A..#\n#####\n---\n@ 3,2 r1 fire | Fuoco")
        self.assertEqual(self.mr.kind_grid(plain), self.mr.kind_grid(burst))

    def test_effect_labels_are_escaped_and_listed(self):
        svg = self.mr.render_svg(self.mr.parse("####\n#A.#\n####\n---\n@ 3,2 fire | <img src=x>"))
        self.assertNotIn("<img", svg)
        self.assertIn("&lt;img src=x&gt;", svg)

    def test_oversized_and_empty_maps_are_rejected(self):
        with self.assertRaises(ValueError):
            self.mr.parse("")
        with self.assertRaises(ValueError):
            self.mr.parse("\n".join(["." * 61] * 3))


ART_MAP = """\
    title: Cripta
    art: damp crypt, cracked flagstones
    ---
    ######
    #A.~.+
    #..g^#
    ######
"""


class MapArtTests(unittest.TestCase):
    """Art comes from the image backend, positions never do."""

    @classmethod
    def setUpClass(cls):
        cls.mr = _load(DISPLAY / "map_render.py", "map_render_art_under_test")
        cls.ma = _load(DISPLAY / "map_art.py", "map_art")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = pathlib.Path(self.tmp.name)
        self.cfg = dict(self.ma.image_gen.DEFAULTS, backend="pollinations")
        self.calls = []
        self._saved = (self.ma.texture, self.ma.paint)
        png = self.ma.layout_png([["floor"]], cell=2)

        def fake(kind_or_kinds, desc, seed, cfg):
            self.calls.append(kind_or_kinds if isinstance(kind_or_kinds, str) else "paint")
            return png
        self.ma.texture = self.ma.paint = fake

    def tearDown(self):
        self.ma.texture, self.ma.paint = self._saved
        self.tmp.cleanup()

    def test_art_header_is_parsed(self):
        spec = self.mr.parse(ART_MAP)
        self.assertEqual(spec.art, "damp crypt, cracked flagstones")
        self.assertEqual(spec.rows[0], "######")

    def test_mode_follows_the_backend(self):
        rm = self.ma.resolve_mode
        self.assertEqual(rm(None, "pollinations"), "tiles")
        self.assertEqual(rm("auto", "local"), "paint")
        self.assertEqual(rm("auto", "gemini"), "paint")
        self.assertEqual(rm("paint", "pollinations"), "tiles", "pollinations cannot repaint a layout")
        self.assertEqual(rm("tiles", "local"), "tiles")
        self.assertEqual(rm("auto", "off"), "off")
        self.assertEqual(rm("off", "local"), "off")

    def test_tokens_do_not_change_the_terrain(self):
        a = self.mr.kind_grid(self.mr.parse("#A.g#"))
        b = self.mr.kind_grid(self.mr.parse("#.gA#"))
        self.assertEqual(a, b)
        self.assertEqual(a[0][1], "floor")

    def test_layout_png_is_a_valid_png_of_the_right_size(self):
        import struct
        import zlib
        data = self.ma.layout_png([["wall", "floor", "water"], ["floor", "door", "void"]], cell=4)
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        w, h = struct.unpack(">II", data[16:24])
        self.assertEqual((w, h), (12, 8))
        idat = data[data.index(b"IDAT") + 4:data.index(b"IEND") - 8]
        self.assertEqual(len(zlib.decompress(idat)), h * (1 + w * 3))

    def test_tiles_are_generated_once_per_terrain_and_reused(self):
        kinds = self.mr.kind_grid(self.mr.parse(ART_MAP))
        self.assertIsNone(self.ma.get_art(kinds, "crypt", self.d, self.cfg, "tiles", generate=False))
        art = self.ma.get_art(kinds, "crypt", self.d, self.cfg, "tiles")
        self.assertEqual(sorted(self.calls), ["floor", "wall", "water"])
        self.assertEqual(sorted(art.textures), ["floor", "wall", "water"])
        moved = self.mr.kind_grid(self.mr.parse(ART_MAP.replace("#A.~", "#.A~")))
        again = self.ma.get_art(moved, "crypt", self.d, self.cfg, "tiles", generate=False)
        self.assertIsNotNone(again, "moving a token must hit the cache")
        self.assertEqual(len(self.calls), 3)
        index = json.loads((self.d / "index.json").read_text(encoding="utf-8"))
        self.assertEqual({e["kind"] for e in index}, {"map-texture"})

    def test_paint_is_cached_per_terrain(self):
        kinds = self.mr.kind_grid(self.mr.parse(ART_MAP))
        cfg = dict(self.cfg, backend="local")
        self.ma.get_art(kinds, "", self.d, cfg, "paint")
        self.assertIsNotNone(self.ma.get_art(kinds, "", self.d, cfg, "paint", generate=False))
        kinds[1][2] = "wall"
        self.assertIsNone(self.ma.get_art(kinds, "", self.d, cfg, "paint", generate=False),
                          "changed terrain needs new art")
        self.assertEqual(self.calls, ["paint"])

    def test_paint_cache_is_per_local_model(self):
        kinds = self.mr.kind_grid(self.mr.parse(ART_MAP))
        cfg = dict(self.cfg, backend="local")
        self.ma.get_art(kinds, "", self.d, cfg, "paint")
        sdxl = dict(cfg, local_model="DreamShaperXL_Lightning")
        self.assertIsNone(self.ma.get_art(kinds, "", self.d, sdxl, "paint", generate=False),
                          "another checkpoint must repaint, not reuse the old model's art")
        self.assertIsNotNone(self.ma.get_art(kinds, "", self.d, cfg, "paint", generate=False),
                             "no local_model keeps the original key, so old caches stay valid")

    def test_a_failed_texture_leaves_that_terrain_vector(self):
        def flaky(kind, desc, seed, cfg):
            if kind == "water":
                raise self.ma.ImageError("queue full")
            return self.ma.layout_png([["floor"]], cell=2)
        self.ma.texture = flaky
        kinds = self.mr.kind_grid(self.mr.parse(ART_MAP))
        art = self.ma.get_art(kinds, "", self.d, self.cfg, "tiles")
        self.assertEqual(sorted(art.textures), ["floor", "wall"])
        self.assertEqual(art.missing, ["water"])
        cached = self.ma.get_art(kinds, "", self.d, self.cfg, "tiles", generate=False)
        self.assertEqual((sorted(cached.textures), cached.missing), (["floor", "wall"], ["water"]),
                         "a partial cache is still usable, and says what to retry")
        self.ma.texture = lambda *_a: self.fail("a texture that just failed must not be retried yet")
        again = self.ma.get_art(kinds, "", self.d, self.cfg, "tiles")
        self.assertEqual(again.missing, ["water"])
        svg = self.mr.render_svg(self.mr.parse(ART_MAP), art)
        self.assertIn('fill="url(#tex-floor)"', svg)
        self.assertIn(self.mr.C["water"], svg, "water falls back to the vector colour")

    def test_nothing_generated_raises(self):
        def broken(*_a):
            raise self.ma.ImageError("offline")
        self.ma.texture = broken
        kinds = self.mr.kind_grid(self.mr.parse(ART_MAP))
        with self.assertRaises(self.ma.ImageError):
            self.ma.get_art(kinds, "", self.d, self.cfg, "tiles")

    def test_painted_svg_keeps_tokens_and_markers_over_the_art(self):
        spec = self.mr.parse(ART_MAP)
        art = self.ma.Art("paint", image=("image/png", self.ma.layout_png([["floor"]], cell=2)))
        svg = self.mr.render_svg(spec, art)
        self.assertIn('<image href="data:image/png;base64,', svg)
        self.assertNotIn(self.mr.C["water"], svg, "terrain comes from the painting")
        self.assertIn(self.mr.C["door"], svg, "doors stay marked")
        self.assertIn(self.mr.C["trap"], svg, "known traps stay marked")
        self.assertIn(self.mr.SIDES["pc"], svg)
        self.assertIn(self.mr.SIDES["foe"], svg)


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

    def test_action_kind_is_close_framed_and_fits_sd15(self):
        self.assertIn("action", self.ig.CLI_KINDS)
        self.assertIn("medium shot", self.ig.build_prompt("action", "a dwarf swings an axe", ""))
        self.assertNotIn("establishing", self.ig.build_prompt("action", "x", ""))
        w, h = self.ig._size_for("action", 768)
        self.assertEqual((w, h), (768, 512))

    def test_dropped_connection_is_retried_once_then_reported(self):
        from unittest import mock
        calls = []

        def flaky(req, timeout):
            calls.append(1)
            if len(calls) == 1:
                raise self.ig.ConnectionDropped("connection dropped: reset")
            return b'{"images": ["aGVsbG8="]}'
        with mock.patch.object(self.ig, "_http", flaky), mock.patch.object(self.ig.time, "sleep"):
            self.assertEqual(self.ig.a1111("txt2img", {}, dict(self.ig.DEFAULTS)), b"hello")
        self.assertEqual(len(calls), 2)

        def dead(req, timeout):
            raise self.ig.ConnectionDropped("connection dropped: reset")
        with mock.patch.object(self.ig, "_http", dead), mock.patch.object(self.ig.time, "sleep"):
            with self.assertRaises(self.ig.ImageError):
                self.ig.a1111("txt2img", {}, dict(self.ig.DEFAULTS))

    def test_gpu_lock_lets_one_request_through_at_a_time(self):
        with tempfile.TemporaryDirectory() as t:
            lock = pathlib.Path(t) / "gpu.lock"
            with self.ig.local_gpu_lock(path=lock):
                with self.assertRaises(self.ig.ImageError):
                    with self.ig.local_gpu_lock(timeout=0.6, path=lock):
                        pass
            with self.ig.local_gpu_lock(timeout=0.6, path=lock):
                pass   # released: the next one gets it

    def test_local_params_only_send_what_is_configured(self):
        base = dict(self.ig.DEFAULTS)
        body = self.ig.local_params(base)
        self.assertEqual(body, {"steps": 25, "cfg_scale": 7.0}, "defaults leave sampler/model to the server")
        body = self.ig.local_params({**base, "local_steps": 6, "local_cfg": 2, "local_sampler": "DPM++ SDE",
                                     "local_scheduler": "Karras", "local_model": "DreamShaperXL_Lightning"})
        self.assertEqual(body["sampler_name"], "DPM++ SDE")
        self.assertEqual(body["scheduler"], "Karras")
        self.assertEqual(body["override_settings"], {"sd_model_checkpoint": "DreamShaperXL_Lightning"})
        self.assertIs(body["override_settings_restore_afterwards"], False)

    def test_prompt_carries_kind_preset_and_style(self):
        p = self.ig.build_prompt("monster", "a ghoul.", "oil painting")
        self.assertTrue(p.startswith("fantasy creature"))
        self.assertTrue(p.endswith("a ghoul, oil painting"))


class ComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cp = _load(DISPLAY / "compose.py", "compose_under_test")
        cls.ig = _load(DISPLAY / "image_gen.py", "image_gen_for_compose")

    def test_spec_accepts_names_aliases_numbers_and_sizes(self):
        figs = self.cp.parse("dwarf:left:large, hound:0.72, explosion:far-right:small")
        self.assertEqual(figs, [("humanoid-short", 0.3, 0.86), ("quadruped", 0.72, 0.62),
                                ("blast", 0.86, 0.42)])

    def test_bad_specs_are_refused_with_a_reason(self):
        for bad in ("", "unicorn:left", "humanoid:up", "humanoid:1.5", "humanoid:left:huge",
                    "humanoid, humanoid, humanoid, humanoid, humanoid"):
            with self.assertRaises(self.cp.ComposeError, msg=bad):
                self.cp.parse(bad)

    def test_depth_sketch_is_a_png_with_bright_figures_on_a_dark_ground(self):
        import struct, zlib
        png = self.cp.depth_png("humanoid:left, quadruped:right", 256, 176)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        w, h = struct.unpack(">II", png[16:24])
        self.assertEqual((w, h), (128, 88), "rendered at half size")
        idat = png[png.index(b"IDAT") + 4:png.index(b"IEND") - 8]
        raw = zlib.decompress(idat)
        row = raw[1 + (h // 2) * (1 + w * 3):][: w * 3]   # middle row, skip filter byte
        grey = row[::3]
        self.assertLess(min(grey), 60, "background stays far (dark)")
        self.assertGreater(max(grey), 180, "figures read near (bright)")

    def test_compose_unit_carries_model_image_and_guidance(self):
        cfg = dict(self.ig.DEFAULTS, local_controlnet_depth="depth [abc]")
        unit = self.ig.compose_unit("humanoid:left", 512, 384, cfg)
        self.assertEqual((unit["model"], unit["module"], unit["weight"], unit["guidance_end"]),
                         ("depth [abc]", "None", 1.0, 1.0))
        self.assertTrue(unit["image"].startswith("iVBOR"), "base64 PNG")

    def test_compose_is_ignored_without_a_depth_model(self):
        self.assertFalse(self.ig.compose_usable(dict(self.ig.DEFAULTS, backend="local")))
        self.assertFalse(self.ig.compose_usable(dict(self.ig.DEFAULTS, backend="gemini",
                                                     local_controlnet_depth="x")))
        self.assertTrue(self.ig.compose_usable(dict(self.ig.DEFAULTS, backend="local",
                                                    local_controlnet_depth="x")))


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
