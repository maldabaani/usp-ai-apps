"""Covers codemind/watch.py, ported from
com.jslogicextractor.watch.InputDirectoryWatcher. orchestrator.run() (via
watch.run_job) and the agent selector are monkeypatched so these tests never
scan a real repository or build a real LLM client -- only the debounced
file-system-event -> job-start wiring is under test, matching this suite's
mocked-JobStarter convention in the Java original.
"""
import asyncio
import time
from pathlib import Path

import pytest

from codemind import job_registry, watch
from config import settings


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setattr(watch, "get_agent_selector", lambda: None)
    job_registry._jobs.clear()
    yield
    job_registry._jobs.clear()


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[Path] = []
        self.event = asyncio.Event()

    async def fake_run_job(self, job, agent_selector) -> None:
        self.calls.append(job.repository_root)
        self.event.set()


def test_starts_a_job_for_each_file_dropped_into_the_watched_directory(tmp_path, monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(watch, "run_job", recorder.fake_run_job)

    async def body():
        watcher = watch.InputDirectoryWatcher(tmp_path, quiet_period_seconds=0.15)
        watcher.start()
        try:
            (tmp_path / "dropped.js").write_text("const a = 1;")
            await asyncio.wait_for(recorder.event.wait(), timeout=3)
        finally:
            watcher.stop()

        assert recorder.calls == [(tmp_path / "dropped.js").resolve()]

    asyncio.run(body())


def test_ignores_a_subdirectory_dropped_into_the_watched_directory(tmp_path, monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(watch, "run_job", recorder.fake_run_job)

    async def body():
        watcher = watch.InputDirectoryWatcher(tmp_path, quiet_period_seconds=0.1)
        watcher.start()
        try:
            (tmp_path / "dropped-folder").mkdir()
            # Wait out the full quiet period before asserting it never reacted.
            await asyncio.sleep(0.5)
        finally:
            watcher.stop()

        assert recorder.calls == []

    asyncio.run(body())


def test_coalesces_rapid_writes_to_the_same_file_into_a_single_job(tmp_path, monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(watch, "run_job", recorder.fake_run_job)

    async def body():
        watcher = watch.InputDirectoryWatcher(tmp_path, quiet_period_seconds=0.2)
        watcher.start()
        try:
            file = tmp_path / "slow-copy.js"
            file.write_text("const a = 1;")
            await asyncio.sleep(0.05)
            with file.open("a") as f:
                f.write("\nconst b = 2;")

            await asyncio.wait_for(recorder.event.wait(), timeout=3)
            await asyncio.sleep(0.5)
        finally:
            watcher.stop()

        assert recorder.calls == [file.resolve()]

    asyncio.run(body())
