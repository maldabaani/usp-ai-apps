"""In-memory registry of extraction jobs, backed by codemind/job_store.py's
file-per-job persistence.

Ported from com.jslogicextractor.orchestration.JobRegistry. Unlike Java's
@Component/@PostConstruct lifecycle, load_persisted_jobs() is called
explicitly from api/main.py's lifespan() at startup (see that module).
Non-terminal jobs found on disk get marked FAILED with "Interrupted at
server restart" rather than silently resumed -- a job's in-flight asyncio
fan-out doesn't survive a process restart any more than Java's in-flight
thread pool did.
"""
from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path
from typing import Optional

from codemind import job_store
from codemind import orchestrator
from codemind.orchestrator import ExecutionMode, ExtractionJob, JobPhase
from config import settings

logger = logging.getLogger(__name__)

_TERMINAL_PHASES = {JobPhase.COMPLETED, JobPhase.FAILED, JobPhase.CANCELLED}

_jobs: dict[uuid.UUID, ExtractionJob] = {}


def load_persisted_jobs() -> None:
    snapshots = job_store.load_all()
    logger.info("Loaded %d persisted job(s) from store", len(snapshots))
    for snapshot in snapshots:
        job = _restore_from_snapshot(snapshot)
        _jobs[job.id] = job


def _restore_from_snapshot(snapshot: dict) -> ExtractionJob:
    phase = JobPhase(snapshot["phase"])
    terminal = phase in _TERMINAL_PHASES
    restored_phase = phase if terminal else JobPhase.FAILED
    restored_reason = snapshot["failure_reason"] if terminal else "Interrupted at server restart"
    original_finished_at = _parse_datetime(snapshot["finished_at"])
    restored_finished_at = (
        _utc_now() if (not terminal and original_finished_at is None) else original_finished_at
    )

    job = ExtractionJob(
        id=uuid.UUID(snapshot["id"]),
        repository_root=Path(snapshot["repository_root"]),
        output_directory=Path(snapshot["output_directory"]),
        max_concurrency=snapshot["max_concurrency"],
        execution_mode=ExecutionMode(snapshot["execution_mode"]),
        incremental=snapshot["incremental"],
    )
    job.created_at = _parse_datetime(snapshot["created_at"])
    job.phase = restored_phase
    job.failure_reason = restored_reason
    job.finished_at = restored_finished_at
    job.total_files = snapshot["total_files"]
    job.processed_files = snapshot["processed_files"]
    job.succeeded_files = snapshot["succeeded_files"]
    job.failed_files = snapshot["failed_files"]
    job.skipped_files = snapshot["skipped_files"]
    return job


def _parse_datetime(value: Optional[str]):
    from datetime import datetime

    return datetime.fromisoformat(value) if value else None


def _utc_now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def register(
    repository_root: Path,
    output_directory_override: Optional[Path] = None,
    max_concurrency_override: Optional[int] = None,
    execution_mode_override: Optional[ExecutionMode] = None,
    incremental: bool = False,
) -> ExtractionJob:
    job_id = uuid.uuid4()
    output_directory = (
        output_directory_override
        if output_directory_override is not None
        else orchestrator.DEFAULT_OUTPUT_DIRECTORY / str(job_id)
    )
    max_concurrency = (
        max_concurrency_override if max_concurrency_override is not None else orchestrator.DEFAULT_MAX_CONCURRENT_REQUESTS
    )
    # Reads the *live* default (settable via the /settings screen without a
    # restart), not a value frozen at startup.
    execution_mode = (
        execution_mode_override
        if execution_mode_override is not None
        else ExecutionMode(settings.CODEMIND_EXECUTION_MODE)
    )

    job = ExtractionJob(
        id=job_id,
        repository_root=repository_root,
        output_directory=output_directory,
        max_concurrency=max_concurrency,
        execution_mode=execution_mode,
        incremental=incremental,
    )
    _jobs[job_id] = job
    job_store.save(job.snapshot())
    return job


def persist(job: ExtractionJob) -> None:
    job_store.save(job.snapshot())


def find(job_id: uuid.UUID) -> Optional[ExtractionJob]:
    return _jobs.get(job_id)


def find_all() -> list[ExtractionJob]:
    return sorted(_jobs.values(), key=lambda job: job.created_at, reverse=True)


def delete(job_id: uuid.UUID) -> None:
    job = _jobs.pop(job_id, None)
    if job is not None:
        _delete_directory(job.output_directory)
    job_store.delete(job_id)
    logger.info("Deleted job %s", job_id)


def clear_all() -> None:
    for job in _jobs.values():
        _delete_directory(job.output_directory)
    _delete_directory(orchestrator.DEFAULT_OUTPUT_DIRECTORY.absolute() / ".manifests")
    job_store.delete_all()
    _jobs.clear()
    logger.info("All job data cleared")


def _delete_directory(directory: Path) -> None:
    shutil.rmtree(directory, ignore_errors=True)
