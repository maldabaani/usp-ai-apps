"""Covers api/routers/assess.py's DELETE /assess/{job_id} -- deleting an
assessment from the dashboard's checkbox multi-select. The other endpoints
in this router (submit/rerun/retry/recreate/update) run the real LangGraph
pipeline and aren't covered here; delete_job (pipeline/runner.py) itself is
covered directly in tests/test_runner_cancel.py against a fake graph, so
this file only needs to monkeypatch it to a no-op to test the router's own
404/registry/file-cleanup behavior in isolation.
"""
import time

import jwt
import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.routers import assess
from api.job_registry import list_assess_jobs, register_assess_job
from config import settings

client = TestClient(app, raise_server_exceptions=False)


def _token(username: str = "assess_test_user", role: str = "user") -> str:
    payload = {"sub": username, "role": role, "exp": time.time() + 3600}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_token()}"}


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setattr(settings, "UPLOADS_DIR", str(tmp_path / "uploads"))
    import api.job_registry as job_registry_module

    job_registry_module._jobs = None  # force a fresh _load() under the new JOBS_DIR
    yield
    job_registry_module._jobs = None


@pytest.fixture(autouse=True)
def _stub_delete_job(monkeypatch):
    """delete_job (pipeline/runner.py) touches a real LangGraph/SQLite
    checkpointer -- out of scope for this router-level test, which only
    needs to verify the endpoint's own 404/registry/file-cleanup behavior."""
    calls = []

    async def fake_delete_job(job_id: str) -> None:
        calls.append(job_id)

    monkeypatch.setattr(assess, "delete_job", fake_delete_job)
    return calls


def test_delete_assessment_returns_not_found_for_unknown_job():
    resp = client.delete("/api/assess/does-not-exist", headers=_auth_headers())

    assert resp.status_code == 404


def test_delete_assessment_removes_job_from_registry(_stub_delete_job):
    register_assess_job("job-1", "12345", "Test PPM", "AI-BA", "document")
    assert any(j["job_id"] == "job-1" for j in list_assess_jobs())

    resp = client.delete("/api/assess/job-1", headers=_auth_headers())

    assert resp.status_code == 204
    assert not any(j["job_id"] == "job-1" for j in list_assess_jobs())
    assert _stub_delete_job == ["job-1"]


def test_delete_assessment_removes_uploaded_pdf(tmp_path, _stub_delete_job):
    register_assess_job("job-2", "12345", "Test PPM", "AI-BA", "document")
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = uploads_dir / "job-2.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake content")

    resp = client.delete("/api/assess/job-2", headers=_auth_headers())

    assert resp.status_code == 204
    assert not pdf_path.exists()


def test_delete_assessment_does_not_error_when_pdf_already_missing(_stub_delete_job):
    register_assess_job("job-3", "12345", "Test PPM", "AI-BA", "document")

    resp = client.delete("/api/assess/job-3", headers=_auth_headers())

    assert resp.status_code == 204


def test_delete_assessment_leaves_other_jobs_in_registry(_stub_delete_job):
    register_assess_job("job-a", "1", "A", "AI-BA", "document")
    register_assess_job("job-b", "2", "B", "AI-BA", "document")

    resp = client.delete("/api/assess/job-a", headers=_auth_headers())

    assert resp.status_code == 204
    remaining_ids = {j["job_id"] for j in list_assess_jobs()}
    assert remaining_ids == {"job-b"}
