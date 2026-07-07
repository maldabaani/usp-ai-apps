"""Covers ingestion/watcher.py's per-target debounced auto-re-ingestion
trigger, on_deleted handling, and the manual/watcher overlap guard
(Phase L-C). Fakes out runner_jobs.ingest_documents (a real call would try
to reach a live Chroma/Ollama embeddings endpoint), matching this
codebase's mocked-client testing convention.
"""
from __future__ import annotations

import asyncio

import pytest

from api import ingest_jobs
from config import settings
from ingestion import runner, runner_jobs, watch_registry, watcher as watcher_module
from ingestion.watcher import WatcherManager, _Handler


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "JOBS_DIR", str(tmp_path / "jobs"))
    ingest_jobs._jobs.clear()
    runner._active_tasks.clear()
    watcher_module._active_paths.clear()
    watch_registry._entries = None
    yield
    ingest_jobs._jobs.clear()
    runner._active_tasks.clear()
    watcher_module._active_paths.clear()
    watch_registry._entries = None


def test_burst_of_schedule_checks_triggers_exactly_one_run(tmp_path, monkeypatch):
    calls = []

    async def fake_ingest_documents(folder_path, progress_callback=None, enable_llm_summary=None, max_concurrency=None):
        calls.append(folder_path)
        return {"errors": []}

    monkeypatch.setattr(runner_jobs, "ingest_documents", fake_ingest_documents)
    monkeypatch.setattr(watcher_module, "QUIET_PERIOD_SECONDS", 0.05)

    watch_dir = tmp_path / "watched"
    watch_dir.mkdir()
    target = watch_registry.add_target(str(watch_dir), "documents")

    async def _body():
        manager = WatcherManager()
        await manager.start_all()
        try:
            manager._schedule_check(target["id"])  # e.g. on_created
            await asyncio.sleep(0.01)
            manager._schedule_check(target["id"])  # e.g. on_modified, same quiet period
            await asyncio.sleep(0.3)  # let the single debounced trigger fire + complete
        finally:
            manager.stop_all()

    asyncio.run(_body())

    assert calls == [str(watch_dir)]


def test_on_deleted_also_triggers_a_run(tmp_path, monkeypatch):
    calls = []

    async def fake_ingest_documents(folder_path, progress_callback=None, enable_llm_summary=None, max_concurrency=None):
        calls.append(folder_path)
        return {"errors": []}

    monkeypatch.setattr(runner_jobs, "ingest_documents", fake_ingest_documents)
    monkeypatch.setattr(watcher_module, "QUIET_PERIOD_SECONDS", 0.05)

    watch_dir = tmp_path / "watched"
    watch_dir.mkdir()
    target = watch_registry.add_target(str(watch_dir), "documents")

    async def _body():
        manager = WatcherManager()
        await manager.start_all()
        try:
            handler = _Handler(manager, target["id"])
            handler.on_deleted(None)
            await asyncio.sleep(0.3)
        finally:
            manager.stop_all()

    asyncio.run(_body())

    assert calls == [str(watch_dir)]


def test_overlap_guard_skips_a_second_trigger_while_a_run_is_active(tmp_path, monkeypatch):
    calls = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_ingest_documents(folder_path, progress_callback=None, enable_llm_summary=None, max_concurrency=None):
        calls.append(folder_path)
        started.set()
        await release.wait()
        return {"errors": []}

    monkeypatch.setattr(runner_jobs, "ingest_documents", fake_ingest_documents)

    watch_dir = tmp_path / "watched"
    watch_dir.mkdir()
    target = watch_registry.add_target(str(watch_dir), "documents")

    async def _body():
        manager = WatcherManager()
        await manager.start_all()
        try:
            await manager._trigger(target)  # first: starts the (blocked) run
            await asyncio.wait_for(started.wait(), timeout=2)
            await manager._trigger(target)  # second: must be skipped, first still active
            release.set()
            await asyncio.sleep(0.05)  # let the first run's task finish
        finally:
            manager.stop_all()

    asyncio.run(_body())

    assert calls == [str(watch_dir)]


def test_is_path_active_reflects_job_status(tmp_path):
    path = str(tmp_path)
    ingest_jobs.register_job("job-x", kind="documents")
    watcher_module.mark_path_active(path, "job-x")

    assert watcher_module.is_path_active(path) is True

    ingest_jobs.finish_job("job-x", {"errors": []})

    assert watcher_module.is_path_active(path) is False


def test_is_path_active_treats_unknown_job_as_inactive(tmp_path):
    path = str(tmp_path)
    watcher_module.mark_path_active(path, "does-not-exist")

    assert watcher_module.is_path_active(path) is False
