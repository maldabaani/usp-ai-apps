"""Covers api/routers/codemind_jobs.py, ported from
com.jslogicextractor.web.ExtractionJobController (the SSE .../qa/stream
route lives in api/routers/codemind_ask.py instead -- see
tests/test_codemind_ask_router.py).
"""
import time
import uuid

import jwt
import pytest
from fastapi.testclient import TestClient

from api.main import app
from codemind import job_registry, orchestrator, qa
from codemind.qa import QaAnswer
from config import settings

client = TestClient(app, raise_server_exceptions=False)


def _token(username: str = "codemind_test_user", role: str = "user") -> str:
    payload = {"sub": username, "role": role, "exp": time.time() + 3600}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_token()}"}


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "JOBS_DIR", str(tmp_path / "jobs"))
    # start_job's background task runs a real orchestrator.run(), which
    # persists the incremental-run manifest under
    # orchestrator.DEFAULT_OUTPUT_DIRECTORY ("./output" relative to the
    # process cwd by default) independently of the job's own output
    # directory -- redirect it so that never leaks into the real repo.
    monkeypatch.setattr(orchestrator, "DEFAULT_OUTPUT_DIRECTORY", tmp_path / "default-output")
    job_registry._jobs.clear()
    yield
    job_registry._jobs.clear()


def test_start_job_returns_accepted_with_job_id(tmp_path):
    # Explicit outputDirectory so this test never touches
    # codemind.orchestrator.DEFAULT_OUTPUT_DIRECTORY's real "./output"
    # (relative to the backend process's cwd) as a side effect.
    resp = client.post(
        "/api/v1/extraction-jobs",
        json={"repositoryPath": str(tmp_path), "outputDirectory": str(tmp_path / "out")},
        headers=_auth_headers(),
    )

    assert resp.status_code == 202
    body = resp.json()
    assert uuid.UUID(body["jobId"])
    assert body["repositoryRoot"] == str(tmp_path.resolve())


def test_start_job_rejects_non_directory_path(tmp_path):
    resp = client.post(
        "/api/v1/extraction-jobs",
        json={"repositoryPath": str(tmp_path / "does-not-exist")},
        headers=_auth_headers(),
    )

    assert resp.status_code == 400
    assert "not a directory" in resp.json()["detail"]


def test_start_job_rejects_invalid_execution_mode(tmp_path):
    resp = client.post(
        "/api/v1/extraction-jobs",
        json={"repositoryPath": str(tmp_path), "executionMode": "BOGUS"},
        headers=_auth_headers(),
    )

    assert resp.status_code == 400
    assert "executionMode" in resp.json()["detail"]


def test_get_job_returns_not_found_for_unknown_id():
    resp = client.get(f"/api/v1/extraction-jobs/{uuid.uuid4()}", headers=_auth_headers())

    assert resp.status_code == 404


def test_list_jobs_returns_all_registered_jobs(tmp_path):
    job = job_registry.register(tmp_path, None, None, None)

    resp = client.get("/api/v1/extraction-jobs", headers=_auth_headers())

    assert resp.status_code == 200
    assert resp.json()[0]["jobId"] == str(job.id)


def test_list_output_files_returns_snapshot_for_known_job(tmp_path):
    job = job_registry.register(tmp_path, tmp_path / "out", None, None)
    (job.output_directory).mkdir(parents=True)
    (job.output_directory / "a.js.json").write_text("{}")

    resp = client.get(f"/api/v1/extraction-jobs/{job.id}/output-files", headers=_auth_headers())

    assert resp.status_code == 200
    assert resp.json()[0]["relativePath"] == "a.js.json"


def test_list_output_files_returns_not_found_for_unknown_job():
    resp = client.get(f"/api/v1/extraction-jobs/{uuid.uuid4()}/output-files", headers=_auth_headers())

    assert resp.status_code == 404


def test_ask_returns_answer_with_source_files_for_known_job(tmp_path, monkeypatch):
    job = job_registry.register(tmp_path, tmp_path / "out", None, None)

    async def fake_ask(output_directory, question):
        assert output_directory == job.output_directory
        assert question == "what does auth.js do?"
        return QaAnswer("It authenticates users.", ["auth.js"])

    monkeypatch.setattr(qa, "ask", fake_ask)

    resp = client.post(
        f"/api/v1/extraction-jobs/{job.id}/qa",
        json={"question": "what does auth.js do?"},
        headers=_auth_headers(),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "It authenticates users."
    assert body["sourceFiles"] == ["auth.js"]


def test_ask_returns_not_found_for_unknown_job():
    resp = client.post(
        f"/api/v1/extraction-jobs/{uuid.uuid4()}/qa",
        json={"question": "anything?"},
        headers=_auth_headers(),
    )

    assert resp.status_code == 404


def test_ask_rejects_blank_question(tmp_path):
    job = job_registry.register(tmp_path, tmp_path / "out", None, None)

    resp = client.post(
        f"/api/v1/extraction-jobs/{job.id}/qa",
        json={"question": ""},
        headers=_auth_headers(),
    )

    assert resp.status_code >= 400
    assert resp.status_code < 500
