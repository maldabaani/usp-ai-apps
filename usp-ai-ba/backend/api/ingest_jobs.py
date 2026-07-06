"""In-memory status tracker for one-time PDF/codebase ingestion background jobs."""
from __future__ import annotations

import time

TERMINAL_STATUSES = {"done", "error", "cancelled"}

_jobs: dict[str, dict] = {}


def register_job(job_id: str, kind: str) -> None:
    _jobs[job_id] = {
        "job_id": job_id,
        "kind": kind,
        "status": "running",
        "progress": {"done": 0, "total": 0},
        "result": None,
        "errors": [],
        "started_at": time.time(),
    }


def update_progress(job_id: str, done: int, total: int) -> None:
    job = _jobs.get(job_id)
    if job is not None:
        job["progress"] = {"done": done, "total": total}


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
