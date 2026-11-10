"""Log a TestClient in against a temp AUTH_ROOT."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient


def login(client: TestClient, root: Path, *, login_name: str = "admin", password: str = "11111", role: str = "admin") -> None:
    os.environ["AUTH_ROOT"] = str(root)
    os.environ["AUTH_SECRET"] = "test-secret-value"
    from app.services import auth_store

    auth_store.init_db()
    if auth_store.get_by_login(login_name) is None:
        auth_store.create_user(login=login_name, password=password, role=role)
    response = client.post("/api/v1/auth/login", json={"login": login_name, "password": password})
    if response.status_code != 200:
        raise AssertionError(response.text)
