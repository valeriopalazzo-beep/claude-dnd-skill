"""PIN login for the LAN display (display/accounts.py + the _auth_gate in
dnd-display-app.py).

Pins the contract that makes impersonation impossible:
- in LAN mode a browser that isn't this PC sees only the login page until it
  logs in;
- a player session acts as its own character whatever name the browser sends,
  reads only its own sheet, and can't reach DM-only endpoints;
- wrong PINs are throttled, and a PIN change / campaign switch / logout-all
  ends existing sessions.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
TOKEN = "t" * 64
PHONE = {"REMOTE_ADDR": "192.168.1.50"}


def _load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class AccountsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if str(DISPLAY) not in sys.path:
            sys.path.insert(0, str(DISPLAY))
        cls.acc = _load(DISPLAY / "accounts.py", "accounts_under_test")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self._saved = (self.acc.find_campaign, self.acc.DM_FILE)
        self.acc.find_campaign = lambda name: root / "campaigns" / name
        self.acc.DM_FILE = str(root / "dm_account.json")
        self.root = root

    def tearDown(self):
        self.acc.find_campaign, self.acc.DM_FILE = self._saved
        self.tmp.cleanup()

    def test_pin_format(self):
        for ok in ("1234", "123456", "12345678"):
            self.assertTrue(self.acc.valid_pin(ok), ok)
        for bad in ("", "123", "123456789", "12a4", " 1234", "1234\n", "１２３４"):
            self.assertFalse(self.acc.valid_pin(bad), bad)

    def test_hash_round_trip_and_no_clear_text(self):
        rec = self.acc.hash_pin("4821")
        self.assertTrue(self.acc.verify_pin("4821", rec))
        self.assertFalse(self.acc.verify_pin("4822", rec))
        self.assertFalse(self.acc.verify_pin("4821", None))
        self.assertNotIn("4821", json.dumps(rec))

    def test_set_pin_is_case_insensitive_and_replaces(self):
        self.acc.set_pin("prova", "Mira", "1111")
        self.acc.set_pin("prova", "mira", "2222")
        data = self.acc.load("prova")
        self.assertEqual(list(data["characters"]), ["mira"])
        self.assertTrue(self.acc.verify_pin("2222", data["characters"]["mira"]))
        self.assertNotIn("2222", (self.root / "campaigns" / "prova" / "accounts.json").read_text(encoding="utf-8"))

    def test_cli_rejects_bad_pin(self):
        self.assertEqual(self.acc.main(["set-pin", "--campaign", "prova",
                                        "--character", "Mira", "--pin", "12"]), 2)
        self.assertEqual(self.acc.load("prova")["characters"], {})


class LoginGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _load(DISPLAY / "dnd-display-app.py", "login_display_app_under_test")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        (root / "campaigns" / "prova" / "characters").mkdir(parents=True)
        for n in ("Mira", "Bran"):
            (root / "campaigns" / "prova" / "characters" / f"{n}.md").write_text(f"# {n}", encoding="utf-8")
        self.camp_file = root / ".campaign"
        self.camp_file.write_text("prova", encoding="utf-8")

        a = self.app
        acc = a._accounts
        self._saved = dict(
            CAMP_FILE=a.CAMP_FILE, SESSIONS_FILE=a.SESSIONS_FILE, _lan_token=a._lan_token,
            _broadcast=a._broadcast, _persist_approved_devices=a._persist_approved_devices,
            _persist_input_queue=a._persist_input_queue, _expected_count=a._expected_count,
            _current_stats=a._current_stats,
            _persist_log=a._persist_log, _persist_tail=a._persist_tail,
            LOG_FILE=a.LOG_FILE, STATS_FILE=a.STATS_FILE,
        )
        self._saved_env = os.environ.get("DND_CAMPAIGN_ROOT")
        os.environ["DND_CAMPAIGN_ROOT"] = str(root)   # /character/<name> reads it directly
        self._saved_acc = (acc.find_campaign, acc.DM_FILE)
        a.CAMP_FILE = str(self.camp_file)
        a.SESSIONS_FILE = str(root / "sessions.json")
        a._lan_token = TOKEN
        self.sent = []
        a._broadcast = self.sent.append
        a._persist_approved_devices = lambda: None
        a._persist_input_queue = lambda: None
        a._persist_log = a._persist_tail = lambda: None   # never touch the real session log
        a.LOG_FILE, a.STATS_FILE = str(root / "log.json"), str(root / "stats.json")
        a._expected_count = 2
        a._current_stats = {"players": [{"name": "Mira"}, {"name": "Bran"}]}
        acc.find_campaign = lambda name: root / "campaigns" / name
        acc.DM_FILE = str(root / "dm_account.json")
        acc.set_pin("prova", "Mira", "111111")
        acc.set_pin("prova", "Bran", "222222")
        a._sessions.clear()
        a._login_fails_ip.clear()
        a._login_fails_char.clear()
        a._rate_buckets.clear()
        a._staged.clear()
        a._input_queue.clear()
        a._client_chars.clear()
        self.client = a.app.test_client()

    def tearDown(self):
        a = self.app
        for k, v in self._saved.items():
            setattr(a, k, v)
        a._accounts.find_campaign, a._accounts.DM_FILE = self._saved_acc
        if self._saved_env is None:
            os.environ.pop("DND_CAMPAIGN_ROOT", None)
        else:
            os.environ["DND_CAMPAIGN_ROOT"] = self._saved_env
        a._sessions.clear()
        a._staged.clear()
        a._input_queue.clear()
        self.tmp.cleanup()

    # helpers
    def _post(self, path, body, **kw):
        return self.client.post(path, data=json.dumps(body), content_type="application/json",
                                environ_base=kw.pop("env", PHONE), **kw)

    def _login(self, character="Mira", pin="111111", env=PHONE):
        return self._post("/login", {"character": character, "pin": pin}, env=env)

    # ── gate ──
    def test_logged_out_phone_gets_login_page_and_nothing_else(self):
        r = self.client.get("/", environ_base=PHONE)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'id="step-pin"', r.data)
        for path in ("/stream", "/character/Mira", "/srd-lookup?name=Fireball", "/media/x.png"):
            self.assertEqual(self.client.get(path, environ_base=PHONE).status_code, 401, path)
        self.assertEqual(self._post("/player-input/stage", {"character": "Mira", "text": "x"}).status_code, 401)

    def test_this_pc_and_the_token_are_the_dm(self):
        r = self.client.get("/")
        self.assertIn(b'name="dnd-login" content=""', r.data)
        self.assertNotIn(TOKEN.encode(), r.data)
        r = self.client.get("/dice-request/abc", headers={"X-DND-Token": TOKEN}, environ_base=PHONE)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/dice-request/abc", environ_base=PHONE).status_code, 401)

    def test_options_list_only_characters_with_a_pin(self):
        o = self.client.get("/login/options", environ_base=PHONE).get_json()
        self.assertEqual(o, {"characters": ["Bran", "Mira"], "dm": False, "me": None})

    # ── login ──
    def test_wrong_pin_then_right_pin(self):
        self.assertEqual(self._login(pin="999999").status_code, 403)
        r = self._login()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["redirect"], "/?char=Mira")
        cookie = r.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertEqual(self.client.get("/login/options", environ_base=PHONE).get_json()["me"], "player")
        sessions = json.loads(pathlib.Path(self.app.SESSIONS_FILE).read_text(encoding="utf-8"))
        sid = self.client.get_cookie("dnd_session").value
        self.assertNotIn(sid, json.dumps(sessions))   # only the hash is stored

    def test_lockout_after_five_misses_even_with_the_right_pin(self):
        for _ in range(5):
            self.assertEqual(self._login(pin="000000").status_code, 403)
        r = self._login()
        self.assertEqual(r.status_code, 429)
        self.assertGreater(r.get_json()["retry_after"], 0)

    def test_unknown_character(self):
        self.assertEqual(self._login(character="Nessuno").status_code, 404)

    # ── a player is bound to their character ──
    def test_player_acts_only_as_own_character(self):
        self._login()
        r = self._post("/player-input/stage", {"character": "Bran", "text": "attacco"},
                       headers={"X-DND-Device": "phone-1"})
        self.assertEqual(r.status_code, 204, r.data)
        self.assertIn("Mira", self.app._staged)
        self.assertNotIn("Bran", self.app._staged)

        r = self._post("/player-input/dice", {"character": "Bran", "spec": "1d20"})
        self.assertEqual(r.get_json()["character"], "Mira")

        self.assertEqual(self.client.get("/character/Mira", environ_base=PHONE).status_code, 200)
        self.assertEqual(self.client.get("/character/Bran", environ_base=PHONE).status_code, 403)
        # The printable sheet and its PDF follow the same rule.
        self.assertEqual(self.client.get("/character/Mira/print", environ_base=PHONE).status_code, 200)
        self.assertEqual(self.client.get("/character/Bran/print", environ_base=PHONE).status_code, 403)
        self.assertEqual(self.client.get("/character/Bran/pdf", environ_base=PHONE).status_code, 403)

    def test_player_url_for_another_character_redirects_to_own(self):
        self._login()
        r = self.client.get("/?char=Bran", environ_base=PHONE)
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/?char=Mira"))
        r = self.client.get("/?char=mira", environ_base=PHONE)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'name="dnd-login" content="player"', r.data)

    def test_player_cannot_reach_dm_endpoints(self):
        self._login()
        for path in ("/clear", "/stats", "/chunk", "/dice-request", "/player-input/drain"):
            self.assertEqual(self._post(path, {}).status_code, 403, path)

    def test_cross_site_post_is_refused(self):
        self._login()
        r = self._post("/player-input/stage", {"character": "Mira", "text": "x"},
                       headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status_code, 403)

    # ── sessions end ──
    def test_pin_change_logs_the_character_out(self):
        self._login()
        time.sleep(0.01)
        self.app._accounts.set_pin("prova", "Mira", "333333")
        self.assertEqual(self.client.get("/character/Mira", environ_base=PHONE).status_code, 401)

    def test_logout_all_and_campaign_switch(self):
        self._login()
        time.sleep(0.01)
        self.app._accounts.logout_all("prova")
        self.assertEqual(self.client.get("/character/Mira", environ_base=PHONE).status_code, 401)

        self.app._login_fails_ip.clear()
        self._login()
        self.camp_file.write_text("altra", encoding="utf-8")
        self.assertEqual(self.client.get("/character/Mira", environ_base=PHONE).status_code, 401)

    def test_logout(self):
        self._login()
        self._post("/logout", {})
        self.assertEqual(self.client.get("/character/Mira", environ_base=PHONE).status_code, 401)

    # ── DM PIN ──
    def test_dm_pin_login_from_another_device(self):
        self.assertEqual(self._post("/login", {"dm": True, "pin": "123456"}).status_code, 403)
        self.app._accounts.set_dm_pin("123456")
        self.app._login_fails_ip.clear()
        r = self._post("/login", {"dm": True, "pin": "123456"})
        self.assertEqual(r.get_json()["redirect"], "/")
        self.assertEqual(self.client.get("/dice-request/abc", environ_base=PHONE).status_code, 200)

    def test_localhost_mode_has_no_login(self):
        self.app._lan_token = None
        self.assertEqual(self.client.get("/character/Bran", environ_base=PHONE).status_code, 200)
        self.assertEqual(self.client.get("/login/options").status_code, 404)


if __name__ == "__main__":
    unittest.main()
