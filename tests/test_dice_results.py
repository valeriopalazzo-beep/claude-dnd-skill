"""
test_dice_results.py — send.py --wait hands the DM the numbers.

A dice request keeps what each prescribed character rolled; GET
/dice-request/<id> returns it, also once the request is complete, and
send.py --wait prints one line per character instead of a bare "all rolls
received".

Run from repo root:
    python -m pytest tests/test_dice_results.py -q
"""
import contextlib
import importlib.util
import io
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(SKILL / "scripts"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load("dnd_display_app_dice_results", DISPLAY / "dnd-display-app.py")
        cls.mod._token_ok = lambda: True
        cls.mod._broadcast = lambda payload: None
        cls.client = cls.mod.app.test_client()

    def request(self, **body):
        r = self.client.post("/dice-request", json=body)
        self.assertEqual(r.status_code, 200)
        return r.get_json()["request_id"]

    def roll(self, who, rid, **extra):
        r = self.client.post("/player-input/dice", json={"character": who, "spec": "1d20", "request_id": rid, **extra})
        self.assertEqual(r.status_code, 200)
        return r.get_json()

    def test_the_results_are_there_while_waiting_and_after(self):
        rid = self.request(characters=["Ajeje", "Lyra"], spec="1d20", modifier=3, label="Furtività", dc=15)
        first = self.roll("Ajeje", rid, modifier=3)
        st = self.client.get(f"/dice-request/{rid}").get_json()
        self.assertEqual((st["complete"], st["pending"], st["dc"], st["label"]), (False, ["Lyra"], 15, "Furtività"))
        self.assertEqual([(r["character"], r["total"]) for r in st["results"]], [("Ajeje", first["total"])])
        second = self.roll("lyra", rid, modifier=3, advantage="advantage")
        st = self.client.get(f"/dice-request/{rid}").get_json()
        self.assertTrue(st["complete"])
        self.assertEqual([r["total"] for r in st["results"]], [first["total"], second["total"]])
        self.assertEqual(st["results"][1]["natural"], second["kept"][0])
        self.assertEqual(len(st["results"][1]["both"]), 2)

    def test_a_roll_for_someone_not_asked_is_not_a_result(self):
        rid = self.request(characters=["Ajeje"])
        self.roll("Lyra", rid)
        st = self.client.get(f"/dice-request/{rid}").get_json()
        self.assertEqual((st["complete"], st["results"]), (False, []))

    def test_old_requests_are_forgotten(self):
        first = self.request(characters=["Ajeje"])
        for _ in range(self.mod._DICE_RESULTS_MAX):
            self.request(characters=["Ajeje"])
        self.assertNotIn(first, self.mod._dice_results)
        self.assertEqual(self.client.get(f"/dice-request/{first}").get_json().get("results"), None)


class FormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.send = _load("send_dice_results", DISPLAY / "send.py")

    def test_one_line_per_roll(self):
        f = self.send._format_roll_result
        r = {"character": "Ajeje", "spec": "1d20", "modifier": 3, "rolls": [14], "kept": [14], "both": None, "total": 17, "natural": 14}
        self.assertEqual(f(r, 15, "Furtività"), "Ajeje: 17 (1d20+3: [14] +3) — Furtività — DC 15: success")
        adv = dict(r, rolls=[20], kept=[20], both=[20, 4], advantage="advantage", total=23, natural=20)
        self.assertEqual(f(adv), "Ajeje: 23 (1d20+3: [20, 4] keep 20 (advantage) +3) — natural 20")
        dmg = {"character": "Lyra", "spec": "2d6", "modifier": 0, "rolls": [1, 5], "kept": [1, 5], "both": None, "total": 6, "natural": None}
        self.assertEqual(f(dmg, None, "Danno"), "Lyra: 6 (2d6: [1, 5]) — Danno")
        low = dict(r, rolls=[1], kept=[1], total=4, natural=1)
        self.assertEqual(f(low, 10), "Ajeje: 4 (1d20+3: [1] +3) — natural 1 — DC 10: failure")

    def test_the_results_go_to_stdout(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.send._print_roll_results({"dc": None, "label": "", "results": [
                {"character": "A", "spec": "1d4", "modifier": 0, "rolls": [2], "kept": [2], "total": 2}]})
            self.send._print_roll_results({})
        self.assertEqual(out.getvalue(), "A: 2 (1d4: [2])\n")


if __name__ == "__main__":
    unittest.main()
