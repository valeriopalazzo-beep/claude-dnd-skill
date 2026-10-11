"""
test_split.py — the party is in more than one place.

display/split.py keeps who is with whom; send.py --group / --only addresses a
piece of the story to some characters, and the display hands it only to their
players. The DM's screen shows everything, with a line saying whose scene it is.

Run from repo root:
    python -m pytest tests/test_split.py -q
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

import media  # noqa: E402
import split as sp  # noqa: E402

PARTY = ["Ajeje", "Hernio", "Lio", "Lyra Ombrafina", "Vardamir Ma'feyn"]


class GroupTests(unittest.TestCase):
    def test_characters_are_written_by_any_part_that_means_one(self):
        self.assertEqual(sp.characters(PARTY, " lyra ; vardamir, LYRA"), ["Lyra Ombrafina", "Vardamir Ma'feyn"])
        for bad in ("l", "Gandalf", "", " , "):
            with self.assertRaises(sp.SplitError):
                sp.characters(PARTY, bad)

    def test_a_split_puts_each_character_in_one_group(self):
        groups = sp.split(PARTY, ["Collina: Hernio, Lyra", " Cappella :lio,vardamir", "Rocca: ajeje"])
        self.assertEqual(groups, [{"name": "Collina", "members": ["Hernio", "Lyra Ombrafina"]},
                                  {"name": "Cappella", "members": ["Lio", "Vardamir Ma'feyn"]},
                                  {"name": "Rocca", "members": ["Ajeje"]}])
        self.assertEqual(sp.left_out(groups, PARTY), [])
        self.assertEqual(sp.left_out(sp.split(PARTY, ["A: Hernio", "B: Lio"]), PARTY), ["Ajeje", "Lyra Ombrafina", "Vardamir Ma'feyn"])
        for bad in (["Collina: Hernio"], ["Collina: Hernio", "Rocca: hernio"], ["Collina: Hernio", "collina: Lio"],
                    ["Hernio e Lyra", "Rocca: Ajeje"], [": Hernio", "Rocca: Ajeje"], ["Collina:", "Rocca: Ajeje"]):
            with self.assertRaises(sp.SplitError):
                sp.split(PARTY, bad)

    def test_someone_moves_to_another_group_or_off_alone(self):
        groups = sp.split(PARTY, ["Collina: Hernio, Lyra", "Rocca: Ajeje"])
        groups, who, where = sp.move(groups, PARTY, "ajeje", "coll")
        self.assertEqual((who, where, groups), ("Ajeje", "Collina", [{"name": "Collina", "members": ["Hernio", "Lyra Ombrafina", "Ajeje"]}]))
        groups = sp.split(PARTY, ["Collina: Hernio, Lyra", "Rocca: Ajeje"])
        groups, _, where = sp.move(groups, PARTY, "lyra", "Sentiero")                     # a new group of one
        self.assertEqual((where, [g["name"] for g in groups], sp.group(groups, "sent")["members"]),
                         ("Sentiero", ["Collina", "Rocca", "Sentiero"], ["Lyra Ombrafina"]))
        groups, _, _ = sp.move(groups, PARTY, "lio", "rocca")                              # someone who was in no group
        self.assertEqual(sp.group(groups, "rocca")["members"], ["Ajeje", "Lio"])
        with self.assertRaises(sp.SplitError):
            sp.move([], PARTY, "ajeje", "Rocca")
        with self.assertRaises(sp.SplitError):
            sp.move(groups, PARTY, "gandalf", "Rocca")

    def test_a_group_is_found_by_part_of_its_name(self):
        groups = sp.split(PARTY, ["Rocca Muta: Ajeje", "Rocca del Corvo: Lio"])
        self.assertEqual(sp.group(groups, "muta")["members"], ["Ajeje"])
        for bad in ("rocca", "collina", ""):
            with self.assertRaises(sp.SplitError):
                sp.group(groups, bad)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._old = os.environ.get("DND_CAMPAIGN_ROOT")
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        chars = self.root / "campaigns" / "prova" / "characters"
        chars.mkdir(parents=True)
        for name in PARTY:
            (chars / (re.sub(r"[^a-z]+", "-", name.lower()).strip("-") + ".md")).write_text(f"# {name}\n- **Class:** Ladro\n", encoding="utf-8")
        self.events = []
        self._saved = (sp._announce, media.active_campaign)
        sp._announce = lambda event="": self.events.append(event)
        media.active_campaign = lambda: "prova"

    def tearDown(self):
        sp._announce, media.active_campaign = self._saved
        if self._old is None:
            os.environ.pop("DND_CAMPAIGN_ROOT", None)
        else:
            os.environ["DND_CAMPAIGN_ROOT"] = self._old
        self._tmp.cleanup()

    def run_sp(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sp.main(["--campaign", "prova", *argv])
        return code, out.getvalue() + err.getvalue()

    def saved(self):
        return sp.load(str(self.root / "campaigns" / "prova" / "split.json"))

    def test_the_party_splits_shifts_and_comes_back_together(self):
        self.assertEqual(sp.party("prova"), PARTY)
        self.assertIn("the party is together", self.run_sp("show")[1])
        code, out = self.run_sp("set", "Collina: Hernio, Lyra", "Rocca: Ajeje")
        self.assertEqual(code, 0, out)
        self.assertIn("in no group (they read only what is sent to everyone): Lio, Vardamir Ma'feyn", out)
        self.assertEqual(sp.audience("prova", "coll"), (["Hernio", "Lyra Ombrafina"], "Collina"))
        self.assertEqual(sp.audience("prova", only="lio, vardamir"), (["Lio", "Vardamir Ma'feyn"], ""))
        for kwargs in ({"group_name": "cappella"}, {"group_name": "collina", "only": "lio"}, {"only": "gandalf"}):
            with self.assertRaises(sp.SplitError):
                sp.audience("prova", **kwargs)
        self.assertEqual(self.run_sp("rename", "rocca", "Scala della Rocca")[0], 0)
        self.assertEqual(self.run_sp("move", "lio", "scala")[0], 0)
        self.assertEqual(sp.group(self.saved(), "scala")["members"], ["Ajeje", "Lio"])
        self.assertEqual(self.run_sp("move", "hernio", "scala")[0], 0)
        self.assertEqual(self.run_sp("move", "lyra", "scala")[0], 0)                       # the last two groups meet
        self.assertEqual((self.saved(), self.events), ([], ["split", "moved", "moved", "moved", "joined"]))
        self.run_sp("set", "A: Hernio", "B: Lio")
        self.assertEqual(self.run_sp("join")[0], 0)
        self.assertEqual((self.saved(), self.events[-1]), ([], "joined"))

    def test_a_mistake_changes_nothing(self):
        self.run_sp("set", "Collina: Hernio, Lyra", "Rocca: Ajeje")
        before, announced = self.saved(), len(self.events)
        for argv in (("set", "Collina: Hernio"), ("set", "A: Hernio", "B: Gandalf"), ("move", "gandalf", "Rocca"),
                     ("rename", "rocca", "collina"), ("rename", "boh", "x"), ("rename", "rocca", " ")):
            code, out = self.run_sp(*argv)
            self.assertEqual(code, 1, argv)
            self.assertIn("Nothing was changed", out)
        self.assertEqual(self.saved(), before)
        self.assertEqual(len(self.events), announced)
        self.run_sp("join")
        self.assertEqual(self.run_sp("join")[0], 1)                                        # together already

    def test_send_refuses_a_group_that_does_not_exist_and_sends_nothing(self):
        env = dict(os.environ, PYTHONUTF8="1")
        r = subprocess.run([sys.executable, str(DISPLAY / "send.py"), "--group", "Collina"], input="Solo per loro.\n",
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual(r.returncode, 2, r.stderr[-400:])
        self.assertIn("Nothing was sent", r.stderr)

    def test_it_runs_as_a_script(self):
        env = dict(os.environ, PYTHONUTF8="1")
        r = subprocess.run([sys.executable, str(DISPLAY / "split.py"), "--campaign", "prova", "show"],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "the party is together"), r.stderr[-400:])


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_split", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        saved = (self.mod._active_campaign, self.mod._find_campaign, self.mod._broadcast)
        self.addCleanup(lambda: setattr(self.mod, "_active_campaign", saved[0]) or setattr(self.mod, "_find_campaign", saved[1])
                        or setattr(self.mod, "_broadcast", saved[2]))
        self.sent = []
        self.mod._broadcast = self.sent.append

    def test_a_piece_carries_who_it_is_for(self):
        r = self.client.post("/chunk", json={"text": "I morti si voltano.", "only": ["Hernio", " Lyra Ombrafina ", ""],
                                             "only_label": "Collina"})
        self.assertEqual(r.status_code, 204)
        self.assertEqual((self.sent[-1]["only"], self.sent[-1]["only_label"]), (["Hernio", "Lyra Ombrafina"], "Collina"))
        with self.mod._text_log_lock:
            logged = list(self.mod._text_log)[-1]
        self.assertEqual((logged["text"], logged["only"], logged["only_label"]), ("I morti si voltano.", ["Hernio", "Lyra Ombrafina"], "Collina"))
        self.client.post("/chunk", json={"text": "«Giù. Subito.»", "npc": "L'adepto", "only": ["Ajeje"]})
        self.assertEqual((self.sent[-1]["npc"], self.sent[-1]["only"]), ("L'adepto", ["Ajeje"]))
        self.assertNotIn("only_label", self.sent[-1])
        for body in ({"text": "Per tutti."}, {"text": "Per tutti.", "only": []}, {"text": "Per tutti.", "only": "Ajeje"}):
            self.client.post("/chunk", json=body)
            self.assertNotIn("only", self.sent[-1])

    def test_players_elsewhere_get_only_a_sign_that_something_happens(self):
        scene = self.mod._scene_for
        piece = {"text": "I morti si voltano.", "only": ["Hernio", "Lyra Ombrafina"], "only_label": "Collina", "scene": {"name": "crypt"}}
        self.assertIs(scene(piece, "hernio", ""), piece)
        self.assertIs(scene(piece, "lyra ombrafina", ""), piece)
        self.assertEqual(scene(piece, "ajeje", ""), {"elsewhere": True})                   # no text, no change of scene
        self.assertIs(scene(piece, None, ""), piece)                                       # the DM's screen: everything
        self.assertEqual(scene(piece, None, "ajeje"), {"elsewhere": True})                 # a phone view opened on this PC
        everyone = {"text": "La campana suona."}
        self.assertIs(scene(everyone, "ajeje", ""), everyone)
        self.assertIsNone(scene(None, "ajeje", ""))
        picture = {"image": {"file": "x.png"}, "only": ["Ajeje"]}
        self.assertEqual(scene(picture, "lio", ""), {"elsewhere": True})

    def test_a_replay_keeps_each_players_own_scenes(self):
        batch = [{"text": "Tutti."}, {"text": "Collina 1", "only": ["Hernio"]}, {"text": "Collina 2", "only": ["Hernio"]},
                 {"text": "Rocca", "only": ["Ajeje"]}, {"text": "Tutti di nuovo."}, {"text": "Collina 3", "only": ["Hernio"]}]
        seen = self.mod._scene_for({"replay_batch": batch}, "ajeje", "")["replay_batch"]
        self.assertEqual(seen, [{"text": "Tutti."}, {"elsewhere": True}, {"text": "Rocca", "only": ["Ajeje"]},
                                {"text": "Tutti di nuovo."}, {"elsewhere": True}])
        self.assertEqual(len(batch), 6, "the log itself is not touched")
        self.assertEqual(len(self.mod._scene_for({"replay_batch": batch}, None, "")["replay_batch"]), 6)

    def test_the_dm_sees_every_group_and_a_player_their_own(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        camp = pathlib.Path(tmp.name)
        groups = sp.split(PARTY, ["Collina: Hernio, Lyra", "Rocca: Ajeje"])
        sp.save(str(camp / "split.json"), groups)
        self.mod._active_campaign, self.mod._find_campaign = (lambda: "prova"), (lambda n: camp)
        self.assertEqual([g["name"] for g in self.client.get("/split").get_json()], ["Collina", "Rocca"])
        self.assertEqual(self.client.post("/split", json={"event": "split"}).status_code, 204)
        self.assertEqual((len(self.sent[-1]["split"]), self.sent[-1]["split_event"]), (2, "split"))
        mine = self.mod._scene_for(self.sent[-1], "ajeje", "")
        self.assertEqual((mine["split"], mine["split_event"]), ([{"name": "Rocca", "members": ["Ajeje"]}], "split"))
        self.assertEqual(self.mod._scene_for(self.sent[-1], "lio", "")["split"], [])
        self.client.post("/split", json={"event": "boh"})
        self.assertEqual(self.sent[-1]["split_event"], "")
        self.assertIn("split_changed", self.mod._DM_ENDPOINTS)
        self.assertNotIn("split_get", self.mod._DM_ENDPOINTS)
        self.mod._active_campaign = lambda: ""
        self.assertEqual(self.client.get("/split").get_json(), [])

    def test_the_stream_filters_after_the_whisper_filter(self):
        src = (DISPLAY / "dnd-display-app.py").read_text(encoding="utf-8")
        self.assertIn("payload = _scene_for(payload, _viewer, _ch)", src)


_SLICE = re.compile(r"(const _SPLIT_ELSEWHERE.*?)\n// ── end split step ──", re.S)


class PageTests(unittest.TestCase):
    def test_a_line_is_drawn_when_the_scene_changes_hands(self):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the split block is no longer where this test slices it")
        collina = {"only": ["Lyra", "Hernio"], "label": "Collina", "narration": True}
        same = {"only": ["hernio", "lyra"], "label": "", "narration": False}
        rocca = {"only": ["Ajeje"], "label": "Rocca", "narration": True}
        told = {"only": None, "narration": True}
        rolled = {"only": None, "narration": False}
        steps = [("", told), ("", collina), ("hernio|lyra", same), ("hernio|lyra", rocca), ("ajeje", rolled), ("ajeje", told),
                 ("ELSE", told), ("ELSE", rolled), ("ELSE", rocca)]
        script = m.group(1) + "\nconst steps = %s;\nconsole.log(JSON.stringify(steps.map(s => _splitStep(s[0] === 'ELSE' ? _SPLIT_ELSEWHERE : s[0], s[1]))));\n" % json.dumps(steps)
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            got = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        self.assertEqual(got[0], ["", None])                                               # nobody is apart: nothing to say
        self.assertEqual(got[1], ["hernio|lyra", {"names": ["Lyra", "Hernio"], "label": "Collina"}])
        self.assertEqual(got[2], ["hernio|lyra", None])                                    # the same people: the same scene
        self.assertEqual(got[3], ["ajeje", {"names": ["Ajeje"], "label": "Rocca"}])
        self.assertEqual(got[4], ["ajeje", None])                                          # a stray roll does not end it
        self.assertEqual(got[5], ["", {"everyone": True}])
        self.assertEqual(got[6], ["", {"everyone": True}])                                 # back from elsewhere
        self.assertEqual(got[7][1], None)
        self.assertEqual(got[8], ["ajeje", {"names": ["Ajeje"], "label": "Rocca"}])

    def test_the_page_draws_the_lines_and_the_pill(self):
        src = HTML.read_text(encoding="utf-8")
        for needle in ('id="ln-split"', "charQueue.push({ split: si })", "item.split", "payload.elsewhere",
                       "(payload.split || payload.split_event)", ".split-mark"):
            self.assertIn(needle, src)

    def test_strings_exist_in_every_language(self):
        keys = ["split.pill", "split.everyone", "split.elsewhere", "split.with", "split.alone", "split.dm", "split.joined"]
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
