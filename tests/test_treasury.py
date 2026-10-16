"""
test_treasury.py — the party's common fund.

treasury.py keeps the group's coins in <campaign>/treasury.json. A player puts
coins in or takes them out from their phone (POST /treasury/move: the coins
move between the sheet and the fund, both or neither), the shop can be paid
from it, every movement is written down and the DM is told.

Run from repo root:
    python -m pytest tests/test_treasury.py -q
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
sys.path.insert(0, str(SKILL / "scripts"))

import inventory  # noqa: E402
import treasury  # noqa: E402

SHEET = """# {name}
## Equipment & Inventory
**Weapons:**
- Stocco

**Adventuring Gear:**
- Zaino

**Currency:** {coins}

## Backstory & Notes
"""


class Sheets(unittest.TestCase):
    """Two characters on disk, found by inventory.py."""

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="dnd-test-treasury-"))
        (self.dir / "aldric.md").write_text(SHEET.format(name="Aldric", coins="15gp 0sp 6cp"), encoding="utf-8")
        (self.dir / "mira.md").write_text(SHEET.format(name="Mira", coins="0gp 4sp 0cp"), encoding="utf-8")
        old = (inventory.paths.characters_dir, inventory.active_campaign)
        inventory.paths.characters_dir = lambda: self.dir
        inventory.active_campaign = lambda: ""
        self.addCleanup(lambda: setattr(inventory.paths, "characters_dir", old[0]))
        self.addCleanup(lambda: setattr(inventory, "active_campaign", old[1]))
        self.fund_path = str(self.dir / "treasury.json")

    def sheet(self, name):
        return (self.dir / f"{name.lower()}.md").read_text(encoding="utf-8")


class FundTests(Sheets):
    def test_coins_in_and_out_with_change(self):
        fund = treasury.new_fund()
        treasury.put(fund, {"gp": 120, "pp": 1}, reason="Ricompensa del borgomastro")
        self.assertEqual(treasury.total_cp(fund), 13000)
        treasury.take(fund, {"gp": 2, "sp": 5}, reason="Due notti alla locanda")
        self.assertEqual(fund["coins"], {"pp": 1, "gp": 117, "ep": 0, "sp": 5, "cp": 0})
        with self.assertRaises(treasury.TreasuryError) as cm:
            treasury.take(fund, {"pp": 20})
        self.assertIn("the fund holds 1pp 117gp 5sp, not enough for 200gp", str(cm.exception))
        with self.assertRaises(treasury.TreasuryError):
            treasury.put(fund, {})
        self.assertEqual([e["kind"] for e in fund["history"]], ["add", "spend"])
        self.assertEqual(treasury.describe(fund["history"][1]), "−2gp 5sp (Due notti alla locanda)")

    def test_a_deposit_and_a_withdrawal_move_coins_between_sheet_and_fund(self):
        fund = treasury.new_fund()
        move = treasury.deposit(fund, "aldric", {"gp": 10})
        self.assertEqual((move["character"], inventory.format_coins(move["purse"])), ("Aldric", "5gp 6cp"))
        self.assertIn("**Currency:** 5gp 0sp 6cp", self.sheet("Aldric"))
        move = treasury.withdraw(fund, "Mira", {"gp": 3})
        self.assertIn("**Currency:** 3gp 4sp 0cp", self.sheet("Mira"))
        self.assertEqual(inventory.format_coins(fund["coins"]), "7gp")
        self.assertEqual(treasury.describe(move["entry"]), "Mira took 3gp out")
        self.assertEqual(treasury.format_note(move["entry"], "7gp"),
                         "[[TREASURY: Mira took 3gp out — the party fund now holds 7gp.]]")

    def test_nothing_moves_when_someone_cannot_pay(self):
        fund = treasury.new_fund()
        with self.assertRaises(treasury.TreasuryError):
            treasury.deposit(fund, "Mira", {"gp": 1})
        self.assertIn("**Currency:** 0gp 4sp 0cp", self.sheet("Mira"))
        with self.assertRaises(treasury.TreasuryError):
            treasury.withdraw(fund, "Mira", {"gp": 1})
        self.assertEqual((treasury.total_cp(fund), fund["history"]), (0, []))
        with self.assertRaises(inventory.InventoryError):
            treasury.withdraw(fund, "Fantasma", {"gp": 1})

    def test_the_fund_pays_at_the_shop(self):
        fund = treasury.new_fund()
        treasury.put(fund, {"gp": 60})
        entry = treasury.shop_buy(fund, "Mira", "Pozione di guarigione", 5000, 1, "gear", "Emporio di Brakka")
        self.assertEqual(inventory.format_coins(fund["coins"]), "10gp")
        self.assertIn("- Pozione di guarigione\n", self.sheet("Mira"))
        self.assertIn("**Currency:** 0gp 4sp 0cp", self.sheet("Mira"), "Mira's own purse is untouched")
        self.assertEqual(treasury.describe(entry), "Mira paid 50gp from the fund (Pozione di guarigione — Emporio di Brakka)")
        with self.assertRaises(treasury.TreasuryError):
            treasury.shop_buy(fund, "Mira", "Spada lunga", 1500)
        self.assertNotIn("Spada lunga", self.sheet("Mira"))

    def test_saved_and_loaded_with_a_bounded_history(self):
        self.assertIsNone(treasury.load(self.fund_path))
        fund = treasury.new_fund()
        for _ in range(treasury.HISTORY_MAX + 5):
            treasury.put(fund, {"cp": 1})
        treasury.save(self.fund_path, fund)
        back = treasury.load(self.fund_path)
        self.assertEqual((back["coins"]["cp"], len(back["history"])), (treasury.HISTORY_MAX + 5, treasury.HISTORY_MAX))
        view = treasury.public_view(back, last=3)
        self.assertEqual((view["text"], len(view["history"])), ("305cp", 3))

    def test_the_command_line(self):
        old = (treasury._campaign, treasury.data_path, treasury._active_campaign)
        treasury._campaign = lambda c="": "prova"
        treasury.data_path = lambda c="": self.fund_path
        treasury._active_campaign = lambda: "altra"           # no announcement to a display
        self.addCleanup(lambda: (setattr(treasury, "_campaign", old[0]), setattr(treasury, "data_path", old[1]),
                                 setattr(treasury, "_active_campaign", old[2])))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(treasury.main(["spend", "1gp"]), 1)
            self.assertEqual(treasury.main(["add", "120", "mo", "--reason", "Ricompensa"]), 0)
            self.assertEqual(treasury.main(["deposit", "Aldric", "5gp"]), 0)
            self.assertEqual(treasury.main(["withdraw", "Mira", "500gp"]), 1)
            self.assertEqual(treasury.main(["show"]), 0)
        self.assertIn("the party has no fund yet. Nothing was changed.", err.getvalue())
        self.assertIn("not enough for 500gp", err.getvalue())
        self.assertIn("Party fund: 125gp", out.getvalue())
        self.assertIn("Aldric put 5gp in", out.getvalue())


class ServerTests(Sheets):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dnd_display_app_treasury", str(DISPLAY / "dnd-display-app.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod._token_ok = lambda: True
        cls.mod._rate_ok = lambda ip: True
        cls.mod._active_campaign = lambda: ""
        cls.mod._persist_stats = lambda: None
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        super().setUp()
        m = self.mod
        saved = (m._broadcast, m._treasury_file, m._treasury.notes_add, m._shop)
        self.addCleanup(lambda: setattr(m, "_broadcast", saved[0]))
        self.addCleanup(lambda: setattr(m, "_treasury_file", saved[1]))
        self.addCleanup(lambda: setattr(m._treasury, "notes_add", saved[2]))
        self.addCleanup(lambda: setattr(m, "_shop", saved[3]))
        self.sent, self.notes = [], []
        m._broadcast = self.sent.append
        m._treasury_file = lambda: self.fund_path
        m._treasury.notes_add = self.notes.append
        m._current_stats = {"players": [{"name": "Aldric"}, {"name": "Mira"}]}

    def make_fund(self, coins):
        fund = treasury.new_fund()
        treasury.put(fund, coins, reason="bottino")
        treasury.save(self.fund_path, fund)

    def move(self, who, direction, coins):
        return self.client.post("/treasury/move", json={"character": who, "dir": direction, "coins": coins})

    def test_no_fund_no_panel(self):
        self.assertEqual(self.client.get("/treasury").get_json(), {"treasury": None, "purse": None})
        r = self.move("Aldric", "in", "1gp")
        self.assertEqual((r.status_code, r.get_json()["code"]), (409, "nofund"))

    def test_a_player_puts_coins_in_and_takes_them_out(self):
        self.make_fund({"gp": 20})
        r = self.move("aldric", "in", "10 mo")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        body = r.get_json()
        self.assertEqual((body["treasury"]["text"], body["purse"]["cp"]), ("30gp", 506))
        self.assertIn("**Currency:** 5gp 0sp 6cp", self.sheet("Aldric"))
        news = next(m for m in self.sent if "treasury" in m)
        self.assertEqual((news["treasury_news"], news["treasury_by"]), ("Aldric put 10gp in", "Aldric"))
        self.assertEqual(self.notes, ["[[TREASURY: Aldric put 10gp in — the party fund now holds 30gp.]]"])
        r = self.move("Mira", "out", "25gp")
        self.assertEqual(r.get_json()["treasury"]["text"], "5gp")
        self.assertIn("**Currency:** 25gp 4sp 0cp", self.sheet("Mira"))
        got = self.client.get("/treasury?character=Mira").get_json()
        self.assertEqual((got["purse"]["cp"], [e["kind"] for e in got["treasury"]["history"]]),
                         (2540, ["withdraw", "deposit", "add"]))

    def test_refusals_change_nothing(self):
        self.make_fund({"gp": 5})
        for who, d, coins, status, code in (("Mira", "in", "1gp", 409, "funds"), ("Mira", "out", "6gp", 409, "fund"),
                                            ("Intruso", "in", "1gp", 403, "character"), ("Mira", "su", "1gp", 400, "dir"),
                                            ("Mira", "in", "tanti", 400, "coins")):
            with self.subTest(who=who, d=d, coins=coins):
                r = self.move(who, d, coins)
                self.assertEqual((r.status_code, r.get_json()["code"]), (status, code))
        self.assertEqual(treasury.load(self.fund_path)["coins"]["gp"], 5)
        self.assertIn("**Currency:** 0gp 4sp 0cp", self.sheet("Mira"))
        self.assertEqual(self.notes, [])

    def test_a_logged_in_player_moves_only_their_own_coins(self):
        self.make_fund({"gp": 5})
        with self.mod.app.test_request_context("/treasury/move", method="POST",
                                               json={"character": "Aldric", "dir": "in", "coins": "1gp"}):
            self.mod.g.role, self.mod.g.character = "player", "mira"
            resp = self.mod.treasury_move()
        self.assertEqual(resp[1], 409, "Mira has 4sp; Aldric's purse is not hers")
        self.assertIn("**Currency:** 15gp 0sp 6cp", self.sheet("Aldric"))

    def test_the_shop_can_be_paid_from_the_fund(self):
        self.make_fund({"gp": 60})
        self.client.post("/shop", json={"name": "Emporio", "items": [{"name": "Pozione di guarigione", "price": "50gp"}]})
        self.assertEqual(self.client.get("/shop?character=Mira").get_json()["fund"]["cp"], 6000)
        self.sent.clear()
        r = self.client.post("/shop/buy", json={"character": "Mira", "item": "i1", "pay": "fund"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["fund"]["text"], "10gp")
        self.assertIn("- Pozione di guarigione\n", self.sheet("Mira"))
        self.assertIn("**Currency:** 0gp 4sp 0cp", self.sheet("Mira"))
        self.assertTrue(any(m.get("shop_sale", {}).get("fund") for m in self.sent))
        self.assertTrue(any("treasury" in m for m in self.sent))
        self.assertEqual(self.notes, [], "the DM hears of it once, as a sale")
        self.assertEqual(treasury.load(self.fund_path)["history"][-1]["kind"], "shop")
        r = self.client.post("/shop/buy", json={"character": "Mira", "item": "i1", "pay": "fund"})
        self.assertEqual((r.status_code, r.get_json()["code"]), (409, "fund"))
        lines = self.mod._shop_cli.drain_sales()
        self.assertEqual(len(lines), 1)
        self.assertIn("for 50gp at \"Emporio\" — paid from the party's common fund (10gp left in it) and added", lines[0])

    def test_wiring(self):
        self.assertIn("treasury_changed", self.mod._DM_ENDPOINTS)
        self.assertNotIn("treasury_move", self.mod._DM_ENDPOINTS)
        self.assertNotIn("treasury_get", self.mod._DM_ENDPOINTS)
        self.assertIn("treasury", (DISPLAY / "check_input.py").read_text(encoding="utf-8"))


_SLICE = re.compile(r"(function _coinsLocal\(text\).*?)\n// ── end treasury entry ──", re.S)


class PageTests(unittest.TestCase):
    def test_history_lines(self):
        if NODE is None:
            raise unittest.SkipTest("node not available")
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertTrue(m, "the treasury block is no longer where this test slices it")
        it = json.loads((DISPLAY / "i18n" / "it.json").read_text(encoding="utf-8"))
        tr = ("const L = %s;\nfunction _t(k, v, f) { let s = L[k]; if (s === undefined) return f;"
              " for (const n in (v || {})) s = s.split('{' + n + '}').join(v[n]); return s; }\n") % json.dumps(it)
        entries = [{"kind": "deposit", "who": "Lyra", "coins": "10gp 5sp"},
                   {"kind": "shop", "who": "Lio", "coins": "50gp", "reason": "Pozione"},
                   {"kind": "upkeep", "coins": "3gp"}, {"kind": "strano", "coins": "1cp"}]
        script = tr + m.group(1) + "\nconsole.log(JSON.stringify(%s.map(_treasuryEntry)));\n" % json.dumps(entries)
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            rows = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        self.assertEqual(rows, [{"text": "Lyra ha versato 10 mo 5 ma", "sign": "plus"},
                                {"text": "Lio ha pagato 50 mo alla bottega — Pozione", "sign": "minus"},
                                {"text": "3 mo di mantenimento", "sign": "minus"},
                                {"text": "1 mr", "sign": "plus"}])

    def test_the_page_has_the_panel_and_every_way_in(self):
        src = HTML.read_text(encoding="utf-8")
        for needle in ('id="tre-modal"', 'id="ln-treasury"', 'data-ln-proxy="ln-treasury"', 'id="ph-treasury-opt"',
                       "window._onTreasury(payload)", 'id="ln-i-chest"', 'id="shop-pay"', "pay: payFund ? 'fund' : 'me'"):
            self.assertIn(needle, src)

    def test_strings_exist_in_every_language(self):
        src = HTML.read_text(encoding="utf-8")
        keys = sorted(set(re.findall(r"_t\('(tre\.[\w.]*\w)'", src)) | {"tre.h.deposit", "tre.h.withdraw", "tre.h.shop",
                      "tre.h.add", "tre.h.income", "tre.h.spend", "tre.h.upkeep", "shop.pay.me", "shop.pay.fund", "tre.title"})
        self.assertGreater(len(keys), 15)
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
