"""Covers ingestion/runner_jobs.py's ingestion-generation bump (Phase L-F):
a successful run bumps ingestion/ingestion_generation.py's counter exactly
once (invalidating api/ask_cache.py's cached answers going forward), a
failed run does not bump it at all (the corpus didn't actually change).

Also covers two accuracy fixes (Phase O): history status must reflect the
job's real computed status (not a hardcoded "done" that used to mask runs
with genuine per-file errors), and partial file results already recorded via
update_result() must survive a top-level exception instead of being wiped to
None.
"""
from __future__ import annotations

import asyncio

import pytest

from api import ingest_jobs
from config import settings
from ingestion import ingest_job_registry, ingestion_generation, runner_jobs


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    ingestion_generation._generation = 0
    monkeypatch.setattr(settings, "JOBS_DIR", str(tmp_path / "jobs"))
    ingest_jobs._jobs.clear()
    ingest_job_registry._entries = None
    yield
    ingestion_generation._generation = 0
    ingest_jobs._jobs.clear()
    ingest_job_registry._entries = None


def test_successful_document_ingestion_bumps_generation_exactly_once(monkeypatch, tmp_path):
    async def fake_ingest_documents(folder_path, progress_callback=None, enable_llm_summary=None, max_concurrency=None):
        return {"errors": []}

    monkeypatch.setattr(runner_jobs, "ingest_documents", fake_ingest_documents)
    ingest_jobs.register_job("job-1", kind="documents")

    asyncio.run(runner_jobs.run_document_ingestion("job-1", str(tmp_path)))

    assert ingestion_generation.current() == 1


def test_failed_document_ingestion_does_not_bump_generation(monkeypatch, tmp_path):
    async def fake_ingest_documents(folder_path, progress_callback=None, enable_llm_summary=None, max_concurrency=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(runner_jobs, "ingest_documents", fake_ingest_documents)
    ingest_jobs.register_job("job-2", kind="documents")

    asyncio.run(runner_jobs.run_document_ingestion("job-2", str(tmp_path)))

    assert ingestion_generation.current() == 0


def test_successful_code_ingestion_bumps_generation_exactly_once(monkeypatch, tmp_path):
    async def fake_ingest_code(repo_path, progress_callback=None, enable_llm_summary=None, max_concurrency=8, force_full_rechunk=False):
        return {"errors": []}

    monkeypatch.setattr(runner_jobs, "ingest_code", fake_ingest_code)
    ingest_jobs.register_job("job-3", kind="code")

    asyncio.run(runner_jobs.run_code_ingestion("job-3", str(tmp_path), None, 8))

    assert ingestion_generation.current() == 1


def test_failed_code_ingestion_does_not_bump_generation(monkeypatch, tmp_path):
    async def fake_ingest_code(repo_path, progress_callback=None, enable_llm_summary=None, max_concurrency=8, force_full_rechunk=False):
        raise RuntimeError("boom")

    monkeypatch.setattr(runner_jobs, "ingest_code", fake_ingest_code)
    ingest_jobs.register_job("job-4", kind="code")

    asyncio.run(runner_jobs.run_code_ingestion("job-4", str(tmp_path), None, 8))

    assert ingestion_generation.current() == 0


def test_history_status_reflects_real_per_file_errors_not_hardcoded_done(monkeypatch, tmp_path):
    """Regression test: record_completed_job used to be called with a
    literal "done" string on the success path regardless of whether the
    result actually contained per-file errors -- so a run with real failures
    was permanently mislabeled "done" in history."""

    async def fake_ingest_code(repo_path, progress_callback=None, enable_llm_summary=None, max_concurrency=8, force_full_rechunk=False):
        return {"errors": ["bad_file.py: syntax error"], "files": [{"path": "bad_file.py", "status": "error"}]}

    monkeypatch.setattr(runner_jobs, "ingest_code", fake_ingest_code)
    ingest_jobs.register_job("job-5", kind="code", source_path="/tmp/some-repo")

    asyncio.run(runner_jobs.run_code_ingestion("job-5", "/tmp/some-repo", None, 8))

    entry = ingest_job_registry.list_history()[0]
    assert entry["status"] == "error"
    assert entry["result"]["files"] == [{"path": "bad_file.py", "status": "error"}]
    assert entry["source_path"] == "/tmp/some-repo"


def test_partial_file_records_survive_top_level_exception(monkeypatch, tmp_path):
    """Regression test: a top-level exception (e.g. an embedding backend
    failure inside flush()) used to discard every file processed before the
    crash by recording result=None -- this asserts whatever update_result()
    already accumulated via progress_callback survives into history instead.
    """

    async def fake_ingest_code(repo_path, progress_callback=None, enable_llm_summary=None, max_concurrency=8, force_full_rechunk=False):
        await progress_callback(1, 3, phase="chunking", partial_result={"files": [{"path": "a.py", "status": "success"}]})
        await progress_callback(
            2,
            3,
            phase="chunking",
            partial_result={"files": [{"path": "a.py", "status": "success"}, {"path": "b.py", "status": "success"}]},
        )
        raise RuntimeError("Ollama connection refused")

    monkeypatch.setattr(runner_jobs, "ingest_code", fake_ingest_code)
    ingest_jobs.register_job("job-6", kind="code", source_path="/tmp/some-repo")

    asyncio.run(runner_jobs.run_code_ingestion("job-6", "/tmp/some-repo", None, 8))

    entry = ingest_job_registry.list_history()[0]
    assert entry["status"] == "error"
    assert entry["result"] is not None
    assert entry["result"]["files"] == [{"path": "a.py", "status": "success"}, {"path": "b.py", "status": "success"}]
    assert entry["errors"] == ["Ollama connection refused"]
    assert entry["source_path"] == "/tmp/some-repo"
