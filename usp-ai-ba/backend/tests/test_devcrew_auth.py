"""Covers Phase 3 of the DevCrew merge: every devcrew/api/*.py router is
gated by StoryForge's own require_auth/require_admin (api/deps.py) once
mounted under /api/devcrew in api/main.py -- DevCrew shipped with zero
auth of its own (see devcrew/api/deps.py's original ContainerDep, which
only read request.app.state.container with no auth check at all).

Uses the real merged app (api.main.app) via TestClient so the lifespan
actually runs and DevCrew's own Container gets assembled -- gated behind
TEST_DATABASE_URL, same skip convention tests/devcrew's own Postgres-
backed tests already use, since this needs a real Postgres reachable at
that URL (auth itself needs no real DB, but booting the real app's
lifespan to prove the auth gate sits in front of real, wired-up routes
does). Run with:
    TEST_DATABASE_URL=postgresql+asyncpg://devcrew:devcrew@localhost:5432/devcrew_test pytest tests/test_devcrew_auth.py
"""
from __future__ import annotations

import os
import time

import jwt
import pytest
from fastapi.testclient import TestClient

from config import settings

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")


def _token(username: str = "devcrew_auth_test", role: str = "user") -> str:
    payload = {"sub": username, "role": role, "exp": time.time() + 3600}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEVCREW_DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("DEVCREW_STARTUP_HEALTH_STRICT", "false")
    from api.main import app

    with TestClient(app) as c:
        yield c


def test_devcrew_health_requires_auth(client: TestClient) -> None:
    resp = client.get("/api/devcrew/health")
    assert resp.status_code == 401


def test_devcrew_runs_list_requires_auth(client: TestClient) -> None:
    resp = client.get("/api/devcrew/runs")
    assert resp.status_code == 401


def test_devcrew_health_works_with_a_plain_user_token(client: TestClient) -> None:
    resp = client.get(
        "/api/devcrew/health", headers={"Authorization": f"Bearer {_token(role='user')}"}
    )
    assert resp.status_code != 401


def test_devcrew_runs_query_param_token_works_like_storyforges_own_sse_routes(
    client: TestClient,
) -> None:
    # require_auth already accepts ?token= as a header fallback (for
    # EventSource, which can't set a custom header) -- confirms DevCrew's
    # own SSE route (GET /runs/{id}/events) will work the same way once a
    # run exists, with no DevCrew-side change needed.
    resp = client.get(f"/api/devcrew/runs?token={_token()}")
    assert resp.status_code != 401


def test_watched_repos_write_requires_admin_not_just_auth(client: TestClient) -> None:
    user_headers = {"Authorization": f"Bearer {_token(role='user')}"}
    resp = client.post(
        "/api/devcrew/watched-repos", json={"repo": "octocat/hello-world"}, headers=user_headers
    )
    assert resp.status_code == 403


def test_watched_repos_write_works_for_admin(client: TestClient) -> None:
    admin_headers = {"Authorization": f"Bearer {_token(role='admin')}"}
    resp = client.post(
        "/api/devcrew/watched-repos", json={"repo": "octocat/hello-world"}, headers=admin_headers
    )
    # Not 401/403 -- got past both auth gates. Whatever happens next
    # (201, or a 4xx from DevCrew's own validation/GitHub-not-configured
    # logic) is DevCrew's own business logic, out of scope for this test.
    assert resp.status_code not in (401, 403)


def test_watched_repos_read_only_needs_auth_not_admin(client: TestClient) -> None:
    user_headers = {"Authorization": f"Bearer {_token(role='user')}"}
    resp = client.get("/api/devcrew/watched-repos", headers=user_headers)
    assert resp.status_code == 200
