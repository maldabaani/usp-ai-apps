"""Scans a repository (or a single dropped file), fans work out across the
registered extraction agents, and tracks per-job progress.

Ported from com.jslogicextractor.orchestration.{ExtractionJob,JobPhase,
JsRepositoryProcessingOrchestrator}. The concurrency model intentionally
changes here: Java's Executors.newFixedThreadPool(job.maxConcurrency())
becomes asyncio.Semaphore(job.max_concurrency) gating real async LLM calls
(agent.extract() is already async, see codemind/agents/*.py) -- this is the
actual performance fix requested (max_concurrency becomes a real, tunable
knob against Ollama's OLLAMA_NUM_PARALLEL, at zero extra OS-thread cost)
rather than a blind thread-pool default. Plain int counters on ExtractionJob
are safe without locks: asyncio is single-threaded/cooperative, so only one
coroutine ever runs between await points, unlike Java's genuinely-parallel
threads which needed AtomicInteger.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Awaitable, Callable

from codemind import filter as file_filter
from codemind import manifest, output, scanner
from codemind.agents.base import LogicExtractionAgent, skipped_result
from codemind.models import SourceFile

logger = logging.getLogger(__name__)

_PREFILTER_AGENT_NAME = "non-substantive-pre-filter"

# Mirrors com.jslogicextractor.config.ExtractionProperties' compact-constructor
# defaults exactly -- these were never env-var-overridable in the Java
# original either (unlike chunking.enabled/max-lines-per-chunk below, which
# were), so they're plain constants here rather than Settings/SettingsResponse
# fields (not settings-screen-editable in either app).
INCLUDED_EXTENSIONS = {
    ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",
    ".py", ".pyw", ".java", ".kt", ".kts",
    ".go", ".cs", ".rb", ".rs", ".php",
}
EXCLUDED_DIRECTORY_NAMES = {
    "node_modules", ".git", "dist", "build", "coverage",
    "out", ".next", ".turbo", "vendor",
    "__pycache__", "target", ".venv", "venv",
    "bin", "obj", ".gradle", ".mypy_cache", ".pytest_cache",
    # Local dev-server build caches -- Angular CLI's .angular/cache holds
    # Vite's pre-bundled copies of third-party deps (confirmed live: a
    # bundled rxjs.js and an anonymous vendor chunk got scanned and
    # "extracted" as if they were application code, accounting for ~40% of
    # one job's reported rule count). .cache is the same category of thing
    # for several other JS toolchains (webpack, parcel, babel).
    ".angular", ".cache",
}
MAX_FILE_SIZE_BYTES = 300_000
DEFAULT_MAX_CONCURRENT_REQUESTS = 8
SKIP_EXISTING_RESULTS = True
CHUNKING_ENABLED = os.getenv("JSPROCESSOR_CHUNKING_ENABLED", "true").lower() == "true"
MAX_LINES_PER_CHUNK = int(os.getenv("JSPROCESSOR_CHUNKING_MAX_LINES", "400"))
DEFAULT_OUTPUT_DIRECTORY = Path(os.getenv("CODEMIND_DEFAULT_OUTPUT_DIRECTORY", "./output"))


class JobPhase(str, Enum):
    PENDING = "PENDING"
    SCANNING = "SCANNING"
    FILTERING = "FILTERING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class ExecutionMode(str, Enum):
    SYNC = "SYNC"
    BATCH = "BATCH"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ExtractionJob:
    id: uuid.UUID
    repository_root: Path
    output_directory: Path
    max_concurrency: int
    execution_mode: ExecutionMode = ExecutionMode.SYNC
    incremental: bool = False
    created_at: datetime = field(default_factory=_now)
    phase: JobPhase = JobPhase.PENDING
    finished_at: datetime | None = None
    failure_reason: str | None = None
    cancel_requested: bool = False
    total_files: int = 0
    processed_files: int = 0
    succeeded_files: int = 0
    failed_files: int = 0
    skipped_files: int = 0
    # Tracks in-flight per-file tasks so request_cancel() can interrupt a call
    # already mid-flight (e.g. stuck in a slow ChatOllama.ainvoke()), not just
    # stop files that haven't started yet -- without this, "Stop Job" only
    # took effect once every currently-running file finished on its own,
    # which could be many minutes on a slow chunk.
    _active_tasks: set = field(default_factory=set, repr=False, compare=False)

    def mark_scanning(self) -> None:
        self.phase = JobPhase.SCANNING

    def mark_filtering(self, total: int) -> None:
        self.total_files = total
        self.phase = JobPhase.FILTERING

    def mark_processing(self) -> None:
        self.phase = JobPhase.PROCESSING

    def mark_completed(self) -> None:
        self.phase = JobPhase.COMPLETED
        self.finished_at = _now()

    def mark_cancelled(self) -> None:
        self.phase = JobPhase.CANCELLED
        self.finished_at = _now()

    def mark_failed(self, reason: str) -> None:
        self.phase = JobPhase.FAILED
        self.failure_reason = reason
        self.finished_at = _now()

    def request_cancel(self) -> None:
        self.cancel_requested = True
        for task in list(self._active_tasks):
            task.cancel()

    def record_result(self, success: bool) -> None:
        self.processed_files += 1
        if success:
            self.succeeded_files += 1
        else:
            self.failed_files += 1

    def record_skipped(self) -> None:
        self.processed_files += 1
        self.skipped_files += 1

    def snapshot(self) -> dict:
        return {
            "id": str(self.id),
            "repository_root": str(self.repository_root),
            "output_directory": str(self.output_directory),
            "max_concurrency": self.max_concurrency,
            "execution_mode": self.execution_mode.value,
            "incremental": self.incremental,
            "created_at": self.created_at.isoformat(),
            "phase": self.phase.value,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "failure_reason": self.failure_reason,
            "total_files": self.total_files,
            "processed_files": self.processed_files,
            "succeeded_files": self.succeeded_files,
            "failed_files": self.failed_files,
            "skipped_files": self.skipped_files,
        }


BatchRunner = Callable[[ExtractionJob, list[SourceFile]], Awaitable[None]]


async def run(
    job: ExtractionJob,
    agent_selector,
    *,
    batch_runner: BatchRunner | None = None,
) -> None:
    job.mark_scanning()
    is_single_file = job.repository_root.is_file()
    try:
        if is_single_file:
            files = scanner.scan_file(
                job.repository_root,
                included_extensions=INCLUDED_EXTENSIONS,
                max_file_size_bytes=MAX_FILE_SIZE_BYTES,
                chunking_enabled=CHUNKING_ENABLED,
                max_lines_per_chunk=MAX_LINES_PER_CHUNK,
            )
        else:
            files = scanner.scan(
                job.repository_root,
                included_extensions=INCLUDED_EXTENSIONS,
                excluded_directory_names=EXCLUDED_DIRECTORY_NAMES,
                max_file_size_bytes=MAX_FILE_SIZE_BYTES,
                chunking_enabled=CHUNKING_ENABLED,
                max_lines_per_chunk=MAX_LINES_PER_CHUNK,
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("Repository scan failed for job %s: %s", job.id, exc)
        job.mark_failed(f"Repository scan failed: {exc}")
        output.write_summary(job.output_directory, _summary(job))
        return

    candidate_files = files
    if job.incremental and not is_single_file:
        candidate_files = _apply_incremental_filter(job, files)

    job.mark_filtering(len(candidate_files))
    logger.info(
        "Job %s: scanned %d files from %s, mode=%s, incremental=%s, maxConcurrency=%d, agents=%d",
        job.id, len(files), job.repository_root, job.execution_mode, job.incremental,
        job.max_concurrency, agent_selector.agent_count(),
    )

    eligible_files = _partition_eligible_files(job, candidate_files)

    if eligible_files:
        job.mark_processing()
        if job.execution_mode == ExecutionMode.BATCH:
            await _run_batch_mode(job, eligible_files, batch_runner)
        else:
            await _run_sync_fan_out(job, eligible_files, agent_selector)

    # Batch mode may have already moved the job to FAILED on a non-recoverable
    # error -- don't clobber that.
    if job.phase != JobPhase.FAILED:
        if job.cancel_requested:
            job.mark_cancelled()
        else:
            job.mark_completed()

    # Persist manifest only after a clean completion so future jobs can run incrementally.
    if job.phase == JobPhase.COMPLETED and not is_single_file:
        hashes = manifest.compute_hashes(job.repository_root, files)
        manifest.save(
            DEFAULT_OUTPUT_DIRECTORY, job.repository_root, manifest.Manifest(job.output_directory, hashes)
        )

    output.write_summary(job.output_directory, _summary(job))
    logger.info(
        "Job %s finished: %d succeeded, %d failed, %d skipped, of %d files",
        job.id, job.succeeded_files, job.failed_files, job.skipped_files, job.total_files,
    )


def _summary(job: ExtractionJob) -> dict:
    return {
        "jobId": str(job.id),
        "phase": job.phase.value,
        "repositoryRoot": str(job.repository_root),
        "totalFiles": job.total_files,
        "succeeded": job.succeeded_files,
        "failed": job.failed_files,
        "skipped": job.skipped_files,
        "createdAt": job.created_at.isoformat(),
        "finishedAt": job.finished_at.isoformat() if job.finished_at else None,
        "failureReason": job.failure_reason,
    }


def _apply_incremental_filter(job: ExtractionJob, files: list[SourceFile]) -> list[SourceFile]:
    """Loads the manifest for this repo, diffs current hashes against it,
    removes output files for deleted source files, and returns only the
    SourceFiles whose originals changed or were added."""
    loaded = manifest.load(DEFAULT_OUTPUT_DIRECTORY, job.repository_root)
    if loaded is None:
        # Manifest disappeared between job registration and now -- fall back to a full run.
        return files

    current_hashes = manifest.compute_hashes(job.repository_root, files)
    changes = manifest.diff(loaded.file_hashes, current_hashes)

    for deleted_rel_path in changes.deleted:
        _delete_output_files(job.output_directory, deleted_rel_path)

    logger.info(
        "Job %s (incremental): %d added, %d modified, %d deleted of %d total files",
        job.id, len(changes.added), len(changes.modified), len(changes.deleted), len(files),
    )

    changed_absolute_paths = {job.repository_root / rel for rel in changes.changed_or_added()}
    return [f for f in files if f.absolute_path in changed_absolute_paths]


def _delete_output_files(output_directory: Path, relative_source_path: str) -> None:
    direct_json = output_directory / f"{relative_source_path}.json"
    try:
        direct_json.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("Could not delete output file %s: %s", direct_json, e)
    # Chunked files produce a sub-directory: outputDir/<relPath>/part-NNNN.ext.json
    chunk_dir = output_directory / relative_source_path
    if chunk_dir.is_dir():
        shutil.rmtree(chunk_dir, ignore_errors=True)


def _partition_eligible_files(job: ExtractionJob, files: list[SourceFile]) -> list[SourceFile]:
    """Applies the unconditional non-substantive pre-filter and (for sync
    mode's resumable re-run support) the existing-result skip, regardless of
    which execution mode handles the remainder."""
    eligible: list[SourceFile] = []
    for file in files:
        reason = file_filter.skip_reason(file)
        if reason is not None:
            result = skipped_result(file, _PREFILTER_AGENT_NAME, reason)
            output.write_result(job.output_directory, file.relative_path, result.to_dict())
            job.record_skipped()
            continue
        if SKIP_EXISTING_RESULTS and output.result_exists(job.output_directory, file.relative_path):
            job.record_result(True)
            continue
        eligible.append(file)
    return eligible


async def _run_sync_fan_out(job: ExtractionJob, files: list[SourceFile], agent_selector) -> None:
    semaphore = asyncio.Semaphore(job.max_concurrency)

    async def bounded_process(file: SourceFile) -> None:
        async with semaphore:
            await _process_file(job, file, agent_selector)

    # Tasks (not bare coroutines) so request_cancel() has something to call
    # .cancel() on -- registered on the job for the duration of this gather so
    # a cancel requested mid-run reaches every currently-running file's
    # in-flight LLM call immediately instead of waiting for it to finish on
    # its own. return_exceptions=True keeps one cancelled/crashed task from
    # aborting gather() itself before the rest have a chance to wind down.
    tasks = [asyncio.ensure_future(bounded_process(file)) for file in files]
    job._active_tasks.update(tasks)
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        job._active_tasks.difference_update(tasks)


async def _process_file(job: ExtractionJob, file: SourceFile, agent_selector) -> None:
    if job.cancel_requested:
        return
    try:
        agent: LogicExtractionAgent = agent_selector.next()
        result = await agent.extract(file)
        output.write_result(job.output_directory, result.relative_path, result.to_dict())
        job.record_result(result.success)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - one file's crash must not sink the whole job
        logger.error("Unexpected error processing %s: %s", file.relative_path, exc)
        job.record_result(False)


async def _run_batch_mode(
    job: ExtractionJob, files: list[SourceFile], batch_runner: BatchRunner | None
) -> None:
    try:
        if batch_runner is not None:
            await batch_runner(job, files)
        else:
            from codemind.batch import run_batch

            await run_batch(job, files)
    except Exception as exc:  # noqa: BLE001
        logger.error("Job %s: batch execution failed: %s", job.id, exc)
        job.mark_failed(f"Batch execution failed: {exc}")
