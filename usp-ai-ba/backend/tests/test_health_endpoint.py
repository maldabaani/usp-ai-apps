"""GET /health: a real liveness check (opens/confirms StoryForge's own checkpointer DB),
not just static config booleans -- what a Docker HEALTHCHECK or external monitor polls.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app
from config import settings

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _isolated_jobs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "JOBS_DIR", str(tmp_path / "jobs"))
    import pipeline.graph as graph_module

    graph_module._graph = None  # force a fresh get_graph() under the new JOBS_DIR
    yield
    graph_module._graph = None


def test_health_reports_ok_with_a_reachable_checkpointer() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checkpointer"] == "ok"


def test_health_needs_no_auth() -> None:
    resp = client.get("/health")
    assert resp.status_code != 401
