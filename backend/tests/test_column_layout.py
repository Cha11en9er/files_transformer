from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.bilingual import already_bilingual, bilingual, bilingual_title, label
from app.services.column_layout import apply_sheet_layout, attach_column, site_columns
from app.services.export_beijing import invoice_headers
from app.services.export_tsd import dt_headers, dt_table, invoice_headers as tsd_invoice_headers


class BilingualTest(unittest.TestCase):
    def test_skips_existing_slash(self):
        self.assertTrue(already_bilingual("Brand / Торговая марка"))
        self.assertEqual(bilingual_title("Brand / Торговая марка"), "Brand / Торговая марка")
        self.assertEqual(bilingual("Color", "Цвет"), "Color / Цвет")
        self.assertEqual(bilingual_title("DESIGN"), "Design / Дизайн")
        self.assertEqual(bilingual_title("Buyer:"), "Buyer / Покупатель:")

    def test_beijing_invoice_headers_have_russian(self):
        headers = invoice_headers("USD")
        blob = " ".join(headers)
        self.assertIn("№", blob)
        self.assertIn("Артикул", blob)
        self.assertIn("Количество", blob)
        self.assertIn("Цена", blob)

    def test_letterhead_and_site_columns_are_bilingual(self):
        self.assertIn("/", label("buyer"))
        self.assertIn("Покупатель", label("buyer"))
        self.assertTrue(already_bilingual(label("invoice_no", colon=True)))
        blob = " ".join(dt_headers())
        self.assertIn("Код ТН ВЭД", blob)
        self.assertIn("цена со скидкой", blob)
        self.assertNotIn("HS Code", blob)
        self.assertTrue(all(title.strip() for title in dt_headers()))
        self.assertIn("/", tsd_invoice_headers("USD")[0])
        site = site_columns("18233", True)
        self.assertIn("/", site["invoice"][0]["title"])
        self.assertIn("Дизайн", site["invoice"][0]["title"])
        beijing = site_columns("BEIJING", False)
        self.assertIn("Артикул", beijing["invoice"][0]["title"])
        tsd = site_columns("18233", False, tsd=True)
        self.assertIn("Модель", tsd["invoice"][0]["title"])
        self.assertTrue(any(col["key"] == "country" for col in tsd["invoice"]))
        self.assertTrue(any("шт, уп, компл" in col["title"] for col in tsd["invoice"]))
        self.assertTrue(any("Таможенный" in col["title"] for col in site_columns("18233", False)["invoice"]))

    def test_prune_drops_vacant_fabric_columns(self):
        from app.services.column_layout import prune_empty_columns

        headers = ["No", "Art", "Width", "Meters", "Amount"]
        keys = ["no", "article", "width", "qty", "amount"]
        rows = [[1, "A", None, None, 10], ["TOTAL", None, None, None, 10]]
        next_h, next_k, next_r = prune_empty_columns(headers, keys, rows)
        self.assertEqual(next_k, ["no", "article", "amount"])
        self.assertEqual(next_r[0], [1, "A", 10])

    def test_dt_table_keeps_discount_and_drops_empty(self):
        items = [
            {
                "article": "AE-1",
                "commercial_data": {"qty": 10, "unit": "ШТ", "price": 1.2, "amount": 12},
                "packing_data": {"boxes": 2, "net_weight": 5, "gross_weight": 6},
                "customs_data": {"hs_code": "8539520009", "country": "CN", "description": "LED"},
            },
            {
                "article": "AE-2",
                "commercial_data": {"qty": 5, "unit": "ШТ", "price": 2, "amount": 10},
                "packing_data": {"boxes": 1, "net_weight": 2, "gross_weight": 3},
                "customs_data": {"hs_code": "8539520009", "country": "CN", "description": "LED"},
            },
        ]
        headers, rows = dt_table(items, {"invoice_no": "OI-451", "currency": "USD"})
        self.assertIn("Код ТН ВЭД", headers)
        self.assertIn("цена со скидкой", headers)
        self.assertNotIn("", headers)
        # пустые ИТС / декларация не должны остаться
        self.assertNotIn("ИТС по запросу", headers)
        self.assertNotIn("Декларация соответствия", headers)
        disc_idx = headers.index("цена со скидкой")
        self.assertTrue(all(row[disc_idx] in (None, "") for row in rows[:-1]))


class AttachColumnTest(unittest.TestCase):
    def test_splits_one_source_row_onto_family_lots(self):
        items = [
            {"id": "a1", "article": "A-1", "commercial_data": {"qty": 40}, "packing_data": {}, "customs_data": {}},
            {"id": "a2", "article": "A-1", "commercial_data": {"qty": 60}, "packing_data": {}, "customs_data": {}},
            {"id": "b1", "article": "B-2", "commercial_data": {"qty": 10}, "packing_data": {}, "customs_data": {}},
        ]
        source = [
            {"article": "A-1", "qty": 100, "raw": {"Finish": "black"}},
            {"article": "B-2", "qty": 10, "raw": {"Finish": "red"}},
        ]
        out = attach_column(items, source, "Finish")
        self.assertEqual(out["matched"], 3)
        self.assertEqual(out["values"]["a1"], "black")
        self.assertEqual(out["values"]["a2"], "black")
        self.assertEqual(out["values"]["b1"], "red")
        self.assertEqual(out["key"], "article-sum")

    def test_keeps_same_article_apart_by_qty(self):
        items = [
            {"id": "1", "article": "X", "commercial_data": {"qty": 5}, "packing_data": {}, "customs_data": {}},
            {"id": "2", "article": "X", "commercial_data": {"qty": 7}, "packing_data": {}, "customs_data": {}},
        ]
        source = [
            {"article": "X", "qty": 5, "raw": {"Batch": "one"}},
            {"article": "X", "qty": 7, "raw": {"Batch": "two"}},
        ]
        out = attach_column(items, source, "Batch")
        self.assertEqual(out["values"]["1"], "one")
        self.assertEqual(out["values"]["2"], "two")

    def test_hides_and_appends(self):
        items = [
            {
                "id": "1",
                "article": "A",
                "commercial_data": {},
                "packing_data": {},
                "customs_data": {},
                "extra_columns": {"invoice": {"c1": "yes"}},
            }
        ]
        next_h, _keys, next_r = apply_sheet_layout(
            ["No", "Art", "Color"],
            ["no", "article", "color"],
            [[1, "A", "red"], [None, "TOTAL:", None]],
            items,
            {"invoice": {"hidden": ["color"], "extra": [{"id": "c1", "title": "Finish / Отделка"}]}},
            "invoice",
        )
        self.assertNotIn("Color", next_h)
        self.assertEqual(next_h[-1], "Finish / Отделка")
        self.assertEqual(next_r[0][-1], "yes")
        self.assertIsNone(next_r[1][-1])


if __name__ == "__main__":
    unittest.main()
