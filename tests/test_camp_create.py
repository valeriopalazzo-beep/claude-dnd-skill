"""Campaign creation from the web (display/camp_create.py + the /campagna routes).

Pins the contract of /campagna:
- nothing works without the DM password, on any device (this PC included);
  wrong passwords are throttled and a new password ends old sessions;
- the chat ends with a <campagna> brief the server checks (enums, party size,
  a folder that doesn't exist yet) — Claude fixes a bad one unseen;
- the generation writes world.md, npcs.md and state.md from the templates,
  rejects a file with missing sections, and creates the campaign folder only
  when all three are good.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
PHONE = {"REMOTE_ADDR": "192.168.1.50"}
LOCAL = {"REMOTE_ADDR": "127.0.0.1"}


def _load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _brief(**over):
    d = {"name": "La Torbiera", "ruleset": "2014", "roll_mode": "players", "party_size": 4,
         "start_level": 1, "tone": "dark fantasy", "magic": "low", "setting": "medieval",
         "danger": "gritty", "premise": "I morti della torbiera si svegliano.",
         "settlement": "Rocca Salvia", "threat": "i morti", "mystery": "chi scava", "wishes": "", "arc": "dynamic"}
    d.update(over)
    return d


def _good_file(fname: str, brief: dict) -> str:
    """A minimal file that passes check_file()."""
    if fname == "world.md":
        return "\n".join(["# World: x", "## Campaign Tone & Genre", "## World Foundations", "## The Settlement — R",
                          "## The Nearby Threat — T", "## The Mystery — M", "## Factions", "## Quest Seed Bank", "## NPCs"])
    if fname == "npcs.md":
        return "\n".join(["# NPCs — x", "| Name | Role | Faction |"]
                         + [f"### N{i}\n- **Role:** r\n### Relationships\n- **Knows:** a" for i in range(3)])
    return "\n".join(["# Campaign: x", f"**Created:** d  **Session count:** 0  **Ruleset:** {brief['ruleset']}",
                      "## Current Situation", "## World State", "## Campaign Arc", "```yaml",
                      f"type: {brief['arc']}", "```", "## Session Flags", f"roll_mode: {brief['roll_mode']}",
                      "## DM Notes (hidden from players)"])


class CampCreateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _load(DISPLAY / "dnd-display-app.py", "camp_display_app_under_test")
        cls.cp = cls.app._camp

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.root = root
        (root / "rt").mkdir()
        self._saved_rt = self.cp.rt
        self.cp.rt = lambda name: str(root / "rt" / name)
        self._saved_env = os.environ.get("DND_CAMPAIGN_ROOT")
        os.environ["DND_CAMPAIGN_ROOT"] = str(root)
        self.cp._fails.clear()
        self.cp._generating.clear()
        self.cp._busy.clear()
        self.client = self.app.app.test_client()

    def tearDown(self):
        self.cp.rt = self._saved_rt
        if self._saved_env is None:
            os.environ.pop("DND_CAMPAIGN_ROOT", None)
        else:
            os.environ["DND_CAMPAIGN_ROOT"] = self._saved_env
        self.tmp.cleanup()

    def _post(self, path, body, env=PHONE):
        return self.client.post(path, data=json.dumps(body), content_type="application/json", environ_base=env)

    # ── password ──
    def test_password_is_hashed_and_checked(self):
        self.cp.set_password("Segreto-lungo")
        raw = (self.root / "rt" / "campaign_password.json").read_text(encoding="utf-8")
        self.assertNotIn("Segreto-lungo", raw)
        self.assertTrue(self.cp.check_password("Segreto-lungo"))
        self.assertFalse(self.cp.check_password("segreto-lungo"))
        self.assertFalse(self.cp.check_password(""))
        with self.assertRaises(ValueError):
            self.cp.set_password("corta")

    def test_new_password_ends_old_sessions(self):
        self.cp.set_password("Segreto-lungo")
        sid = self.cp.new_session()
        self.assertTrue(self.cp.session_ok(sid))
        self.cp.set_password("Altro-segreto")
        self.assertFalse(self.cp.session_ok(sid))
        self.assertFalse(self.cp.session_ok("inventato"))

    def test_routes_need_the_password_even_from_this_pc(self):
        self.cp.set_password("Segreto-lungo")
        for env in (PHONE, LOCAL):
            self.assertEqual(self._post("/campagna/start", {}, env=env).status_code, 401)
        self.assertEqual(self.client.get("/campagna/conv/" + "a" * 32, environ_base=PHONE).status_code, 401)
        page = self.client.get("/campagna", environ_base=PHONE)
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"authed: false", page.data)

    def test_login_then_chat(self):
        self.cp.set_password("Segreto-lungo")
        self.assertEqual(self._post("/campagna/login", {"password": "sbagliata"}).status_code, 403)
        r = self._post("/campagna/login", {"password": "Segreto-lungo"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("HttpOnly", r.headers.get("Set-Cookie", ""))
        r = self._post("/campagna/start", {})
        self.assertEqual(r.status_code, 200)
        cid = r.get_json()["id"]
        self.assertEqual(self.client.get("/campagna/conv/" + cid, environ_base=PHONE).status_code, 200)
        self.assertIn(b"authed: true", self.client.get("/campagna", environ_base=PHONE).data)

    def test_wrong_passwords_are_throttled(self):
        self.cp.set_password("Segreto-lungo")
        for _ in range(self.cp.FAIL_IP_MAX):
            self._post("/campagna/login", {"password": "no"})
        r = self._post("/campagna/login", {"password": "Segreto-lungo"})
        self.assertEqual(r.status_code, 429)

    def test_no_password_means_closed(self):
        self.assertFalse(self.cp.check_password(""))
        self.assertEqual(self._post("/campagna/login", {"password": "qualsiasi"}).status_code, 403)

    # ── the brief ──
    def test_brief_is_validated(self):
        b, p = self.cp.validate_brief(_brief())
        self.assertEqual(p, [])
        self.assertEqual(b["folder"], "la-torbiera")
        for why, data in {"tone": _brief(tone="cyberpunk"), "rules": _brief(ruleset="2020"),
                          "party": _brief(party_size=0), "level": _brief(start_level=25),
                          "name": _brief(name="!!!")}.items():
            b, p = self.cp.validate_brief(data)
            self.assertIsNone(b, why)
            self.assertTrue(p, why)

    def test_existing_folder_is_refused(self):
        (self.root / "campaigns" / "la-torbiera").mkdir(parents=True)
        b, p = self.cp.validate_brief(_brief())
        self.assertIsNone(b)
        self.assertIn("esiste già", p[0])

    def test_chat_reaches_a_brief_and_bad_ones_are_fixed_unseen(self):
        conv = self.cp.new_conv()
        replies = iter([
            "Ecco. <campagna>" + json.dumps(_brief(tone="cyberpunk")) + "</campagna>",
            "Fatto! Scrivo a mario@example.com <campagna>" + json.dumps(_brief()) + "</campagna>",
        ])
        out = self.cp.handle_message(conv, "Confermo", ask=lambda c: next(replies))
        self.assertTrue(out["ready"])
        self.assertNotIn("example.com", out["reply"])
        view = self.cp.public_view(conv)
        self.assertEqual([m["role"] for m in view["history"]], ["guide", "player", "guide"])
        self.assertEqual(view["brief"]["folder"], "la-torbiera")

    def test_failed_reply_drops_the_message(self):
        conv = self.cp.new_conv()

        def boom(c):
            raise RuntimeError("timeout")
        with self.assertRaises(RuntimeError):
            self.cp.handle_message(conv, "ciao", ask=boom)
        self.assertEqual(len(conv["history"]), 1)

    # ── generation ──
    def test_generation_writes_the_campaign(self):
        conv = self.cp.new_conv()
        conv["brief"], _ = self.cp.validate_brief(_brief(tone="random"))
        calls = []

        def fake(system, prompt):
            fname = prompt.rsplit("Scrivi ora ", 1)[1].split(" ", 1)[0]
            calls.append(fname)
            if fname == "npcs.md" and calls.count("npcs.md") == 1:
                return "<file># NPCs senza schede</file>"          # rejected, then fixed
            return "<file>" + _good_file(fname, conv["resolved"]) + "</file>"

        self.cp.generate(conv, ask=fake, today="2026-10-02")
        self.assertEqual(conv["gen"]["status"], "done", conv["gen"])
        self.assertEqual(calls, ["world.md", "npcs.md", "npcs.md", "state.md"])
        self.assertIn(conv["resolved"]["tone"], self.cp.TONES)
        dest = self.root / "campaigns" / "la-torbiera"
        for f in ("world.md", "npcs.md", "state.md", "session-log.md"):
            self.assertTrue((dest / f).exists(), f)
        self.assertTrue((dest / "characters").is_dir())
        self.assertIn("la-torbiera", (dest / "session-log.md").read_text(encoding="utf-8"))
        self.assertTrue(self.cp.public_view(conv)["finished"])

    def test_generation_stops_on_a_bad_file_and_writes_nothing(self):
        conv = self.cp.new_conv()
        conv["brief"], _ = self.cp.validate_brief(_brief())
        self.cp.generate(conv, ask=lambda s, p: "<file># niente</file>", today="2026-10-02")
        self.assertEqual(conv["gen"]["status"], "error")
        self.assertFalse((self.root / "campaigns" / "la-torbiera").exists())

    def test_state_must_match_the_brief(self):
        b, _ = self.cp.validate_brief(_brief(roll_mode="auto"))
        body = _good_file("state.md", dict(b, roll_mode="players"))
        self.assertTrue(self.cp.check_file("state.md", body, b))
        self.assertEqual(self.cp.check_file("state.md", _good_file("state.md", b), b), [])

    def test_leftover_placeholders_are_rejected(self):
        b, _ = self.cp.validate_brief(_brief())
        body = _good_file("world.md", b) + "\n- **Biome:** <primary terrain>"
        self.assertTrue(self.cp.check_file("world.md", body, b))

    def test_procedure_is_read_from_the_skill(self):
        self.assertIn("Three Truths", self.cp._procedure())


if __name__ == "__main__":
    unittest.main()
