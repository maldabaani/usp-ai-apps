"""Covers codemind/orchestrator.py, ported from
com.jslogicextractor.orchestration.JsRepositoryProcessingOrchestrator.

Concurrency assertions adapt to the asyncio.Semaphore fan-out (Java's
Executors.newFixedThreadPool + real OS threads becomes cooperative
coroutines) -- the assertion that matters is preserved unchanged: no more
than max_concurrency extractions are ever in flight at once.
"""
import asyncio
import uuid
from pathlib import Path
from typing import Optional

import pytest

from codemind import manifest, orchestrator
from codemind.agents.base import ExtractionResult, failure_result, success_result
from codemind.agents.selector import AgentSelector
from codemind.models import SourceFile
from codemind.orchestrator import ExecutionMode, ExtractionJob, JobPhase


@pytest.fixture(autouse=True)
def _isolate_default_output_directory(tmp_path, monkeypatch):
    """A COMPLETED directory job writes its incremental-run manifest under
    orchestrator.DEFAULT_OUTPUT_DIRECTORY ("./output" relative to the process
    cwd by default, matching Java's own default) -- redirect it into tmp_path
    so these tests never leak a real ./output/.manifests directory into the
    repo."""
    monkeypatch.setattr(orchestrator, "DEFAULT_OUTPUT_DIRECTORY", tmp_path / "default-output")


class _StubAgent:
    def __init__(self, extract_fn) -> None:
        self._extract_fn = extract_fn

    def name(self) -> str:
        return "test-agent"

    async def extract(self, file: SourceFile) -> ExtractionResult:
        return await self._extract_fn(file)


def _job(repo_root: Path, max_concurrency: int = 4, execution_mode: ExecutionMode = ExecutionMode.SYNC,
          incremental: bool = False) -> ExtractionJob:
    return ExtractionJob(
        id=uuid.uuid4(),
        repository_root=repo_root,
        output_directory=repo_root / "out",
        max_concurrency=max_concurrency,
        execution_mode=execution_mode,
        incremental=incremental,
    )


def test_processes_all_files_with_bounded_concurrency_and_isolates_failures(tmp_path, monkeypatch):
    (tmp_path / "a.js").write_text("const a = 1;")
    (tmp_path / "b.js").write_text("const b = 2;")
    (tmp_path / "c.js").write_text("const c = 3;")
    monkeypatch.setattr(orchestrator, "SKIP_EXISTING_RESULTS", False)

    active_calls = 0
    max_observed_concurrency = 0
    concurrency_limit = 2
    lock = asyncio.Lock()

    async def extract_fn(file: SourceFile) -> ExtractionResult:
        nonlocal active_calls, max_observed_concurrency
        async with lock:
            active_calls += 1
            max_observed_concurrency = max(max_observed_concurrency, active_calls)
        await asyncio.sleep(0.05)
        async with lock:
            active_calls -= 1
        if file.relative_path == "b.js":
            return failure_result(file, "test-agent", "boom", 1)
        return success_result(file, "test-agent", "{}", 1, None, None)

    selector = AgentSelector([_StubAgent(extract_fn)])
    job = _job(tmp_path, max_concurrency=concurrency_limit)

    asyncio.run(orchestrator.run(job, selector))

    assert job.phase == JobPhase.COMPLETED
    assert job.total_files == 3
    assert job.succeeded_files == 2
    assert job.failed_files == 1
    assert max_observed_concurrency <= concurrency_limit


def test_skips_files_with_existing_results_when_enabled(tmp_path):
    (tmp_path / "a.js").write_text("const a = 1;")
    (tmp_path / "b.js").write_text("const b = 2;")

    call_count = 0

    async def extract_fn(file: SourceFile) -> ExtractionResult:
        nonlocal call_count
        call_count += 1
        return success_result(file, "test-agent", "{}", 1, None, None)

    selector = AgentSelector([_StubAgent(extract_fn)])
    job = _job(tmp_path)
    # Pre-seed an existing result for a.js so it's skipped (matches
    # SKIP_EXISTING_RESULTS' default of True).
    (job.output_directory).mkdir(parents=True)
    (job.output_directory / "a.js.json").write_text("{}")

    asyncio.run(orchestrator.run(job, selector))

    assert call_count == 1
    assert job.succeeded_files == 2


def test_delegates_to_batch_runner_when_execution_mode_is_batch(tmp_path):
    (tmp_path / "a.js").write_text("const a = 1;")
    (tmp_path / "b.js").write_text("const b = 2;")

    async def unused_extract(file: SourceFile) -> ExtractionResult:
        raise AssertionError("BATCH mode must not invoke the SYNC agent path")

    selector = AgentSelector([_StubAgent(unused_extract)])

    batch_calls = []

    async def fake_batch_runner(job: ExtractionJob, files: list[SourceFile]) -> None:
        batch_calls.append((job, files))

    job = _job(tmp_path, execution_mode=ExecutionMode.BATCH)

    asyncio.run(orchestrator.run(job, selector, batch_runner=fake_batch_runner))

    assert len(batch_calls) == 1
    assert batch_calls[0][0] is job
    assert job.phase == JobPhase.COMPLETED


def test_marks_job_failed_when_batch_runner_throws(tmp_path):
    (tmp_path / "a.js").write_text("const a = 1;")

    async def unused_extract(file: SourceFile) -> ExtractionResult:
        raise AssertionError("BATCH mode must not invoke the SYNC agent path")

    selector = AgentSelector([_StubAgent(unused_extract)])

    async def failing_batch_runner(job: ExtractionJob, files: list[SourceFile]) -> None:
        raise RuntimeError("boom")

    job = _job(tmp_path, execution_mode=ExecutionMode.BATCH)

    asyncio.run(orchestrator.run(job, selector, batch_runner=failing_batch_runner))

    assert job.phase == JobPhase.FAILED
    assert "boom" in job.failure_reason


def test_processes_a_single_dropped_file_when_job_root_is_a_file_not_a_directory(tmp_path):
    file = tmp_path / "dropped.js"
    file.write_text("const a = 1;")

    written = []

    async def extract_fn(source_file: SourceFile) -> ExtractionResult:
        written.append(source_file.relative_path)
        return success_result(source_file, "test-agent", "{}", 1, None, None)

    selector = AgentSelector([_StubAgent(extract_fn)])
    job = _job(file)
    job.output_directory = tmp_path / "out"

    asyncio.run(orchestrator.run(job, selector))

    assert job.phase == JobPhase.COMPLETED
    assert job.total_files == 1
    assert job.succeeded_files == 1
    assert written == ["dropped.js"]


def test_request_cancel_interrupts_an_in_flight_extraction_immediately(tmp_path, monkeypatch):
    """Without task-level cancellation, request_cancel() only stops files that
    haven't started yet -- a file already mid-extract() (e.g. a slow
    ChatOllama.ainvoke()) would keep running for however long it takes,
    leaving "Stop Job" with no visible effect until it finished on its own."""
    (tmp_path / "a.js").write_text("const a = 1;")
    monkeypatch.setattr(orchestrator, "SKIP_EXISTING_RESULTS", False)

    started = asyncio.Event()

    async def extract_fn(file: SourceFile) -> ExtractionResult:
        started.set()
        await asyncio.sleep(30)  # would time out the test below if not interrupted
        return success_result(file, "test-agent", "{}", 1, None, None)

    selector = AgentSelector([_StubAgent(extract_fn)])
    job = _job(tmp_path, max_concurrency=1)

    async def body() -> None:
        run_task = asyncio.ensure_future(orchestrator.run(job, selector))
        await asyncio.wait_for(started.wait(), timeout=2)
        job.request_cancel()
        await asyncio.wait_for(run_task, timeout=2)

    asyncio.run(body())

    assert job.phase == JobPhase.CANCELLED


def test_incremental_job_only_processes_changed_files(tmp_path, monkeypatch):
    (tmp_path / "unchanged.js").write_text("const x = 1;")
    (tmp_path / "changed.js").write_text("const y = 2;")

    extracted_paths = []

    async def extract_fn(file: SourceFile) -> ExtractionResult:
        extracted_paths.append(file.relative_path)
        return success_result(file, "test-agent", "logic", 1, None, None)

    selector = AgentSelector([_StubAgent(extract_fn)])
    job = _job(tmp_path, incremental=True)

    previous_hashes = {
        "unchanged.js": "correct-hash-matches-real-file",
        "changed.js": "stale-hash-does-not-match",
    }

    monkeypatch.setattr(
        manifest, "load",
        lambda default_dir, repo_root: manifest.Manifest(job.output_directory, previous_hashes),
    )
    monkeypatch.setattr(
        manifest, "compute_hashes",
        lambda repo_root, files: {
            "unchanged.js": "correct-hash-matches-real-file",
            "changed.js": "new-hash-after-edit",
        },
    )
    monkeypatch.setattr(
        manifest, "diff",
        lambda previous, current: manifest.FileChanges(added=[], modified=["changed.js"], deleted=[]),
    )
    monkeypatch.setattr(orchestrator, "manifest", manifest)

    asyncio.run(orchestrator.run(job, selector))

    assert job.phase == JobPhase.COMPLETED
    assert job.total_files == 1  # only the changed file counted
    assert extracted_paths == ["changed.js"]
