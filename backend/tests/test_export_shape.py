# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.export import build_export_preview, export_18233
from app.services.export_18233_templates import is_fabric_layout
from app.services.export_tsd import is_tsd_layout


def _nut(i: int) -> dict:
    return {
        "article": f"M{i}",
        "commercial_data": {"qty": 10 + i, "unit": "pcs", "price": 0.5, "amount": 5 + i},
        "packing_data": {"rolls": 1, "net_weight": 1.2, "gross_weight": 1.5},
        "customs_data": {
            "hs_code": f"731816910{i}",
            "country": "CN",
            "description_en": f"nut {i}",
        },
        "source_traces": {"invoice": {"no": i}},
    }


def _fabric(i: int) -> dict:
    return {
        "article": f"ENZO {i}",
        "commercial_data": {"qty": 100, "unit": "meters", "price": 2, "amount": 200},
        "packing_data": {"rolls": 2, "meters": 100, "width": 1.4, "area": 140, "net_weight": 50, "gross_weight": 55},
        "customs_data": {"hs_code": "5407520000"},
        "source_traces": {"invoice": {"no": i}},
    }


class ExportShapeTest(unittest.TestCase):
    def test_nuts_are_tsd_not_fabric(self):
        items = [_nut(1), _nut(2), _nut(3)]
        self.assertTrue(is_tsd_layout(items))
        self.assertFalse(is_fabric_layout("626-1", items))

    def test_fabric_stays_fabric(self):
        items = [_fabric(1), _fabric(2), _fabric(3)]
        self.assertFalse(is_tsd_layout(items))
        self.assertTrue(is_fabric_layout("626-1", items))

    def test_18233_export_always_three_books(self, tmp_path=None):
        from tempfile import TemporaryDirectory

        items = [_nut(1), _nut(2), _nut(3)]
        preview = build_export_preview(items, profile_type="18233", header={"invoice_no": "THN-1"})
        self.assertEqual(len(preview["files"]), 3)
        titles = [sheet["title"] for file in preview["files"] for sheet in file["sheets"]]
        self.assertEqual(
            titles,
            ["Invoice Инвойс", "Packing Пакинг", "Specification Спецификация"],
        )
        with TemporaryDirectory() as tmp:
            paths = export_18233(items, Path(tmp), header={"invoice_no": "THN-1"}, shipment_title="nuts")
            self.assertEqual(len(paths), 3)
            joined = " ".join(path.name.upper() for path in paths)
            self.assertIn("ИНВОЙС", joined)
            self.assertIn("ПАКИНГ", joined)
            self.assertIn("СПЕЦИФИКАЦИЯ", joined)


if __name__ == "__main__":
    unittest.main()
