# -*- coding: utf-8 -*-
"""Accounts, sessions, ownership and the action journal."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.main import create_app
from app.services import auth_store, history_store
from app.services.auth_store import AuthError


class AuthRulesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="auth_test_", ignore_cleanup_errors=True)
        self.auth = Path(self._tmp.name) / "auth"
        self.history = Path(self._tmp.name) / "history"
        self._old_auth = os.environ.get("AUTH_ROOT")
        self._old_hist = os.environ.get("HISTORY_ROOT")
        self._old_secret = os.environ.get("AUTH_SECRET")
        os.environ["AUTH_ROOT"] = str(self.auth)
        os.environ["HISTORY_ROOT"] = str(self.history)
        os.environ["AUTH_SECRET"] = "test-secret-value"
        auth_store.init_db()
        history_store.init_history_db()
        self.client = TestClient(create_app(), follow_redirects=False)

    def tearDown(self) -> None:
        if self._old_auth is None:
            os.environ.pop("AUTH_ROOT", None)
        else:
            os.environ["AUTH_ROOT"] = self._old_auth
        if self._old_hist is None:
            os.environ.pop("HISTORY_ROOT", None)
        else:
            os.environ["HISTORY_ROOT"] = self._old_hist
        if self._old_secret is None:
            os.environ.pop("AUTH_SECRET", None)
        else:
            os.environ["AUTH_SECRET"] = self._old_secret
        self._tmp.cleanup()

    def test_password_is_hashed_and_short_rejected(self) -> None:
        with self.assertRaises(AuthError):
            auth_store.create_user(login="ann", password="1111", role="worker")
        user = auth_store.create_user(login="ann", password="11111", role="admin")
        self.assertNotIn("11111", str(user))
        with auth_store._connect() as conn:
            row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
        self.assertTrue(str(row["password_hash"]).startswith("pbkdf2$"))
        self.assertNotIn("11111", row["password_hash"])

    def test_last_admin_cannot_be_blocked_or_demoted(self) -> None:
        admin = auth_store.create_user(login="solo", password="11111", role="admin")
        other = auth_store.create_user(login="second", password="11111", role="admin")
        auth_store.set_blocked(other["id"], True)
        with self.assertRaises(AuthError):
            auth_store.set_blocked(admin["id"], True)
        with self.assertRaises(AuthError):
            auth_store.set_role(admin["id"], "worker")
        auth_store.set_blocked(other["id"], False)
        blocked = auth_store.set_blocked(other["id"], True)
        self.assertTrue(blocked["blocked"])

    def test_anonymous_cannot_open_history_or_files(self) -> None:
        self.assertEqual(self.client.get("/api/v1/history/").status_code, 401)
        self.assertEqual(self.client.get("/").status_code, 303)

    def test_worker_sees_only_own_shipments_admin_sees_old_rows(self) -> None:
        worker = auth_store.create_user(login="worker1", password="11111", role="worker")
        other = auth_store.create_user(login="worker2", password="11111", role="worker")
        auth_store.create_user(login="boss", password="11111", role="admin")
        src = Path(self._tmp.name) / "a.xlsx"
        src.write_bytes(b"aa")
        own = history_store.create_draft(
            title="Своя",
            profile_type="18233",
            workspace={"title": "Своя", "items": [], "header_fields": {"invoice_no": "INV-1"}},
            sources=[(src, "a.xlsx")],
            owner_user_id=worker["id"],
            owner_login=worker["login"],
        )
        foreign = history_store.create_draft(
            title="Чужая",
            profile_type="18233",
            workspace={"title": "Чужая", "items": [], "header_fields": {"invoice_no": "INV-2"}},
            sources=[(src, "a.xlsx")],
            owner_user_id=other["id"],
            owner_login=other["login"],
        )
        legacy = history_store.create_draft(
            title="Старая",
            profile_type="18233",
            workspace={"title": "Старая", "items": []},
            sources=[(src, "a.xlsx")],
        )
        self.client.post("/api/v1/auth/login", json={"login": "worker1", "password": "11111"})
        listed = self.client.get("/api/v1/history/").json()
        ids = {item["id"] for item in listed["items"]}
        self.assertEqual(ids, {own})
        self.assertEqual(self.client.get(f"/api/v1/history/{foreign}").status_code, 404)
        self.client.post("/api/v1/auth/logout")
        self.client.post("/api/v1/auth/login", json={"login": "boss", "password": "11111"})
        admin_ids = {item["id"] for item in self.client.get("/api/v1/history/").json()["items"]}
        self.assertEqual(admin_ids, {own, foreign, legacy})
        legacy_row = next(item for item in self.client.get("/api/v1/history/").json()["items"] if item["id"] == legacy)
        self.assertTrue(legacy_row["owner_unknown"])
        self.assertEqual(self.client.get("/api/v1/auth/audit").status_code, 200)
        self.client.post("/api/v1/auth/logout")
        self.client.post("/api/v1/auth/login", json={"login": "worker1", "password": "11111"})
        self.assertEqual(self.client.get("/api/v1/auth/audit").status_code, 403)
        self.assertEqual(self.client.get("/users").status_code, 303)

    def test_failed_login_is_not_in_the_journal(self) -> None:
        auth_store.create_user(login="boss", password="11111", role="admin")
        bad = self.client.post("/api/v1/auth/login", json={"login": "boss", "password": "wrong"})
        self.assertEqual(bad.status_code, 401)
        self.client.post("/api/v1/auth/login", json={"login": "boss", "password": "11111"})
        actions = [ev["action"] for group in self.client.get("/api/v1/auth/audit").json()["items"] for ev in group["events"]]
        self.assertIn("login", actions)
        self.assertNotIn("failed", actions)


if __name__ == "__main__":
    unittest.main()
