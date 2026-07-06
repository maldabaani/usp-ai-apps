"""Covers api/routers/watch.py's CRUD over watched auto-re-ingestion
targets (Phase L-C): add/list/toggle/delete plus admin-gating on the three
mutating endpoints (GET is any authenticated user, per the same convention
as GET /api/settings)."""
from __future__ import annotations

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from api.main import app
from config import settings
from ingestion import watch_registry
from ingestion.watcher import watcher

client = TestClient(app, raise_server_exceptions=False)


def _token(username: str = "watch_test_user", role: str = "user") -> str:
    payload = {"sub": username, "role": role, "exp": time.time() + 3600}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _auth_headers(role: str = "user") -> dict:
    return {"Authorization": f"Bearer {_token(role=role)}"}


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "JOBS_DIR", str(tmp_path / "jobs"))
    watch_registry._entries = None
    yield
    watcher.stop_all()
    watch_registry._entries = None


def test_add_list_toggle_delete_round_trip(tmp_path):
    watch_dir = tmp_path / "watched"
    watch_dir.mkdir()

    add_resp = client.post(
        "/api/watch/targets", json={"path": str(watch_dir), "kind": "documents"}, headers=_auth_headers("admin")
    )
    assert add_resp.status_code == 200
    target = add_resp.json()
    assert target["path"] == str(watch_dir)
    assert target["kind"] == "documents"
    assert target["enabled"] is True

    list_resp = client.get("/api/watch/targets", headers=_auth_headers())
    assert list_resp.status_code == 200
    assert [t["id"] for t in list_resp.json()] == [target["id"]]

    toggle_resp = client.patch(
        f"/api/watch/targets/{target['id']}", json={"enabled": False}, headers=_auth_headers("admin")
    )
    assert toggle_resp.status_code == 200
    assert toggle_resp.json()["enabled"] is False

    delete_resp = client.delete(f"/api/watch/targets/{target['id']}", headers=_auth_headers("admin"))
    assert delete_resp.status_code == 200

    list_after_delete = client.get("/api/watch/targets", headers=_auth_headers())
    assert list_after_delete.json() == []


def test_add_rejects_a_path_that_is_not_a_directory(tmp_path):
    not_a_dir = tmp_path / "missing"

    resp = client.post(
        "/api/watch/targets", json={"path": str(not_a_dir), "kind": "code"}, headers=_auth_headers("admin")
    )

    assert resp.status_code == 400


def test_toggle_returns_404_for_unknown_target():
    resp = client.patch(
        "/api/watch/targets/does-not-exist", json={"enabled": False}, headers=_auth_headers("admin")
    )

    assert resp.status_code == 404


def test_delete_returns_404_for_unknown_target():
    resp = client.delete("/api/watch/targets/does-not-exist", headers=_auth_headers("admin"))

    assert resp.status_code == 404


def test_mutating_endpoints_require_admin(tmp_path):
    watch_dir = tmp_path / "watched"
    watch_dir.mkdir()

    add_resp = client.post(
        "/api/watch/targets", json={"path": str(watch_dir), "kind": "documents"}, headers=_auth_headers("user")
    )
    assert add_resp.status_code == 403

    # Seed one target as admin so toggle/delete have something to act on.
    seeded = client.post(
        "/api/watch/targets", json={"path": str(watch_dir), "kind": "documents"}, headers=_auth_headers("admin")
    ).json()

    toggle_resp = client.patch(
        f"/api/watch/targets/{seeded['id']}", json={"enabled": False}, headers=_auth_headers("user")
    )
    assert toggle_resp.status_code == 403

    delete_resp = client.delete(f"/api/watch/targets/{seeded['id']}", headers=_auth_headers("user"))
    assert delete_resp.status_code == 403


def test_list_requires_auth():
    resp = client.get("/api/watch/targets")

    assert resp.status_code == 401
