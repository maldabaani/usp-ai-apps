"""Covers codemind/job_registry.py, ported from
com.jslogicextractor.orchestration.JobRegistry (no dedicated Java test class
exists for this one -- these cases are newly written, not ported from an
existing JUnit suite, covering the module's own documented contract: restart
recovery marks non-terminal jobs FAILED, register()/find()/delete()/
clear_all() round-trip through job_store.py's per-file persistence).
"""
import uuid
from pathlib import Path

import pytest

from codemind import job_registry, job_store, orchestrator
from codemind.orchestrator import ExecutionMode, ExtractionJob, JobPhase
from config import settings


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "JOBS_DIR", str(tmp_path))
    # clear_all() also rmtree's DEFAULT_OUTPUT_DIRECTORY/.manifests ("./output"
    # relative to the process cwd by default) -- redirect it so that test
    # never deletes a real ./output/.manifests directory outside tmp_path.
    monkeypatch.setattr(orchestrator, "DEFAULT_OUTPUT_DIRECTORY", tmp_path / "default-output")
    job_registry._jobs.clear()
    yield
    job_registry._jobs.clear()


def test_register_persists_and_is_findable(tmp_path):
    job = job_registry.register(tmp_path / "repo", None, None, None)

    assert job_registry.find(job.id) is job
    assert (tmp_path / "codemind_jobs" / f"{job.id}.json").exists()


def test_register_uses_live_execution_mode_default_when_not_overridden(tmp_path):
    original = settings.CODEMIND_EXECUTION_MODE
    try:
        settings.apply_updates({"CODEMIND_EXECUTION_MODE": "BATCH"})
        job = job_registry.register(tmp_path / "repo", None, None, None)
        assert job.execution_mode == ExecutionMode.BATCH
    finally:
        settings.apply_updates({"CODEMIND_EXECUTION_MODE": original})


def test_find_all_sorted_newest_first(tmp_path):
    first = job_registry.register(tmp_path / "repo1", None, None, None)
    second = job_registry.register(tmp_path / "repo2", None, None, None)
    second.created_at = first.created_at.replace(year=first.created_at.year + 1)

    all_jobs = job_registry.find_all()

    assert all_jobs[0] is second
    assert all_jobs[1] is first


def test_delete_removes_from_registry_and_store(tmp_path):
    job = job_registry.register(tmp_path / "repo", None, None, None)

    job_registry.delete(job.id)

    assert job_registry.find(job.id) is None
    assert not (tmp_path / "codemind_jobs" / f"{job.id}.json").exists()


def test_clear_all_empties_registry(tmp_path):
    job_registry.register(tmp_path / "repo1", None, None, None)
    job_registry.register(tmp_path / "repo2", None, None, None)

    job_registry.clear_all()

    assert job_registry.find_all() == []


def test_load_persisted_jobs_marks_non_terminal_jobs_interrupted(tmp_path):
    job_id = uuid.uuid4()
    job_store.save(
        {
            "id": str(job_id),
            "repository_root": str(tmp_path / "repo"),
            "output_directory": str(tmp_path / "out"),
            "max_concurrency": 4,
            "execution_mode": "SYNC",
            "incremental": False,
            "created_at": "2024-01-01T00:00:00+00:00",
            "phase": "PROCESSING",
            "finished_at": None,
            "failure_reason": None,
            "total_files": 10,
            "processed_files": 3,
            "succeeded_files": 3,
            "failed_files": 0,
            "skipped_files": 0,
        }
    )

    job_registry.load_persisted_jobs()

    restored = job_registry.find(job_id)
    assert restored.phase == JobPhase.FAILED
    assert restored.failure_reason == "Interrupted at server restart"
    assert restored.finished_at is not None


def test_load_persisted_jobs_preserves_terminal_jobs_unchanged(tmp_path):
    job_id = uuid.uuid4()
    job_store.save(
        {
            "id": str(job_id),
            "repository_root": str(tmp_path / "repo"),
            "output_directory": str(tmp_path / "out"),
            "max_concurrency": 4,
            "execution_mode": "SYNC",
            "incremental": False,
            "created_at": "2024-01-01T00:00:00+00:00",
            "phase": "COMPLETED",
            "finished_at": "2024-01-01T00:05:00+00:00",
            "failure_reason": None,
            "total_files": 2,
            "processed_files": 2,
            "succeeded_files": 2,
            "failed_files": 0,
            "skipped_files": 0,
        }
    )

    job_registry.load_persisted_jobs()

    restored = job_registry.find(job_id)
    assert restored.phase == JobPhase.COMPLETED
    assert restored.failure_reason is None
