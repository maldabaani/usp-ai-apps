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


@pytest.fixture(autouse=True)
def _job_states(monkeypatch):
    """In-memory stand-in for get_job_state(job_id) -- populated per-test
    (via `_job_states["job-x"] = {...}`) instead of touching a real
    LangGraph/SQLite checkpointer. Used by delete/rerun, which both need to
    read a job's real solution_doc_path/solution_doc_text."""
    states: dict[str, dict] = {}

    async def fake_get_job_state(job_id: str):
        return states.get(job_id)

    monkeypatch.setattr(assess, "get_job_state", fake_get_job_state)
    return states


@pytest.fixture(autouse=True)
def _stub_run_assessment(monkeypatch):
    """submit_assessment/rerun_assessment kick off the real LangGraph
    pipeline via _run_assessment -- out of scope here (see module
    docstring). Stubbed to capture the initial_state it was given instead,
    so tests can assert on solution_doc_path/solution_doc_text without ever
    touching a real graph."""
    captured: list[dict] = []

    async def fake_run_assessment(initial_state):
        captured.append(initial_state)

    monkeypatch.setattr(assess, "_run_assessment", fake_run_assessment)
    return captured


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


def test_delete_assessment_removes_uploaded_pdf(tmp_path, _stub_delete_job, _job_states):
    register_assess_job("job-2", "12345", "Test PPM", "AI-BA", "document")
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = uploads_dir / "job-2.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake content")
    _job_states["job-2"] = {"solution_doc_path": str(pdf_path)}

    resp = client.delete("/api/assess/job-2", headers=_auth_headers())

    assert resp.status_code == 204
    assert not pdf_path.exists()


def test_delete_assessment_removes_uploaded_docx(tmp_path, _stub_delete_job, _job_states):
    register_assess_job("job-docx", "12345", "Test PPM", "AI-BA", "document")
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    docx_path = uploads_dir / "job-docx.docx"
    docx_path.write_bytes(b"fake docx content")
    _job_states["job-docx"] = {"solution_doc_path": str(docx_path)}

    resp = client.delete("/api/assess/job-docx", headers=_auth_headers())

    assert resp.status_code == 204
    assert not docx_path.exists()


def test_delete_pasted_text_job_is_a_noop_filewise(_stub_delete_job, _job_states):
    register_assess_job("text-job", "12345", "Test PPM", "AI-BA", "document")
    _job_states["text-job"] = {"solution_doc_path": "", "solution_doc_text": "some pasted text"}

    resp = client.delete("/api/assess/text-job", headers=_auth_headers())

    assert resp.status_code == 204


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


_SUBMIT_FORM = {"ppm_number": "12345", "ppm_name": "Test PPM", "system_name": "AI-BA"}


def test_submit_assessment_rejects_missing_file_and_text():
    resp = client.post("/api/assess", headers=_auth_headers(), data=_SUBMIT_FORM)

    assert resp.status_code == 400


def test_submit_assessment_rejects_both_file_and_text():
    resp = client.post(
        "/api/assess",
        headers=_auth_headers(),
        files={"file": ("sdd.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={**_SUBMIT_FORM, "solution_doc_text": "some pasted text"},
    )

    assert resp.status_code == 400


def test_submit_assessment_rejects_unsupported_extension():
    resp = client.post(
        "/api/assess",
        headers=_auth_headers(),
        files={"file": ("sdd.txt", b"plain text", "text/plain")},
        data=_SUBMIT_FORM,
    )

    assert resp.status_code == 400
    assert ".pdf" in resp.json()["detail"] and ".docx" in resp.json()["detail"]


def test_submit_assessment_rejects_legacy_doc_extension():
    resp = client.post(
        "/api/assess",
        headers=_auth_headers(),
        files={"file": ("sdd.doc", b"fake legacy doc bytes", "application/msword")},
        data=_SUBMIT_FORM,
    )

    assert resp.status_code == 400


def test_submit_assessment_accepts_docx_upload_and_preserves_suffix(tmp_path, _stub_run_assessment):
    resp = client.post(
        "/api/assess",
        headers=_auth_headers(),
        files={
            "file": (
                "sdd.docx",
                b"fake docx bytes",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data=_SUBMIT_FORM,
    )

    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    saved = list((tmp_path / "uploads").glob(f"{job_id}.*"))
    assert len(saved) == 1
    assert saved[0].suffix == ".docx"
    assert _stub_run_assessment[0]["solution_doc_path"] == str(saved[0])


def test_submit_assessment_accepts_pasted_text_with_no_file(_stub_run_assessment):
    resp = client.post(
        "/api/assess",
        headers=_auth_headers(),
        data={**_SUBMIT_FORM, "solution_doc_text": "The SDD says the system must do X."},
    )

    assert resp.status_code == 200
    assert _stub_run_assessment[0]["solution_doc_path"] == ""
    assert _stub_run_assessment[0]["solution_doc_text"] == "The SDD says the system must do X."


def test_rerun_unknown_job_returns_404():
    resp = client.post("/api/assess/rerun/does-not-exist", headers=_auth_headers())

    assert resp.status_code == 404


def test_rerun_preserves_docx_suffix(tmp_path, _job_states, _stub_run_assessment):
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    original_path = uploads_dir / "orig-job.docx"
    original_path.write_bytes(b"fake docx bytes")
    _job_states["orig-job"] = {
        "ppm_number": "1",
        "ppm_name": "A",
        "system_name": "S",
        "solution_doc_path": str(original_path),
        "solution_doc_text": "",
        "output_mode": "document",
    }

    resp = client.post("/api/assess/rerun/orig-job", headers=_auth_headers())

    assert resp.status_code == 200
    new_job_id = resp.json()["job_id"]
    new_files = list(uploads_dir.glob(f"{new_job_id}.*"))
    assert len(new_files) == 1
    assert new_files[0].suffix == ".docx"
    assert _stub_run_assessment[0]["solution_doc_path"] == str(new_files[0])


def test_rerun_missing_original_file_returns_404(_job_states):
    _job_states["orig-job"] = {
        "ppm_number": "1",
        "ppm_name": "A",
        "system_name": "S",
        "solution_doc_path": "/nonexistent/path.pdf",
        "solution_doc_text": "",
        "output_mode": "document",
    }

    resp = client.post("/api/assess/rerun/orig-job", headers=_auth_headers())

    assert resp.status_code == 404


def test_rerun_pasted_text_job_carries_text_forward_with_no_file(tmp_path, _job_states, _stub_run_assessment):
    _job_states["orig-text-job"] = {
        "ppm_number": "1",
        "ppm_name": "A",
        "system_name": "S",
        "solution_doc_path": "",
        "solution_doc_text": "Original pasted SDD text.",
        "output_mode": "document",
    }

    resp = client.post("/api/assess/rerun/orig-text-job", headers=_auth_headers())

    assert resp.status_code == 200
    assert _stub_run_assessment[0]["solution_doc_path"] == ""
    assert _stub_run_assessment[0]["solution_doc_text"] == "Original pasted SDD text."
    uploads_dir = tmp_path / "uploads"
    assert not uploads_dir.exists() or not list(uploads_dir.glob("*"))
