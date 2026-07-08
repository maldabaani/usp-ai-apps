"""Covers GET/PUT /api/settings, including anthropic_api_key/anthropic_model
and the ingestion pipeline's ingest_ollama_* fields."""
import time

import jwt
import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.user_store import create_user
from config import settings

client = TestClient(app, raise_server_exceptions=False)


def _token(username: str, role: str) -> str:
    payload = {"sub": username, "role": role, "exp": time.time() + 3600}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


@pytest.fixture(autouse=True)
def _no_real_env_writes(monkeypatch):
    """PUT /settings normally persists to backend/.env via
    config_store.update_env_file -- monkeypatch that specific call site
    (api.routers.settings' imported reference, not config_store's own
    module-level name, since the router already bound the function object at
    import time) to a no-op, so these tests only exercise the in-memory
    settings.apply_updates() path and never touch the real .env file on disk.
    """
    monkeypatch.setattr("api.routers.settings.update_env_file", lambda updates: None)


@pytest.fixture(scope="module", autouse=True)
def _admin_and_user():
    create_user("settings_test_admin", "adminpass", role="admin")
    create_user("settings_test_user", "userpass", role="user")


def test_get_settings_includes_ingest_ollama_fields():
    resp = client.get("/api/settings", headers={"Authorization": f"Bearer {_token('settings_test_user', 'user')}"})

    assert resp.status_code == 200
    body = resp.json()
    assert "anthropic_api_key_masked" in body
    assert "anthropic_model" in body
    assert "ingest_ollama_enabled" in body
    assert "ingest_ollama_model" in body
    assert "ollama_num_ctx" in body
    assert set(body["restart_required_fields"]) == {
        "ingest_ollama_enabled",
        "ingest_ollama_model",
    }


def test_get_settings_requires_auth():
    resp = client.get("/api/settings")
    assert resp.status_code == 401


def test_put_settings_requires_admin():
    resp = client.put(
        "/api/settings",
        json={"ingest_ollama_model": "llama3:8b"},
        headers={"Authorization": f"Bearer {_token('settings_test_user', 'user')}"},
    )
    assert resp.status_code == 403


def test_get_settings_includes_ask_qa_model_and_it_is_not_restart_required():
    resp = client.get("/api/settings", headers={"Authorization": f"Bearer {_token('settings_test_user', 'user')}"})

    assert resp.status_code == 200
    body = resp.json()
    assert "ask_qa_model" in body
    assert "ask_qa_model" not in body["restart_required_fields"]


def test_put_settings_updates_ask_qa_model():
    resp = client.put(
        "/api/settings",
        json={"ask_qa_model": "ollama"},
        headers={"Authorization": f"Bearer {_token('settings_test_admin', 'admin')}"},
    )

    assert resp.status_code == 200
    assert resp.json()["ask_qa_model"] == "ollama"

    get_resp = client.get(
        "/api/settings", headers={"Authorization": f"Bearer {_token('settings_test_admin', 'admin')}"}
    )
    assert get_resp.json()["ask_qa_model"] == "ollama"


def test_get_settings_includes_llm_request_timeout_and_it_is_not_restart_required():
    resp = client.get("/api/settings", headers={"Authorization": f"Bearer {_token('settings_test_user', 'user')}"})

    assert resp.status_code == 200
    body = resp.json()
    assert "llm_request_timeout_seconds" in body
    assert "llm_request_timeout_seconds" not in body["restart_required_fields"]


def test_get_settings_includes_ollama_embed_num_ctx_and_it_is_not_restart_required():
    resp = client.get("/api/settings", headers={"Authorization": f"Bearer {_token('settings_test_user', 'user')}"})

    assert resp.status_code == 200
    body = resp.json()
    assert "ollama_embed_num_ctx" in body
    assert "ollama_embed_num_ctx" not in body["restart_required_fields"]


def test_put_settings_updates_ollama_embed_num_ctx():
    original = settings.OLLAMA_EMBED_NUM_CTX
    try:
        resp = client.put(
            "/api/settings",
            json={"ollama_embed_num_ctx": 4096},
            headers={"Authorization": f"Bearer {_token('settings_test_admin', 'admin')}"},
        )

        assert resp.status_code == 200
        assert resp.json()["ollama_embed_num_ctx"] == 4096

        get_resp = client.get(
            "/api/settings", headers={"Authorization": f"Bearer {_token('settings_test_admin', 'admin')}"}
        )
        assert get_resp.json()["ollama_embed_num_ctx"] == 4096
    finally:
        settings.apply_updates({"OLLAMA_EMBED_NUM_CTX": original})


def test_put_settings_updates_llm_request_timeout_seconds():
    resp = client.put(
        "/api/settings",
        json={"llm_request_timeout_seconds": 600},
        headers={"Authorization": f"Bearer {_token('settings_test_admin', 'admin')}"},
    )

    assert resp.status_code == 200
    assert resp.json()["llm_request_timeout_seconds"] == 600

    get_resp = client.get(
        "/api/settings", headers={"Authorization": f"Bearer {_token('settings_test_admin', 'admin')}"}
    )
    assert get_resp.json()["llm_request_timeout_seconds"] == 600


def test_put_settings_updates_ingest_ollama_fields():
    resp = client.put(
        "/api/settings",
        json={
            "ingest_ollama_model": "llama3:8b",
            "ingest_ollama_enabled": True,
        },
        headers={"Authorization": f"Bearer {_token('settings_test_admin', 'admin')}"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ingest_ollama_model"] == "llama3:8b"
    assert body["ingest_ollama_enabled"] is True

    # Round-trips via a fresh GET too, not just the PUT response.
    get_resp = client.get(
        "/api/settings", headers={"Authorization": f"Bearer {_token('settings_test_admin', 'admin')}"}
    )
    assert get_resp.json()["ingest_ollama_model"] == "llama3:8b"


def test_put_settings_masks_anthropic_key_and_leaves_it_unchanged_when_echoed_back():
    admin_token = _token("settings_test_admin", "admin")

    first = client.put(
        "/api/settings",
        json={"anthropic_api_key": "sk-ant-real-secret-value-123456"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert first.status_code == 200
    mask = first.json()["anthropic_api_key_masked"]
    assert mask.endswith("3456")
    assert "sk-ant-real-secret-value" not in mask

    # PUTting the mask back (as the Angular settings page does when the
    # field wasn't touched) must not be treated as a new secret.
    real_key_before = settings.ANTHROPIC_API_KEY
    second = client.put(
        "/api/settings",
        json={"anthropic_api_key": mask, "anthropic_model": "claude-opus-4-8"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert second.status_code == 200
    assert settings.ANTHROPIC_API_KEY == real_key_before
    assert second.json()["anthropic_model"] == "claude-opus-4-8"


def test_settings_generation_bumps_on_every_apply_updates_call():
    before = settings.settings_generation
    client.put(
        "/api/settings",
        json={"ingest_ollama_model": "qwen2.5:14b"},
        headers={"Authorization": f"Bearer {_token('settings_test_admin', 'admin')}"},
    )
    assert settings.settings_generation == before + 1
