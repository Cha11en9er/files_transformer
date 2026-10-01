import unittest
from pathlib import Path

from prepare_transform_code.numbers import parse_number
from prepare_transform_code.shipment import analyze

ROOT = Path(__file__).resolve().parents[2] / "documents_final"


def load(name):
    return analyze(ROOT / name)


class NumbersTest(unittest.TestCase):
    def test_money_formats(self):
        self.assertEqual(parse_number("US$55,000.00"), 55000)
        self.assertEqual(parse_number("¥1 267,00"), 1267)
        self.assertEqual(parse_number("¥291 557,00"), 291557)
        self.assertAlmostEqual(parse_number("6809.6"), 6809.6)
        self.assertAlmostEqual(parse_number("21.03 Cbm"), 21.03)


class NoTemplateBranchTest(unittest.TestCase):
    def test_reader_does_not_name_shipments(self):
        package = Path(__file__).resolve().parents[1]
        for path in package.rglob("*.py"):
            if path.name.startswith("test_"):
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("Шаблон", text, path.name)
            self.assertNotIn("TIG200", text, path.name)
            self.assertNotIn("JH21024", text, path.name)


class Shipment1Test(unittest.TestCase):
    def test_one_machine(self):
        result = load("Шаблон 1")
        roles = {doc["role"] for doc in result["documents"]}
        self.assertIn("invoice", roles)
        self.assertIn("packing", roles)
        self.assertNotIn("duplicate", roles)
        self.assertEqual(len(result["lots"]), 1)
        lot = result["lots"][0]
        self.assertEqual(lot["pieces"], 1)
        self.assertEqual(lot["packages"], 5)
        self.assertEqual(lot["net"], 2980)
        self.assertEqual(lot["gross"], 3270)
        self.assertEqual(lot["volume"], 21.03)
        self.assertEqual(lot["amount"], 55000)
        self.assertEqual(result["currency"], "USD")
        self.assertEqual(result["invoice_no"], "JH21024")


class Shipment2Test(unittest.TestCase):
    def test_seven_lots_and_weight_conflict(self):
        result = load("Шаблон 2")
        goods = [lot for lot in result["lots"] if not lot["freight"]]
        self.assertEqual(len(goods), 7)
        pieces = sorted(lot["pieces"] for lot in goods)
        self.assertEqual(pieces, [60, 60, 200, 240, 650, 1000, 2840])
        self.assertIn("weight_conflict", result["flags"])
        self.assertTrue(all(lot["gross"] is None for lot in goods))


class Shipment3Test(unittest.TestCase):
    def test_fabric(self):
        result = load("Шаблон 3")
        self.assertEqual(len(result["lots"]), 1)
        lot = result["lots"][0]
        self.assertAlmostEqual(lot["pieces"], 6809.6)
        self.assertEqual(lot["packages"], 178)
        self.assertAlmostEqual(lot["net"], 5154.9)
        self.assertAlmostEqual(lot["gross"], 5332.9)
        self.assertAlmostEqual(lot["volume"], 12.5)
        self.assertAlmostEqual(lot["price"], 6.25)
        self.assertEqual(lot["amount"], 42560)
        self.assertEqual(result["invoice_no"], "HF20230106")
        self.assertIn("POLYESTER", lot["description"].upper())


class Shipment4Test(unittest.TestCase):
    def test_duplicate_scan_and_spec_lots(self):
        result = load("Шаблон 4")
        duplicates = [doc for doc in result["documents"] if doc["role"] == "duplicate"]
        scans = [doc for doc in result["documents"] if doc["role"] == "scan"]
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(len(scans), 1)
        goods = [lot for lot in result["lots"] if not lot["freight"]]
        articles = [lot["vendor"] for lot in goods]
        self.assertEqual(sum(1 for article in articles if str(article).startswith("1971")), 2)
        self.assertEqual(sum(lot["pieces"] or 0 for lot in goods), 2261)
        self.assertEqual(result["currency"], "EUR")
        self.assertTrue(result["freights"])


class Shipment5Test(unittest.TestCase):
    def test_join_by_model_not_row_order(self):
        result = load("Шаблон 5")
        self.assertEqual(result["currency"], "CNY")
        self.assertEqual(result["invoice_no"], "CI240301RU")
        by_model = {lot["model"]: lot for lot in result["lots"]}
        self.assertEqual(set(by_model), {"TIG200Pro", "TIG1000", "TIG630", "ASAW1600II", "Axis"})
        tig1000 = by_model["TIG1000"]
        self.assertEqual(tig1000["pieces"], 2)
        self.assertEqual(tig1000["packages"], 2)
        self.assertEqual(tig1000["gross"], 234)
        self.assertEqual(tig1000["net"], 182)
        self.assertEqual(tig1000["price"], 11200)
        axis = by_model["Axis"]
        self.assertEqual(axis["pieces"], 2)
        self.assertEqual(axis["price"], 540)
        shared = by_model["TIG200Pro"]["measure_group"]
        self.assertIsNotNone(shared)
        self.assertEqual(shared["gross"], 404)
        self.assertEqual(shared["net"], 357)
        self.assertIs(by_model["TIG200Pro"]["measure_group"], by_model["Axis"]["measure_group"])
        self.assertEqual(by_model["TIG200Pro"]["pieces"], 35)
        self.assertIsNone(by_model["TIG200Pro"]["gross"])
        groups = {}
        own = 0
        for lot in result["lots"]:
            if lot["measure_group"]:
                groups[id(lot["measure_group"])] = lot["measure_group"]["gross"]
            else:
                own += lot["gross"] or 0
        self.assertEqual(own + sum(groups.values()), 3079)


if __name__ == "__main__":
    unittest.main()
