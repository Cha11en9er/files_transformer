from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.bilingual import already_bilingual, bilingual, bilingual_title, label
from app.services.column_layout import apply_sheet_layout, attach_column, site_columns
from app.services.export_beijing import invoice_headers
from app.services.export_tsd import _DT_UNNAMED, dt_headers, invoice_headers as tsd_invoice_headers


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
        self.assertIn("HS Code", blob)
        self.assertEqual(dt_headers().count(_DT_UNNAMED), 3)
        self.assertIn("/", tsd_invoice_headers("USD")[0])
        site = site_columns("18233", True)
        self.assertIn("/", site["invoice"][0]["title"])
        self.assertIn("Дизайн", site["invoice"][0]["title"])
        beijing = site_columns("BEIJING", False)
        self.assertIn("Артикул", beijing["invoice"][0]["title"])


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
