"""Covers ingestion/runner.py's run_tracked()/cancel_job() and
api/routers/ingest.py's POST /ingest/{job_id}/cancel + GET /ingest/history --
the "Stop ingestion" feature. Unlike pipeline/runner.py's equivalent (which
needs a fake LangGraph checkpoint, see tests/test_runner_cancel.py), an
ingestion job's whole state already lives in api/ingest_jobs.py's real
in-memory dict, so these tests exercise that directly rather than mocking it.
"""
from __future__ import annotations

import asyncio
import time

import jwt
import pytest
from fastapi.testclient import TestClient

from api import ingest_jobs
from api.main import app
from config import settings
from ingestion import ingest_job_registry, runner, runner_jobs

client = TestClient(app, raise_server_exceptions=False)


def _token(username: str = "ingest_test_user", role: str = "user") -> str:
    payload = {"sub": username, "role": role, "exp": time.time() + 3600}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_token()}"}


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "JOBS_DIR", str(tmp_path / "jobs"))
    ingest_jobs._jobs.clear()
    runner._active_tasks.clear()
    import ingestion.ingest_job_registry as registry_module

    registry_module._entries = None
    yield
    ingest_jobs._jobs.clear()
    runner._active_tasks.clear()
    registry_module._entries = None


# -- ingestion/runner.py unit tests -----------------------------------------


def test_cancel_job_cancels_in_flight_task_and_marks_status():
    async def _body():
        ingest_jobs.register_job("job-1", kind="code")
        cancelled = False

        async def _long_running():
            nonlocal cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled = True
                raise

        runner.run_tracked("job-1", _long_running())
        await asyncio.sleep(0)  # let the task reach its own await point first

        await runner.cancel_job("job-1")

        assert cancelled
        assert ingest_jobs.get_ingest_job("job-1")["status"] == "cancelled"
        assert "job-1" not in runner._active_tasks

    asyncio.run(_body())


def test_cancel_job_works_with_no_active_task():
    ingest_jobs.register_job("job-2", kind="documents")

    asyncio.run(runner.cancel_job("job-2"))

    assert ingest_jobs.get_ingest_job("job-2")["status"] == "cancelled"


def test_cancel_job_raises_for_unknown_job():
    with pytest.raises(ValueError, match="No ingestion job found"):
        asyncio.run(runner.cancel_job("does-not-exist"))


@pytest.mark.parametrize("status_setter", ["done", "error", "cancelled"])
def test_cancel_job_raises_for_already_terminal_job(status_setter):
    ingest_jobs.register_job("job-3", kind="code")
    ingest_jobs._jobs["job-3"]["status"] = status_setter

    with pytest.raises(ValueError, match="already"):
        asyncio.run(runner.cancel_job("job-3"))


# -- Router-level tests ------------------------------------------------------


def test_cancel_endpoint_returns_not_found_for_unknown_job():
    resp = client.post("/api/ingest/does-not-exist/cancel", headers=_auth_headers())

    assert resp.status_code == 404


def test_cancel_endpoint_returns_conflict_for_already_terminal_job():
    ingest_jobs.register_job("job-4", kind="code")
    ingest_jobs._jobs["job-4"]["status"] = "done"

    resp = client.post("/api/ingest/job-4/cancel", headers=_auth_headers())

    assert resp.status_code == 409


def test_cancel_endpoint_stops_a_running_job_and_records_history(monkeypatch, tmp_path):
    # run_tracked() needs a running event loop -- submitting through the real
    # HTTP endpoint (rather than calling runner.run_tracked directly from
    # this sync test function) schedules it on TestClient's own loop, which
    # the follow-up cancel call below reuses.
    async def _never_finishes(repo_path, progress_callback=None, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(runner_jobs, "ingest_code", _never_finishes)

    start_resp = client.post(
        "/api/ingest/code", json={"repo_path": str(tmp_path)}, headers=_auth_headers()
    )
    job_id = start_resp.json()["job_id"]

    resp = client.post(f"/api/ingest/{job_id}/cancel", headers=_auth_headers())

    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    assert ingest_jobs.get_ingest_job(job_id)["status"] == "cancelled"
    history = ingest_job_registry.list_history()
    assert any(entry["job_id"] == job_id and entry["status"] == "cancelled" for entry in history)


def test_history_endpoint_returns_recorded_entries():
    ingest_job_registry.record_completed_job("job-6", "documents", "done", {"files_processed": 2}, [])

    resp = client.get("/api/ingest/history", headers=_auth_headers())

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["job_id"] == "job-6"
    assert body[0]["status"] == "done"


def test_history_returns_most_recent_first():
    ingest_job_registry.record_completed_job("older", "code", "done", {}, [])
    ingest_job_registry.record_completed_job("newer", "code", "done", {}, [])

    resp = client.get("/api/ingest/history", headers=_auth_headers())

    job_ids = [entry["job_id"] for entry in resp.json()]
    assert job_ids == ["newer", "older"]
