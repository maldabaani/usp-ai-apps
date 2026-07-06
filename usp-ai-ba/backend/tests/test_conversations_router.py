"""Covers api/routers/conversations.py's CRUD endpoints (Phase L-E): full
create/list/get/delete round trip plus per-owner isolation (a non-owner
gets 404, matching conversation_store.get_conversation's own not-found-vs-
not-yours ambiguity by design)."""
from __future__ import annotations

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from api.main import app
from config import settings

client = TestClient(app, raise_server_exceptions=False)


def _token(username: str, role: str = "user") -> str:
    payload = {"sub": username, "role": role, "exp": time.time() + 3600}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _auth_headers(username: str = "alice") -> dict:
    return {"Authorization": f"Bearer {_token(username)}"}


@pytest.fixture(autouse=True)
def _isolated_jobs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "JOBS_DIR", str(tmp_path / "jobs"))


def test_create_list_get_delete_round_trip():
    create_resp = client.post("/api/conversations", json={"kind": "technical"}, headers=_auth_headers())
    assert create_resp.status_code == 200
    conversation = create_resp.json()
    assert conversation["kind"] == "technical"
    assert conversation["messages"] == []

    list_resp = client.get("/api/conversations", headers=_auth_headers())
    assert list_resp.status_code == 200
    assert [c["id"] for c in list_resp.json()] == [conversation["id"]]

    get_resp = client.get(f"/api/conversations/{conversation['id']}", headers=_auth_headers())
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == conversation["id"]

    delete_resp = client.delete(f"/api/conversations/{conversation['id']}", headers=_auth_headers())
    assert delete_resp.status_code == 200

    get_after_delete = client.get(f"/api/conversations/{conversation['id']}", headers=_auth_headers())
    assert get_after_delete.status_code == 404


def test_create_conversation_accepts_a_custom_title():
    resp = client.post(
        "/api/conversations", json={"kind": "business", "title": "Q3 planning"}, headers=_auth_headers()
    )

    assert resp.json()["title"] == "Q3 planning"


def test_get_returns_404_for_unknown_conversation():
    resp = client.get("/api/conversations/does-not-exist", headers=_auth_headers())

    assert resp.status_code == 404


def test_get_returns_404_for_another_users_conversation():
    create_resp = client.post("/api/conversations", json={"kind": "technical"}, headers=_auth_headers("alice"))
    conversation_id = create_resp.json()["id"]

    resp = client.get(f"/api/conversations/{conversation_id}", headers=_auth_headers("bob"))

    assert resp.status_code == 404


def test_delete_returns_404_for_another_users_conversation():
    create_resp = client.post("/api/conversations", json={"kind": "technical"}, headers=_auth_headers("alice"))
    conversation_id = create_resp.json()["id"]

    resp = client.delete(f"/api/conversations/{conversation_id}", headers=_auth_headers("bob"))

    assert resp.status_code == 404


def test_list_is_scoped_per_user():
    client.post("/api/conversations", json={"kind": "technical"}, headers=_auth_headers("alice"))
    client.post("/api/conversations", json={"kind": "technical"}, headers=_auth_headers("bob"))

    alice_list = client.get("/api/conversations", headers=_auth_headers("alice")).json()
    bob_list = client.get("/api/conversations", headers=_auth_headers("bob")).json()

    assert len(alice_list) == 1
    assert len(bob_list) == 1


def test_conversations_require_auth():
    resp = client.get("/api/conversations")

    assert resp.status_code == 401
