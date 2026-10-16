"""
test_pwa.py — the display installs on a phone's home screen.

The manifest and the service worker are public and carry no game data; the
worker caches nothing and only answers an unreachable display with an offline
page in the browser's language; the page offers "Install the app" where the
browser can.

Run from repo root:
    python -m pytest tests/test_pwa.py -q
"""
import importlib.util
import json
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
LOGIN = DISPLAY / "templates" / "login.html"
NODE = shutil.which("node")
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(SKILL / "scripts"))


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_pwa", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        old = self.mod._lan_token
        self.mod._lan_token = "segreto"          # LAN mode: everything else needs a login
        self.addCleanup(setattr, self.mod, "_lan_token", old)

    def get(self, path, lang="it-IT,it;q=0.9"):
        return self.client.get(path, headers={"Accept-Language": lang}, environ_base={"REMOTE_ADDR": "192.168.1.50"})

    def test_the_manifest_is_public_and_installable(self):
        r = self.get("/manifest.webmanifest")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "application/manifest+json")
        m = json.loads(r.get_data(as_text=True))
        self.assertEqual((m["name"], m["short_name"], m["lang"]), ("D&D al tavolo", "D&D", "it"))
        self.assertEqual((m["start_url"], m["scope"], m["display"]), ("/", "/", "standalone"))
        sizes = {(i["sizes"], i["purpose"]) for i in m["icons"]}
        self.assertTrue({("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= sizes)
        for icon in m["icons"]:
            path = DISPLAY / "icons" / icon["src"].rsplit("/", 1)[1]
            head = path.read_bytes()[:24]           # PNG: width and height in the IHDR chunk
            self.assertEqual("%dx%d" % (int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")),
                             icon["sizes"], path.name)
            self.assertEqual(self.get(icon["src"]).status_code, 200, icon["src"])
        self.assertEqual(json.loads(self.get("/manifest.webmanifest", lang="en-US").get_data(as_text=True))["name"],
                         "D&D at the table")
        self.assertEqual(json.loads(self.get("/manifest.webmanifest", lang="xx").get_data(as_text=True))["lang"], "en")

    def test_the_worker_caches_nothing_and_has_the_offline_page(self):
        r = self.get("/sw.js")
        self.assertEqual((r.status_code, r.mimetype), (200, "text/javascript"))
        js = r.get_data(as_text=True)
        self.assertIn("Il tavolo non risponde", js)
        self.assertIn("e.request.mode !== 'navigate'", js)
        self.assertNotIn("caches.", js)
        self.assertNotIn("</script>", js, "the offline page is a JS string: no raw </script> in it")
        self.assertEqual(r.headers["Service-Worker-Allowed"], "/")
        if NODE:
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
                f.write("const self={addEventListener(){}};\n" + js + "\nconsole.log(OFFLINE);\n")
                path = f.name
            try:
                out = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
                self.assertEqual(out.returncode, 0, out.stderr[:400])
                self.assertIn("<h1>Il tavolo non risponde</h1>", out.stdout)
                self.assertIn("fetch('/ping'", out.stdout)
            finally:
                pathlib.Path(path).unlink(missing_ok=True)

    def test_the_rest_still_needs_a_login(self):
        self.assertIn("pwa_manifest", self.mod._PUBLIC_ENDPOINTS)
        self.assertIn("pwa_sw", self.mod._PUBLIC_ENDPOINTS)
        self.assertIn("ping", self.mod._PUBLIC_ENDPOINTS, "the offline page polls /ping before reloading")
        self.assertEqual(self.get("/vote").status_code, 401)


_SLICE = re.compile(r"(function _pwaPlan\(ua, standalone, hasPrompt\).*?)\n// ── end pwa plan ──", re.S)


class PageTests(unittest.TestCase):
    def test_both_pages_link_the_manifest_and_register_the_worker(self):
        for page in (HTML, LOGIN):
            src = page.read_text(encoding="utf-8")
            with self.subTest(page=page.name):
                self.assertIn('<link rel="manifest" href="/manifest.webmanifest">', src)
                self.assertIn('<meta name="theme-color"', src)
                self.assertIn("navigator.serviceWorker.register('/sw.js')", src)
        src = HTML.read_text(encoding="utf-8")
        for needle in ('id="ph-install-opt"', 'id="ln-install-more"', 'id="pwa-modal"', "beforeinstallprompt"):
            self.assertIn(needle, src)

    def test_when_the_install_button_shows(self):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the PWA block is no longer where this test slices it")
        cases = [
            ("Mozilla/5.0 (Linux; Android 14) Chrome/129 Mobile", False, True),
            ("Mozilla/5.0 (Linux; Android 14) Chrome/129 Mobile", False, False),
            ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) Version/17.5 Mobile/15E148 Safari/604.1", False, False),
            ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) CriOS/129 Mobile/15E148", False, False),
            ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Version/17.5 Mobile/15E148 Safari/604.1", False, False),
            ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) Version/17.5 Mobile/15E148 Safari/604.1", True, False),
        ]
        script = m.group(1) + "\nconsole.log(JSON.stringify(%s.map(c => _pwaPlan(c[0], c[1], c[2]).mode)));\n" % json.dumps(cases)
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            modes = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        self.assertEqual(modes, ["prompt", None, "ios", None, "ios", None])

    def test_strings_exist_in_every_language(self):
        keys = ["pwa.name", "pwa.shortName", "pwa.offlineTitle", "pwa.offlineText", "pwa.retry", "pwa.install",
                "pwa.installed", "pwa.ios1", "pwa.ios2", "pwa.ios3", "pwa.iosLogin"]
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
