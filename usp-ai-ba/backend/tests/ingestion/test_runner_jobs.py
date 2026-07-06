"""Covers ingestion/runner_jobs.py's ingestion-generation bump (Phase L-F):
a successful run bumps ingestion/ingestion_generation.py's counter exactly
once (invalidating api/ask_cache.py's cached answers going forward), a
failed run does not bump it at all (the corpus didn't actually change).
"""
from __future__ import annotations

import asyncio

import pytest

from api import ingest_jobs
from ingestion import ingestion_generation, runner_jobs


@pytest.fixture(autouse=True)
def _reset_generation():
    ingestion_generation._generation = 0
    yield
    ingestion_generation._generation = 0


def test_successful_document_ingestion_bumps_generation_exactly_once(monkeypatch, tmp_path):
    async def fake_ingest_documents(folder_path, progress_callback=None):
        return {"errors": []}

    monkeypatch.setattr(runner_jobs, "ingest_documents", fake_ingest_documents)
    ingest_jobs.register_job("job-1", kind="documents")

    asyncio.run(runner_jobs.run_document_ingestion("job-1", str(tmp_path)))

    assert ingestion_generation.current() == 1


def test_failed_document_ingestion_does_not_bump_generation(monkeypatch, tmp_path):
    async def fake_ingest_documents(folder_path, progress_callback=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(runner_jobs, "ingest_documents", fake_ingest_documents)
    ingest_jobs.register_job("job-2", kind="documents")

    asyncio.run(runner_jobs.run_document_ingestion("job-2", str(tmp_path)))

    assert ingestion_generation.current() == 0


def test_successful_code_ingestion_bumps_generation_exactly_once(monkeypatch, tmp_path):
    async def fake_ingest_code(repo_path, progress_callback=None, enable_llm_summary=None, max_concurrency=8):
        return {"errors": []}

    monkeypatch.setattr(runner_jobs, "ingest_code", fake_ingest_code)
    ingest_jobs.register_job("job-3", kind="code")

    asyncio.run(runner_jobs.run_code_ingestion("job-3", str(tmp_path), None, 8))

    assert ingestion_generation.current() == 1


def test_failed_code_ingestion_does_not_bump_generation(monkeypatch, tmp_path):
    async def fake_ingest_code(repo_path, progress_callback=None, enable_llm_summary=None, max_concurrency=8):
        raise RuntimeError("boom")

    monkeypatch.setattr(runner_jobs, "ingest_code", fake_ingest_code)
    ingest_jobs.register_job("job-4", kind="code")

    asyncio.run(runner_jobs.run_code_ingestion("job-4", str(tmp_path), None, 8))

    assert ingestion_generation.current() == 0
