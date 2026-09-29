"""Covers the merge plan's Phase 7: "Send to DevCrew" --
POST /api/devcrew/runs/from-storyforge-epic. Uses the real merged app via
TestClient (gated behind TEST_DATABASE_URL, same convention as
tests/test_devcrew_auth.py) since this endpoint genuinely needs a real
DevCrew Container (RunManager/RunStore) to create a run against -- but
pipeline.runner.get_job_state is monkeypatched (deferred-imported inside
the route handler, so patching the original module attribute works),
avoiding any real LangGraph/SQLite StoryForge job lookup.
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


def _auth_headers(role: str = "user") -> dict:
    payload = {"sub": "devcrew_bridge_test", "role": role, "exp": time.time() + 3600}
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return {"Authorization": f"Bearer {token}"}


APPROVED_STORY = {
    "epic_title": "Add a discount field",
    "user_story": "As a shopper, I want a discount field on my order.",
    "acceptance_criteria": ["Given an order, when discount is applied, then total reflects it."],
    "dev_tasks": [
        {
            "title": "1 Task - Add discount field",
            "user_story": "As a backend dev...",
            "acceptance_criteria": [".."],
            "technical_approach": ["Add a discount field"],
            "affected_components": {
                "frontend": "N/A",
                "backend": "the order service",
                "middleware": "N/A",
                "database": "N/A",
            },
            "api_contract": {"endpoint": "POST /orders"},
            "business_rules": ["Rule 1"],
            "error_handling": ["Scenario 1"],
        }
    ],
    "unit_test_tasks": [
        {
            "title": "1 Unit Test",
            "test_objective": "Verify",
            "test_scenarios": {"happy_path": ["TC-01"]},
            "test_data": {},
            "mock_setup": [],
            "assertions": [],
        }
    ],
}

UNSUPPORTED_STORY = {
    **APPROVED_STORY,
    "dev_tasks": [
        {
            **APPROVED_STORY["dev_tasks"][0],
            "affected_components": {
                "frontend": "the legacy jQuery admin screen",
                "backend": "N/A",
                "middleware": "N/A",
                "database": "N/A",
            },
        }
    ],
}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEVCREW_DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("DEVCREW_STARTUP_HEALTH_STRICT", "false")
    monkeypatch.setenv("DEVCREW_GITHUB_TOKEN", "dummy-test-token")
    from api.main import app

    with TestClient(app) as c:
        yield c


def _fake_job_state(stories):
    async def fake(job_id):
        if job_id != "job-1":
            return None
        return {"approved_stories": stories}

    return fake


def test_unknown_job_404s(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    import pipeline.runner as runner

    monkeypatch.setattr(runner, "get_job_state", _fake_job_state([APPROVED_STORY]))

    resp = client.post(
        "/api/devcrew/runs/from-storyforge-epic",
        json={"job_id": "no-such-job", "epic_index": 0, "repo_target": "acme/shop"},
        headers=_auth_headers(),
    )

    assert resp.status_code == 404


def test_out_of_range_epic_index_404s(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    import pipeline.runner as runner

    monkeypatch.setattr(runner, "get_job_state", _fake_job_state([APPROVED_STORY]))

    resp = client.post(
        "/api/devcrew/runs/from-storyforge-epic",
        json={"job_id": "job-1", "epic_index": 5, "repo_target": "acme/shop"},
        headers=_auth_headers(),
    )

    assert resp.status_code == 404


def test_unapproved_epic_400s(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    import pipeline.runner as runner

    monkeypatch.setattr(runner, "get_job_state", _fake_job_state([None]))  # deleted before submit

    resp = client.post(
        "/api/devcrew/runs/from-storyforge-epic",
        json={"job_id": "job-1", "epic_index": 0, "repo_target": "acme/shop"},
        headers=_auth_headers(),
    )

    assert resp.status_code == 400


def test_unsupported_stack_422s(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    import pipeline.runner as runner

    monkeypatch.setattr(runner, "get_job_state", _fake_job_state([UNSUPPORTED_STORY]))

    resp = client.post(
        "/api/devcrew/runs/from-storyforge-epic",
        json={"job_id": "job-1", "epic_index": 0, "repo_target": "acme/shop"},
        headers=_auth_headers(),
    )

    assert resp.status_code == 422
    assert "legacy" in resp.json()["detail"].lower()


def test_valid_epic_creates_a_run_and_records_the_dispatch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    import api.devcrew_dispatch_registry as registry
    import pipeline.runner as runner

    monkeypatch.setattr(runner, "get_job_state", _fake_job_state([APPROVED_STORY]))
    registry._dispatches = None  # fresh load under this test's JOBS_DIR

    resp = client.post(
        "/api/devcrew/runs/from-storyforge-epic",
        json={"job_id": "job-1", "epic_index": 0, "repo_target": "acme/shop"},
        headers=_auth_headers(),
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["repo_target"] == "acme/shop"
    assert "Add a discount field" in body["request"]

    dispatches = registry.list_dispatches_for_job("job-1")
    assert len(dispatches) == 1
    assert dispatches[0]["run_id"] == body["id"]
    assert dispatches[0]["epic_title"] == "Add a discount field"


def test_requires_auth(client: TestClient):
    resp = client.post(
        "/api/devcrew/runs/from-storyforge-epic",
        json={"job_id": "job-1", "epic_index": 0, "repo_target": "acme/shop"},
    )
    assert resp.status_code == 401
