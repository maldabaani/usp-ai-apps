"""Covers api/routers/codemind_ask.py, ported from
com.jslogicextractor.web.GlobalAskController (the cross-job "Ask All" SSE
route) and ExtractionJobController's per-job POST .../qa/stream route.
"""
import time
import uuid

import jwt
import pytest
from fastapi.testclient import TestClient

from api.main import app
from codemind import job_registry, qa
from codemind.orchestrator import JobPhase
from codemind.qa import QaStreamResult
from config import settings

client = TestClient(app, raise_server_exceptions=False)


def _token(username: str = "codemind_ask_test_user", role: str = "user") -> str:
    payload = {"sub": username, "role": role, "exp": time.time() + 3600}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_token()}"}


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "JOBS_DIR", str(tmp_path / "jobs"))
    job_registry._jobs.clear()
    yield
    job_registry._jobs.clear()


async def _chunks(*values):
    for value in values:
        yield value


def test_ask_stream_returns_sse_sources_and_chunks_for_known_job(tmp_path, monkeypatch):
    job = job_registry.register(tmp_path, tmp_path / "out", None, None)

    async def fake_ask_for_stream(output_directories, question, mode="deep"):
        assert output_directories == [job.output_directory]
        assert question == "what does auth.js do?"
        assert mode == "deep"
        return QaStreamResult(["auth.js"], _chunks("It ", "authenticates ", "users."))

    monkeypatch.setattr(qa, "ask_for_stream", fake_ask_for_stream)

    resp = client.post(
        f"/api/v1/extraction-jobs/{job.id}/qa/stream",
        json={"question": "what does auth.js do?"},
        headers=_auth_headers(),
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert 'event: sources\ndata: ["auth.js"]' in resp.text
    assert 'event: chunk\ndata: "It "' in resp.text
    assert 'event: chunk\ndata: "authenticates "' in resp.text
    assert 'event: chunk\ndata: "users."' in resp.text


def test_ask_stream_returns_not_found_for_unknown_job():
    resp = client.post(
        f"/api/v1/extraction-jobs/{uuid.uuid4()}/qa/stream",
        json={"question": "anything?"},
        headers=_auth_headers(),
    )

    assert resp.status_code == 404


def test_ask_stream_rejects_blank_question(tmp_path):
    job = job_registry.register(tmp_path, tmp_path / "out", None, None)

    resp = client.post(
        f"/api/v1/extraction-jobs/{job.id}/qa/stream",
        json={"question": ""},
        headers=_auth_headers(),
    )

    assert 400 <= resp.status_code < 500


def test_ask_all_stream_only_includes_completed_jobs(tmp_path, monkeypatch):
    completed_job = job_registry.register(tmp_path, tmp_path / "out1", None, None)
    completed_job.phase = JobPhase.COMPLETED
    pending_job = job_registry.register(tmp_path, tmp_path / "out2", None, None)
    pending_job.phase = JobPhase.PROCESSING

    async def fake_ask_for_stream(output_directories, question, mode="deep"):
        assert output_directories == [completed_job.output_directory]
        return QaStreamResult(["auth.js"], _chunks("answer"))

    monkeypatch.setattr(qa, "ask_for_stream", fake_ask_for_stream)

    resp = client.post(
        "/api/v1/ask/stream",
        json={"question": "anything?"},
        headers=_auth_headers(),
    )

    assert resp.status_code == 200
    assert 'event: chunk\ndata: "answer"' in resp.text


def test_ask_stream_passes_comprehensive_mode_through_to_qa(tmp_path, monkeypatch):
    job = job_registry.register(tmp_path, tmp_path / "out", None, None)

    async def fake_ask_for_stream(output_directories, question, mode="deep"):
        assert mode == "comprehensive"
        return QaStreamResult([], _chunks("Whole-codebase overview."))

    monkeypatch.setattr(qa, "ask_for_stream", fake_ask_for_stream)

    resp = client.post(
        f"/api/v1/extraction-jobs/{job.id}/qa/stream",
        json={"question": "explain this codebase", "mode": "comprehensive"},
        headers=_auth_headers(),
    )

    assert resp.status_code == 200
    assert "Whole-codebase overview." in resp.text


def test_ask_all_stream_ignores_mode_field_and_stays_deep(tmp_path, monkeypatch):
    completed_job = job_registry.register(tmp_path, tmp_path / "out1", None, None)
    completed_job.phase = JobPhase.COMPLETED

    async def fake_ask_for_stream(output_directories, question, mode="deep"):
        assert mode == "deep"  # Ask All never threads request.mode through
        return QaStreamResult(["auth.js"], _chunks("answer"))

    monkeypatch.setattr(qa, "ask_for_stream", fake_ask_for_stream)

    resp = client.post(
        "/api/v1/ask/stream",
        json={"question": "anything?", "mode": "comprehensive"},
        headers=_auth_headers(),
    )

    assert resp.status_code == 200
