"""Covers api/routers/prompts.py's GET/PUT /api/prompts/ask endpoints
(Phase L-D): read is any authenticated user, write is admin-gated (matching
api/routers/settings.py's own GET/PUT split), and an invalid template is
rejected with the store's friendly 400 message."""
from __future__ import annotations

import time

import jwt
import pytest
from fastapi.testclient import TestClient

import prompt_store
from api.main import app
from config import settings
from prompts.ask_prompts import BUSINESS_ASK_SYSTEM_PROMPT, TECHNICAL_ASK_SYSTEM_PROMPT

client = TestClient(app, raise_server_exceptions=False)


def _token(username: str = "prompts_test_user", role: str = "user") -> str:
    payload = {"sub": username, "role": role, "exp": time.time() + 3600}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _auth_headers(role: str = "user") -> dict:
    return {"Authorization": f"Bearer {_token(role=role)}"}


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "JOBS_DIR", str(tmp_path / "jobs"))
    prompt_store._cache = None
    yield
    prompt_store._cache = None


def test_get_ask_prompts_defaults_to_the_built_in_templates():
    resp = client.get("/api/prompts/ask", headers=_auth_headers())

    assert resp.status_code == 200
    body = resp.json()
    assert body["technical"] == {
        "custom": None,
        "default": TECHNICAL_ASK_SYSTEM_PROMPT,
        "effective": TECHNICAL_ASK_SYSTEM_PROMPT,
    }
    assert body["business"] == {
        "custom": None,
        "default": BUSINESS_ASK_SYSTEM_PROMPT,
        "effective": BUSINESS_ASK_SYSTEM_PROMPT,
    }


def test_put_ask_prompt_saves_and_reflects_in_get():
    put_resp = client.put(
        "/api/prompts/ask/technical", json={"template": "Custom: {context}"}, headers=_auth_headers("admin")
    )

    assert put_resp.status_code == 200
    assert put_resp.json()["effective"] == "Custom: {context}"

    get_resp = client.get("/api/prompts/ask", headers=_auth_headers())
    assert get_resp.json()["technical"]["custom"] == "Custom: {context}"


def test_put_ask_prompt_with_none_resets_to_default():
    client.put("/api/prompts/ask/business", json={"template": "Custom: {context}"}, headers=_auth_headers("admin"))

    reset_resp = client.put("/api/prompts/ask/business", json={"template": None}, headers=_auth_headers("admin"))

    assert reset_resp.status_code == 200
    assert reset_resp.json()["custom"] is None
    assert reset_resp.json()["effective"] == BUSINESS_ASK_SYSTEM_PROMPT


def test_put_ask_prompt_rejects_invalid_template():
    resp = client.put(
        "/api/prompts/ask/technical", json={"template": "No placeholder here"}, headers=_auth_headers("admin")
    )

    assert resp.status_code == 400
    assert "context" in resp.json()["detail"]


def test_put_ask_prompt_requires_admin():
    resp = client.put(
        "/api/prompts/ask/technical", json={"template": "Custom: {context}"}, headers=_auth_headers("user")
    )

    assert resp.status_code == 403


def test_get_ask_prompts_requires_auth():
    resp = client.get("/api/prompts/ask")

    assert resp.status_code == 401
