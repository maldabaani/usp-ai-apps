"""In-memory status tracker for one-time PDF/codebase ingestion background jobs."""
from __future__ import annotations

import time

TERMINAL_STATUSES = {"done", "error", "cancelled"}

_jobs: dict[str, dict] = {}


def register_job(job_id: str, kind: str, source_path: str = "") -> None:
    _jobs[job_id] = {
        "job_id": job_id,
        "kind": kind,
        "status": "running",
        "progress": {"done": 0, "total": 0},
        "result": None,
        "errors": [],
        "started_at": time.time(),
        "source_path": source_path,
        "phase": None,
    }


def update_progress(job_id: str, done: int, total: int) -> None:
    job = _jobs.get(job_id)
    if job is not None:
        job["progress"] = {"done": done, "total": total}


def set_phase(job_id: str, phase: str) -> None:
    job = _jobs.get(job_id)
    if job is not None:
        job["phase"] = phase


def update_result(job_id: str, partial: dict) -> None:
    """Shallow-merges a tier's partial result into the job's live result, so a
    running job's per-file progress is visible before it reaches a terminal
    state. Merge is shallow/key-level only ({**old, **new}) -- correct today
    since tier 1 writes "files" and tier 2 writes "enrichment_files", two
    disjoint keys; a future overlapping key would fully replace rather than
    deep-merge."""
    job = _jobs.get(job_id)
    if job is not None:
        job["result"] = {**(job["result"] or {}), **partial}


def finish_job(job_id: str, result: dict) -> None:
    job = _jobs.get(job_id)
    if job is not None:
        job["errors"] = result.get("errors", [])
        job["status"] = "done" if not job["errors"] else "error"
        job["result"] = result


def fail_job(job_id: str, error: str) -> None:
    job = _jobs.get(job_id)
    if job is not None:
        job["status"] = "error"
        job["errors"] = job["errors"] + [error]


def get_ingest_job(job_id: str) -> dict | None:
    return _jobs.get(job_id)


def is_terminal(job_id: str) -> bool:
    job = _jobs.get(job_id)
    return job is not None and job["status"] in TERMINAL_STATUSES


def cancel_job(job_id: str) -> None:
    job = _jobs.get(job_id)
    if job is not None:
        job["status"] = "cancelled"
