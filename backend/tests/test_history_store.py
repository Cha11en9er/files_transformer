# -*- coding: utf-8 -*-
"""History store: draft, rename, save, zip, delete file/shipment."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services import history_store


class HistoryStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="hist_test_", ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name) / "history"
        self._old = os.environ.get("HISTORY_ROOT")
        os.environ["HISTORY_ROOT"] = str(self.root)
        history_store.init_history_db()

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("HISTORY_ROOT", None)
        else:
            os.environ["HISTORY_ROOT"] = self._old
        self._tmp.cleanup()

    def _source(self, name: str, body: bytes = b"abc") -> Path:
        folder = Path(self._tmp.name) / "uploads"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_bytes(body)
        return path

    def test_create_list_rename_save_export_delete(self) -> None:
        invoice = self._source("invoice.xlsx", b"invoice-bytes")
        packing = self._source("packing.pdf", b"packing-bytes")
        hid = history_store.create_draft(
            title="INV-1",
            profile_type="18233",
            workspace={"title": "INV-1", "items": [{"id": "1"}], "profile_type": "18233"},
            sources=[(invoice, "invoice.xlsx"), (packing, "packing.pdf")],
            catalog_names=set(),
        )
        self.assertTrue(hid)
        listed = history_store.list_shipments()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["title"], "INV-1")
        self.assertEqual(listed[0]["source_count"], 2)
        self.assertTrue(listed[0]["updated_at_moscow"])

        renamed = history_store.rename_shipment(hid, "Контракт HUAH / INV-1")
        assert renamed is not None
        self.assertEqual(renamed["title"], "Контракт HUAH / INV-1")

        saved = history_store.save_workspace(
            hid,
            {
                "title": "Контракт HUAH / INV-1",
                "items": [{"id": "1"}, {"id": "2"}],
                "column_layout": {},
            },
            title="Контракт HUAH / INV-1",
        )
        assert saved is not None
        ws = json.loads((self.root / hid / "workspace.json").read_text(encoding="utf-8"))
        self.assertEqual(len(ws["items"]), 2)

        stored = history_store.store_export_zip(hid, b"PK\x03\x04zip", filename="Контракт_Excel.zip")
        assert stored is not None
        detail = history_store.get_shipment(hid)
        assert detail is not None
        self.assertEqual(detail["export_count"], 1)
        self.assertTrue(detail["sources"])
        self.assertTrue(detail["exports"])

        export_name = detail["exports"][0]["name"]
        self.assertTrue(history_store.delete_file(hid, "exports", export_name))
        detail2 = history_store.get_shipment(hid)
        assert detail2 is not None
        self.assertEqual(detail2["export_count"], 0)

        self.assertTrue(history_store.delete_file(hid, "sources", "invoice.xlsx"))
        detail3 = history_store.get_shipment(hid)
        assert detail3 is not None
        self.assertEqual(detail3["source_count"], 1)

        self.assertTrue(history_store.delete_shipment(hid))
        self.assertIsNone(history_store.get_shipment(hid))
        self.assertEqual(history_store.list_shipments(), [])

    def test_moscow_label_formats(self) -> None:
        label = history_store.moscow_label("2026-10-28T07:26:55Z")
        self.assertTrue(label.startswith("28.10.2026"))
        self.assertIn(":", label)


class HistoryApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="hist_api_", ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name) / "history"
        self._old = os.environ.get("HISTORY_ROOT")
        os.environ["HISTORY_ROOT"] = str(self.root)
        history_store.init_history_db()
        src_dir = Path(self._tmp.name) / "up"
        src_dir.mkdir(parents=True, exist_ok=True)
        src = src_dir / "a.xlsx"
        src.write_bytes(b"aa")
        self.hid = history_store.create_draft(
            title="API-1",
            profile_type="BEIJING",
            workspace={"title": "API-1", "items": [], "profile_type": "BEIJING"},
            sources=[(src, "a.xlsx")],
        )
        from fastapi.testclient import TestClient
        from app.main import create_app

        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self.client = None
        if self._old is None:
            os.environ.pop("HISTORY_ROOT", None)
        else:
            os.environ["HISTORY_ROOT"] = self._old
        self._tmp.cleanup()

    def test_list_get_patch_delete(self) -> None:
        listed = self.client.get("/api/v1/history/")
        self.assertEqual(listed.status_code, 200)
        body = listed.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["items"][0]["id"], self.hid)

        got = self.client.get(f"/api/v1/history/{self.hid}")
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.json()["title"], "API-1")
        self.assertEqual(got.json()["workspace"]["title"], "API-1")

        patched = self.client.patch(f"/api/v1/history/{self.hid}", json={"title": "Новое имя"})
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.json()["title"], "Новое имя")

        saved = self.client.post(
            "/api/v1/history/",
            json={
                "history_id": self.hid,
                "title": "Новое имя",
                "profile_type": "BEIJING",
                "items": [],
                "header_fields": {"invoice_no": "X"},
            },
        )
        self.assertEqual(saved.status_code, 200)

        deleted_file = self.client.request(
            "DELETE",
            f"/api/v1/history/{self.hid}/files",
            json={"kind": "sources", "name": "a.xlsx"},
        )
        self.assertEqual(deleted_file.status_code, 200)

        deleted = self.client.delete(f"/api/v1/history/{self.hid}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/history/{self.hid}").status_code, 404)


if __name__ == "__main__":
    unittest.main()
