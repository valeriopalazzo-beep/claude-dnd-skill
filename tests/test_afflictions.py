"""
test_afflictions.py — diseases, poisons and madness that run their course.

display/afflictions.py keeps them in the campaign's afflictions.json (DMG
2014 pp. 256-260, the SRD): a disease incubates, shows, and at every dawn its
save goes to the character's phone; the display settles it when the roll
comes back (sewer plague moves the exhaustion, cackle fever lowers its DC,
pale tincture counts successes); sight rot worsens unless treated; poisons
last their hours; madness is short, long or indefinite with its effect read
from the SRD's tables in English and Italian.

Run from repo root:
    python -m pytest tests/test_afflictions.py -q
"""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import random
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(SKILL / "scripts"))

import afflictions as af  # noqa: E402

SHEET = """# Lyra
## Ability Scores
| STR | DEX | CON | INT | WIS | CHA |
|-----|-----|-----|-----|-----|-----|
| 8 (-1) | 16 (+3) | 14 (+2) | 12 (+1) | 10 (+0) | 16 (+3) |

## Saving Throws
| STR | DEX | CON | INT | WIS | CHA |
|-----|-----|-----|-----|-----|-----|
| -1 | +5* | +4* | +1 | +0 | +3 |
"""
SHEET_IT = """# Hernio
## Ability Scores
| FOR | DES | COS | INT | SAG | CAR |
|-----|-----|-----|-----|-----|-----|
| 8 (-1) | 17 (+3) | 13 (+1) | 10 (+0) | 12 (+1) | 15 (+2) |
"""


class Rng(random.Random):
    """A die that always shows the same face."""

    def __init__(self, face):
        super().__init__(0)
        self.face = face

    def randint(self, a, b):
        return max(a, min(b, self.face))


class CatalogueTests(unittest.TestCase):
    def test_names_in_both_languages(self):
        self.assertEqual(af.find("Epidemia fognaria"), ("disease", "sewer_plague"))
        self.assertEqual(af.find("cackle fever"), ("disease", "cackle_fever"))
        self.assertEqual(af.find("veleno di serpente"), ("poison", "serpent_venom"))
        self.assertEqual(af.find("Olio di Taggit"), ("poison", "oil_of_taggit"))
        with self.assertRaises(af.AfflictionError):
            af.find("raffreddore")

    def test_the_madness_tables_come_from_the_srd(self):
        for kind, rows in (("short", 10), ("long", 12), ("indefinite", 12)):
            for lang in ("en", "it"):
                with self.subTest(kind=kind, lang=lang):
                    table = af.madness_table(kind, lang)
                    self.assertEqual(len(table), rows)
                    self.assertEqual((table[0][0][0], table[-1][0][1]), (1, 100))
        self.assertIn("unconscious", af.madness_effect("short", 95)["en"])
        self.assertIn("perde i sensi", af.madness_effect("short", 95)["it"])
        self.assertEqual(af.madness_conditions("short", 5, Rng(1)), ["Paralyzed"])
        self.assertEqual(af.madness_conditions("long", 60, Rng(1)), ["Blinded"])
        self.assertEqual(af.madness_conditions("long", 60, Rng(4)), ["Deafened"])

    def test_save_bonus_from_the_sheet(self):
        self.assertEqual(af.save_bonus(SHEET, "CON"), 4)
        self.assertEqual(af.save_bonus(SHEET, "WIS"), 0)
        self.assertEqual(af.save_bonus(SHEET_IT, "CON"), 1, "no saves table: the ability, Italian headers")


class CourseTests(unittest.TestCase):
    def test_sewer_plague_incubates_then_asks_at_each_dawn(self):
        data = af.blank()
        entry, ch = af.infect(data, "Lyra", "sewer_plague", 100, Rng(2))      # symptoms in 2 days: hour 148
        self.assertEqual((entry["status"], entry["onset_h"], ch["event"]), ("incubating", 148, "secret"))
        self.assertEqual(af.public_view(data, "Lyra"), [], "the player does not know yet")
        changes = af.tick(data, 100, 148 + 10, Rng(2))   # one dawn after the symptoms (hour 150)
        self.assertEqual(changes[0]["add"], ["Exhaustion (1)"])
        self.assertEqual(entry["due"], 1, "only the dawn after the symptoms")
        ch = af.settle(data, entry, 5, Rng(2))
        self.assertEqual((entry["level"], ch["event"], ch["add"]), (2, "worse", ["Exhaustion (2)"]))
        self.assertIn("Exhaustion (1)", ch["remove"])
        entry["due"] = 2
        af.settle(data, entry, 15, Rng(2))
        ch = af.settle(data, entry, 15, Rng(2))
        self.assertEqual((ch["event"], data["list"], ch["remove"][:1]), ("cured", [], ["Exhaustion (1)"]))

    def test_cackle_fever_drops_its_dc_and_three_failures_bring_madness(self):
        data = af.blank()
        entry, _ = af.infect(data, "Lyra", "cackle_fever", 10, Rng(1))
        af.tick(data, 10, 11, Rng(1))
        self.assertEqual((entry["status"], entry["conditions"]), ("active", ["Exhaustion (1)"]))
        af.settle(data, entry, 20, Rng(4))
        self.assertEqual(entry["dc"], 9)
        for _ in range(3):
            ch = af.settle(data, entry, 1, Rng(4))
        self.assertEqual(entry["failures"], 3)
        self.assertTrue(any(e["kind"] == "madness" and e["key"] == "indefinite" for e in data["list"]))
        self.assertIn("indefinite", " ".join(ch["lines"]))
        ch = af.settle(data, entry, 20, Rng(6))
        ch = af.settle(data, entry, 20, Rng(6))
        self.assertEqual(ch["event"], "cured")

    def test_sight_rot_worsens_unless_treated_and_three_doses_cure(self):
        data = af.blank()
        entry, _ = af.infect(data, "Lyra", "sight_rot", 0, Rng(1))
        af.tick(data, 0, 24 + DAWN(), Rng(1))
        self.assertEqual(entry["level"], 2)
        af.treat(data, entry)
        af.tick(data, 24 + DAWN(), 48 + DAWN(), Rng(1))
        self.assertEqual(entry["level"], 2, "the ointment held it")
        af.tick(data, 48 + DAWN(), 48 * 3 + DAWN(), Rng(1))
        self.assertEqual((entry["level"], entry["conditions"]), (5, ["Blinded"]))
        af.treat(data, entry)
        ch = af.treat(data, entry)
        self.assertEqual((ch["event"], ch["remove"]), ("cured", ["Blinded"]))

    def test_poisons(self):
        data = af.blank()
        ch = af.poison(data, "Lyra", "serpent_venom", 15, 0, Rng(4))
        self.assertEqual((ch["damage"], data["list"]), (6, []), "half of 3d6 on a success, nothing lasts")
        ch = af.poison(data, "Lyra", "oil_of_taggit", 3, 0, Rng(4))
        self.assertEqual(ch["add"], ["Poisoned", "Unconscious"])
        self.assertEqual(data["list"][0]["until_h"], 24)
        ch = af.poison(data, "Ajeje", "drow_poison", 7, 0, Rng(4))
        self.assertIn("Unconscious", ch["add"], "failed by 5 or more")
        changes = af.tick(data, 0, 24, Rng(4))
        self.assertEqual(sorted(c["event"] for c in changes), ["cured", "cured"])
        self.assertEqual(data["list"], [])

    def test_pale_tincture_counts_seven_successes(self):
        data = af.blank()
        ch = af.poison(data, "Lyra", "pale_tincture", 2, 0, Rng(3))
        self.assertEqual((ch["damage"], ch["add"]), (3, ["Poisoned"]))
        entry = data["list"][0]
        af.tick(data, 0, 30, Rng(3))
        self.assertEqual(entry["due"], 1)
        self.assertEqual(af.settle(data, entry, 5, Rng(3))["damage"], 3)
        for _ in range(6):
            af.settle(data, entry, 20, Rng(3))
        ch = af.settle(data, entry, 20, Rng(3))
        self.assertEqual((ch["event"], ch["remove"]), ("cured", ["Poisoned"]))

    def test_midnight_tears_strike_at_midnight(self):
        data = af.blank()
        ch = af.poison(data, "Lyra", "midnight_tears", None, 18, Rng(3))
        self.assertEqual(ch["event"], "secret")
        entry = data["list"][0]
        af.tick(data, 18, 23, Rng(3))
        self.assertEqual(entry["due"], 0)
        af.tick(data, 23, 24, Rng(3))
        self.assertEqual(entry["due"], 1)
        ch = af.settle(data, entry, 20, Rng(3))
        self.assertEqual((ch["damage"], data["list"]), (13, []), "half of 9d6 of threes")

    def test_madness(self):
        data = af.blank()
        ch = af.madness(data, "Lyra", "breve", 10, Rng(3), d100=5)
        self.assertEqual(ch["add"], [{"name": "Paralyzed", "duration_type": "rounds", "duration_remaining": 30}])
        ch = af.madness(data, "Lyra", "lunga", 10, Rng(3), d100=30)
        self.assertEqual((ch["add"], data["list"][1]["until_h"]), ([], 40))
        af.madness(data, "Lyra", "indefinita", 10, Rng(3), d100=99)
        changes = af.tick(data, 10, 50, Rng(3))
        self.assertEqual(len(changes), 2)
        self.assertEqual([e["key"] for e in data["list"]], ["indefinite"])
        with self.assertRaises(af.AfflictionError):
            af.madness(data, "Lyra", "eterna", 10, Rng(3))

    def test_a_condition_another_affliction_needs_stays(self):
        data = af.blank()
        af.poison(data, "Lyra", "oil_of_taggit", 3, 0, Rng(4))
        af.poison(data, "Lyra", "truth_serum", 3, 0, Rng(4))
        ch = af.end(data, data["list"][0], "cured")
        self.assertEqual(ch["remove"], ["Unconscious"], "Poisoned is still the truth serum's")


def DAWN():
    return af.DAWN_HOUR


class CommandTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        chars = self.root / "campaigns" / "prova" / "characters"
        chars.mkdir(parents=True)
        (chars / "lyra.md").write_text(SHEET, encoding="utf-8")
        (self.root / "rt").mkdir()
        (self.root / "rt" / ".campaign").write_text("prova", encoding="utf-8")
        for var, value in (("DND_CAMPAIGN_ROOT", str(self.root)), ("DND_RUNTIME_DIR", str(self.root / "rt"))):
            old = os.environ.get(var)
            os.environ[var] = value
            self.addCleanup(lambda var=var, old=old: os.environ.pop(var, None) if old is None else os.environ.__setitem__(var, old))
        self.posts, self.asked = [], []
        saved = (af._post, af._ask, af._active_campaign)
        af._active_campaign = lambda: "prova"      # media keeps the first runtime folder it sees
        af._post = lambda path, body: self.posts.append((path, body)) or {}
        af._ask = lambda name, mod, label, dc, timeout: self.asked.append((name, mod, label, dc)) or self.total
        self.total = 5
        self.addCleanup(lambda: (setattr(af, "_post", saved[0]), setattr(af, "_ask", saved[1]),
                                 setattr(af, "_active_campaign", saved[2])))

    def run_af(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = af.main(["--campaign", "prova", *argv])
        return code, out.getvalue() + err.getvalue()

    def saved(self):
        return af.load(str(self.root / "campaigns" / "prova" / "afflictions.json"))

    def test_a_poison_from_the_phone_to_the_display(self):
        code, out = self.run_af("expose", "lyra", "olio di taggit")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.asked[-1][:2], ("Lyra", 4))
        self.assertEqual(self.posts[-1][0], "/afflictions/apply")
        self.assertEqual(self.posts[-1][1]["add"], ["Poisoned", "Unconscious"])
        self.assertIn("write it on the sheet", out)
        self.assertEqual(len(self.saved()["list"]), 1)
        code, out = self.run_af("cure", "Lyra", "--all")
        self.assertEqual((code, self.saved()["list"]), (0, []))
        self.assertEqual(self.posts[-1][1]["remove"], ["Poisoned", "Unconscious"])

    def test_a_disease_resisted_infects_nobody(self):
        self.total = 15
        code, out = self.run_af("expose", "Lyra", "sewer plague")
        self.assertIn("not infected", out)
        self.assertEqual(self.saved()["list"], [])
        self.assertEqual(self.run_af("expose", "Lyra", "epidemia fognaria", "--total", "3")[0], 0)
        self.assertEqual(self.saved()["list"][0]["status"], "incubating")

    def test_madness_custom_resolve_and_mistakes(self):
        code, out = self.run_af("madness", "Lyra", "indefinite", "--d100", "50")
        self.assertIn("I find it hard to care", out)
        self.assertEqual(self.run_af("madness", "Lyra", "short", "--save", "SAG", "--dc", "15", "--total", "16")[0], 0)
        self.assertEqual(len(self.saved()["list"]), 1, "resisted")
        code, out = self.run_af("custom", "Lyra", "Febbre di palude", "--dc", "12", "--effect", "Brividi")
        self.assertEqual(code, 0, out)
        code, out = self.run_af("resolve", "Lyra", "febbre di palude", "--total", "3")
        self.assertIn("exhaustion 2", out)
        before = self.saved()
        for argv in (("add", "Gandalf", "malizia"), ("add", "Lyra", "raffreddore"), ("cure", "Lyra"),
                     ("madness", "Lyra", "eterna"), ("custom", "Lyra", "X", "--dc", "99")):
            code, out = self.run_af(*argv)
            self.assertEqual(code, 1, argv)
            self.assertIn("Nothing was changed", out)
        self.assertEqual(self.saved(), before)

    def test_the_calendar_runs_the_course(self):
        cal = importlib.util.spec_from_file_location("calendar_under_test_afflictions", str(SKILL / "scripts" / "calendar.py"))
        mod = importlib.util.module_from_spec(cal)
        cal.loader.exec_module(mod)
        self.run_af("custom", "Lyra", "Febbre di palude", "--dc", "12")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            mod._afflictions("prova", 20, 20 + 24)
        self.assertIn("saves due: Lyra (Febbre di palude, DC 12)", out.getvalue())
        self.assertIn(("/afflictions/ask", {}), self.posts)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_afflictions", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.mod
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.camp = pathlib.Path(tmp.name)
        (self.camp / "lyra.md").write_text(SHEET, encoding="utf-8")
        m = self.mod
        saved = (m._active_campaign, m._find_campaign, m._broadcast, m._find_sheet_file, m._persist_stats, m._spell_lang,
                 m.secrets.randbelow, m._phone_present, af.notes_add)
        self.addCleanup(lambda: (setattr(m, "_active_campaign", saved[0]), setattr(m, "_find_campaign", saved[1]),
                                 setattr(m, "_broadcast", saved[2]), setattr(m, "_find_sheet_file", saved[3]),
                                 setattr(m, "_persist_stats", saved[4]), setattr(m, "_spell_lang", saved[5]),
                                 setattr(m.secrets, "randbelow", saved[6]), setattr(m, "_phone_present", saved[7]),
                                 setattr(af, "notes_add", saved[8])))
        self.sent, self.notes = [], []
        m._active_campaign, m._find_campaign, m._broadcast = (lambda: "prova"), (lambda n: self.camp), self.sent.append
        m._find_sheet_file = lambda name: (str(self.camp / "lyra.md"), name, "prova")
        m._persist_stats = lambda: None
        m._spell_lang = lambda value="": "it"
        m._phone_present = lambda name: True
        af.notes_add = self.notes.append
        with m._stats_lock:
            m._current_stats["players"] = [{"name": "Lyra", "hp": {"current": 20, "max": 20, "temp": 2}, "conditions": []}]

    def player(self):
        return self.mod._current_stats["players"][0]

    def test_a_dawn_save_goes_to_the_phone_and_comes_back_applied(self):
        data = af.blank()
        entry, _ = af.custom(data, "Lyra", "Epidemia", None, 11)
        entry["due"] = 2
        af.save(str(self.camp / "afflictions.json"), data)
        self.client.post("/stats", json={"players": [{"name": "Lyra", "_conditions_add": "Exhaustion (1)"}]})
        r = self.client.post("/afflictions/ask", json={})
        self.assertEqual(r.get_json()["asked"], ["Lyra"])
        req = [p["dice_request"] for p in self.sent if "dice_request" in p][-1]
        self.assertEqual((req["label"], req["modifier"], req["dc"]), ("TS Costituzione: Epidemia", 4, 11))
        self.mod.secrets.randbelow = lambda n: 2           # a natural 3: 7, failed
        r = self.client.post("/player-input/dice", json={"character": "Lyra", "spec": "1d20", "modifier": 4,
                                                           "request_id": req["request_id"]})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Exhaustion (2)", self.player()["conditions"])
        self.assertNotIn("Exhaustion (1)", self.player()["conditions"])
        self.assertIn("[[AFFLICTION:", self.notes[-1])
        self.assertIn({"afflictions_event": {"character": "Lyra", "what": "worse", "name": "Epidemia"}}, self.sent)
        again = [p["dice_request"] for p in self.sent if "dice_request" in p][-1]
        self.assertNotEqual(again["request_id"], req["request_id"], "the second save due is asked at once")
        view = self.client.get("/afflictions?character=Lyra").get_json()["afflictions"]
        self.assertEqual((view[0]["level"], view[0]["save_due"]), (2, True))

    def test_apply_takes_damage_from_temporary_hit_points_first(self):
        r = self.client.post("/afflictions/apply", json={"character": "Lyra", "add": ["Poisoned"], "remove": [],
                                                          "damage": 5, "event": "hit", "name": "Serpent venom"})
        self.assertEqual(r.status_code, 204)
        self.assertEqual((self.player()["hp"]["current"], self.player()["hp"]["temp"]), (17, 0))
        self.assertEqual(self.player()["conditions"], ["Poisoned"])
        self.client.post("/afflictions/apply", json={"character": "Lyra", "remove": ["Poisoned"], "event": "secret"})
        self.assertEqual(self.player()["conditions"], [])
        self.assertNotIn("afflictions_event", self.sent[-1], "a secret is not told to the screens")
        for ep in ("afflictions_apply", "afflictions_ask"):
            self.assertIn(ep, self.mod._DM_ENDPOINTS)

    def test_the_dm_screen_sees_what_is_incubating(self):
        data = af.blank()
        af.infect(data, "Lyra", "sewer_plague", None, Rng(1))
        af.save(str(self.camp / "afflictions.json"), data)
        self.assertEqual(self.client.get("/afflictions?character=Lyra").get_json()["afflictions"], [])
        dm = self.client.get("/dm-screen/data").get_json()["afflictions"]
        self.assertEqual((dm[0]["who"], dm[0]["status"]), ("Lyra", "incubating"))


class PageTests(unittest.TestCase):
    def test_the_page_has_the_panel(self):
        html = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")
        for needle in ('id="ail-modal"', 'id="ln-ail"', 'id="ph-ail-opt"', "window._onAfflictions("):
            self.assertIn(needle, html)
        self.assertIn('id="s-ail"', (DISPLAY / "templates" / "dm.html").read_text(encoding="utf-8"))

    def test_strings_exist_in_every_language(self):
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("ail.title", "ail.kind.disease", "ail.st.exhaustion", "ail.st.dcdrop", "ail.due", "ail.toast.worse",
                        "ail.dc"):
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
