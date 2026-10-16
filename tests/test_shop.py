"""
test_shop.py — players buy from a shop on their phone; the sheet pays.

The DM opens a shop (display/shop.py → POST /shop). A purchase (POST
/shop/buy) is applied to the buyer's character sheet with scripts/inventory.py
before the request returns — coins off, item on, stock down — or not at all.
The DM is told what was sold with the next check_input.py.

Run from repo root:
    python -m pytest tests/test_shop.py -q
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
DISPLAY = SKILL / "display"
sys.path.insert(0, str(DISPLAY))

SHEET = """# {name}
## Equipment & Inventory
**Weapons:**
- Stocco

**Armour:**
- Armatura di cuoio

**Adventuring Gear:**
- Zaino

**Currency:** {coins}

## Backstory & Notes
"""

LIST = [
    {"name": "Corda di canapa (15 m)", "price": "1gp"},
    {"name": "Pozione di guarigione", "price": "50 mo", "stock": 1, "note": "sigillo rosso"},
    {"name": "Spada corta", "price": "10gp", "section": "weapons"},
    {"name": "Razioni (1 giorno)", "price": "5sp"},
]


def _import_app():
    spec = importlib.util.spec_from_file_location(
        "dnd_display_app_shop", str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ShopTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.mod = _import_app()
        cls.mod._token_ok = lambda: True
        cls.mod._rate_ok = lambda ip: True
        cls.mod._active_campaign = lambda: ""
        cls.mod._persist_stats = lambda: None
        cls.client = cls.mod.app.test_client()
        cls.inv = cls.mod._inventory
        cls.sales = pathlib.Path(cls.mod._shop_cli.SALES_FILE)
        cls._orig = (cls.inv.paths.characters_dir, cls.inv.active_campaign, cls.mod._broadcast)

    @classmethod
    def tearDownClass(cls):
        cls.inv.paths.characters_dir, cls.inv.active_campaign, cls.mod._broadcast = cls._orig

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="dnd-test-shop-"))
        (self.dir / "aldric.md").write_text(SHEET.format(name="Aldric", coins="15gp 0sp 6cp"), encoding="utf-8")
        (self.dir / "mira.md").write_text(SHEET.format(name="Mira", coins="0gp 4sp 0cp"), encoding="utf-8")
        self.inv.paths.characters_dir = lambda: self.dir
        self.inv.active_campaign = lambda: ""
        self.mod._current_stats = {"players": [{"name": "Aldric"}, {"name": "Mira"}]}
        self.mod._shop = None
        self.sales.unlink(missing_ok=True)
        self.sent = []
        self.mod._broadcast = self.sent.append

    def tearDown(self):
        self.mod._shop = None
        self.sales.unlink(missing_ok=True)
        pathlib.Path(self.mod.SHOP_FILE).unlink(missing_ok=True)

    def _open(self, items=None, **extra):
        body = {"name": "Emporio di Brakka", "keeper": "Brakka", "items": items if items is not None else LIST}
        body.update(extra)
        return self.client.post("/shop", json=body)

    def _buy(self, who, item, qty=1):
        return self.client.post("/shop/buy", json={"character": who, "item": item, "qty": qty})

    def _sheet(self, name):
        return (self.dir / f"{name.lower()}.md").read_text(encoding="utf-8")

    # ── opening ──────────────────────────────────────────────────────────────

    def test_opening_prices_every_item_in_copper_and_tells_every_screen(self):
        r = self._open()
        self.assertEqual(r.status_code, 200)
        shop = r.get_json()["shop"]
        self.assertEqual([(i["id"], i["price_cp"], i["price"], i["stock"], i["section"]) for i in shop["items"]], [
            ("i1", 100, "1gp", None, "gear"), ("i2", 5000, "50gp", 1, "gear"),
            ("i3", 1000, "10gp", None, "weapons"), ("i4", 50, "5sp", None, "gear")])
        self.assertEqual(self.sent, [{"shop": shop, "shop_opened": True}])
        self.assertEqual(self.client.get("/shop").get_json(), {"shop": shop, "purse": None, "fund": None})

    def test_a_bad_price_or_an_empty_list_opens_nothing(self):
        for items in ([], [{"name": "Corda", "price": "poco"}], [{"name": "", "price": "1gp"}],
                      [{"name": "Corda", "price": "1gp", "stock": -1}],
                      [{"name": "Corda", "price": "1gp", "section": "cappelli"}]):
            with self.subTest(items=items):
                self.assertEqual(self._open(items).status_code, 400)
        self.assertEqual(self.sent, [])
        self.assertIsNone(self.client.get("/shop").get_json()["shop"])

    def test_the_buyer_sees_the_purse_on_their_sheet(self):
        self._open()
        purse = self.client.get("/shop?character=Aldric").get_json()["purse"]
        self.assertEqual((purse["cp"], purse["coins"]["gp"], purse["coins"]["cp"]), (1506, 15, 6))
        self.assertIsNone(self.client.get("/shop?character=Fantasma").get_json()["purse"])

    def test_closing_takes_the_shop_off_every_screen(self):
        self._open()
        self.sent.clear()
        self.assertEqual(self.client.post("/shop", json={"close": True}).status_code, 200)
        self.assertEqual(self.sent, [{"shop": None}])
        self.assertEqual(self._buy("Aldric", "i1").get_json()["code"], "closed")

    # ── buying ───────────────────────────────────────────────────────────────

    def test_a_purchase_pays_adds_the_item_and_is_reported_to_the_dm(self):
        self._open()
        self.sent.clear()
        r = self._buy("aldric", "i4")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual((body["bought"], body["total"], body["purse"]["cp"]), ("Razioni (1 giorno)", "5sp", 1456))
        sheet = self._sheet("Aldric")
        self.assertIn("**Currency:** 14gp 5sp 6cp", sheet)
        self.assertIn("- Zaino\n- Razioni (1 giorno)\n", sheet)
        # every screen: the bag on the display's copy of the sheet, and who bought what
        self.assertEqual(self.sent[0]["stats"]["players"][0]["sheet"]["inventory"], ["Razioni (1 giorno)"])
        self.assertEqual(self.sent[1]["shop_sale"], {"character": "Aldric", "item": "Razioni (1 giorno)", "total": "5sp", "fund": False})
        lines = self.mod._shop_cli.drain_sales()
        self.assertEqual(len(lines), 1)
        self.assertIn('[[SHOP: Aldric bought Razioni (1 giorno) for 5sp at "Emporio di Brakka"', lines[0])
        self.assertIn("14gp 5sp 6cp left", lines[0])
        self.assertEqual(self.mod._shop_cli.drain_sales(), [])

    def test_a_weapon_lands_in_the_weapons_list(self):
        self._open()
        self._buy("Aldric", "i3")
        self.assertIn("- Stocco\n- Spada corta\n", self._sheet("Aldric"))

    def test_buying_several_charges_for_each_and_counts_them(self):
        self._open()
        r = self._buy("Aldric", "i1", qty=3)
        self.assertEqual((r.get_json()["bought"], r.get_json()["total"]), ("Corda di canapa (15 m) ×3", "3gp"))
        self.assertIn("- Corda di canapa (15 m) ×3\n", self._sheet("Aldric"))

    def test_too_poor_changes_nothing(self):
        self._open()
        before = self._sheet("Mira")
        self.sent.clear()
        r = self._buy("Mira", "i1")
        self.assertEqual((r.status_code, r.get_json()["code"]), (409, "funds"))
        self.assertEqual(self._sheet("Mira"), before)
        self.assertEqual(self.sent, [])
        self.assertFalse(self.sales.exists())

    def test_stock_runs_out(self):
        self._open()
        (self.dir / "aldric.md").write_text(SHEET.format(name="Aldric", coins="200gp 0sp 0cp"), encoding="utf-8")
        self.assertEqual(self._buy("Aldric", "i2").get_json()["shop"]["items"][1]["stock"], 0)
        r = self._buy("Aldric", "i2")
        self.assertEqual((r.status_code, r.get_json()["code"]), (409, "stock"))
        self.assertIn("**Currency:** 150gp 0sp 0cp", self._sheet("Aldric"), "the second potion was not charged")

    def test_unknown_item_character_or_quantity_is_refused(self):
        self._open()
        self.assertEqual(self._buy("Aldric", "i99").status_code, 404)
        self.assertEqual(self._buy("Intruso", "i1").status_code, 403)
        self.assertEqual(self._buy("Everybody", "i1").status_code, 403)
        self.assertEqual(self._buy("Aldric", "i1", qty=0).status_code, 400)
        self.assertEqual(self._buy("Aldric", "i1", qty="molti").status_code, 400)

    def test_a_logged_in_player_buys_only_for_themselves(self):
        self._open()
        with self.mod.app.test_request_context("/shop/buy", method="POST",
                                               json={"character": "Aldric", "item": "i4"}):
            self.mod.g.role, self.mod.g.character = "player", "mira"
            resp = self.mod.shop_buy()
        self.assertEqual(resp[1], 409, "Mira has 4sp: she cannot pay 5sp, and Aldric's purse is not hers")
        self.assertIn("**Currency:** 15gp 0sp 6cp", self._sheet("Aldric"))

    def test_only_the_dm_opens_and_closes(self):
        self.assertIn("shop_set", self.mod._DM_ENDPOINTS)
        self.assertNotIn("shop_buy", self.mod._DM_ENDPOINTS)
        self.assertNotIn("shop_get", self.mod._DM_ENDPOINTS)

    def test_check_input_tells_the_dm_what_was_sold(self):
        self._open()
        self._buy("Aldric", "i1")
        out = subprocess.run([sys.executable, str(DISPLAY / "check_input.py")], capture_output=True, text=True,
                             encoding="utf-8", env=dict(os.environ, PYTHONUTF8="1"), timeout=30).stdout
        self.assertIn("[[SHOP: Aldric bought Corda di canapa (15 m) for 1gp", out)
        self.assertFalse(self.sales.exists())


class PriceListTests(unittest.TestCase):

    def setUp(self):
        import shop
        self.shop = shop

    def test_lines_become_items(self):
        items = self.shop.parse_items(
            "# commento\n"
            "Corda di canapa (15 m) | 1gp\n"
            "\n"
            "Pozione di guarigione | 50gp | 3 | sigillo di cera rossa\n"
            "[weapons] Spada corta | 10gp | 1\n"
            "[armor] Scudo | 10gp || di quercia | con lo stemma\n")
        self.assertEqual(items, [
            {"name": "Corda di canapa (15 m)", "price": "1gp", "section": "gear"},
            {"name": "Pozione di guarigione", "price": "50gp", "section": "gear", "stock": 3,
             "note": "sigillo di cera rossa"},
            {"name": "Spada corta", "price": "10gp", "section": "weapons", "stock": 1},
            {"name": "Scudo", "price": "10gp", "section": "armour", "note": "di quercia | con lo stemma"},
        ])

    def test_a_malformed_line_names_itself(self):
        for text, part in (("Corda", "line 1"), ("Corda | 1gp\nPozione | 50gp | tre", "line 2"), ("", "nothing to sell")):
            with self.subTest(text=text), self.assertRaises(ValueError) as cm:
                self.shop.parse_items(text)
            self.assertIn(part, str(cm.exception))


class ShopWiringTests(unittest.TestCase):

    def test_the_page_listens_for_the_shop(self):
        src = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("window._onShop(payload)", src)
        self.assertIn('id="shop-modal"', src)

    def test_strings_exist_in_every_language(self):
        keys = ["shop.title", "shop.open", "shop.purse", "shop.buy", "shop.confirm", "shop.left", "shop.soldout",
                "shop.opened", "shop.bought", "shop.sale", "shop.hint.phone", "shop.nosheet", "shop.err.funds",
                "shop.err.stock", "shop.err.closed", "shop.err.generic",
                "coin.pp", "coin.gp", "coin.ep", "coin.sp", "coin.cp"]
        for path in sorted((DISPLAY / "i18n").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                with self.subTest(lang=path.stem, key=key):
                    self.assertTrue(data.get(key))


if __name__ == "__main__":
    unittest.main()
