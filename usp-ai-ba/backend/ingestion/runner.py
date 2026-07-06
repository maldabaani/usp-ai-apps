"""Runs ingestion jobs as tracked asyncio Tasks (instead of FastAPI
BackgroundTasks) so they can actually be cancelled -- BackgroundTasks never
exposes a handle to what it schedules, so there'd otherwise be nothing for a
cancel to interrupt. Copies pipeline/runner.py's run_tracked()/
_active_tasks/cancel_job() idiom exactly (added this session for StoryForge's
"Stop Assessment" feature) rather than inventing a third cancellation
pattern for a third job type.

Unlike StoryForge's assessment jobs (whose state lives in a separate
LangGraph checkpoint that must be marked "cancelled" after the task stops),
an ingestion job's whole state already lives in api/ingest_jobs.py's
in-memory dict -- there's no second system to reconcile, so cancel_job here
only needs to stop the task and flip that one status field.
"""
from __future__ import annotations

import asyncio
import contextlib
from typing import Coroutine

from api import ingest_jobs

_active_tasks: dict[str, "asyncio.Task"] = {}


def run_tracked(job_id: str, coro: "Coroutine") -> "asyncio.Task":
    """Schedules coro as a fire-and-forget task, tracked by job_id. Holding
    the Task in _active_tasks is itself what keeps it alive (asyncio doesn't
    guarantee an unreferenced task won't be garbage-collected mid-run), and
    is what cancel_job() below cancels."""
    task = asyncio.ensure_future(coro)
    _active_tasks[job_id] = task
    task.add_done_callback(
        lambda t, jid=job_id: _active_tasks.pop(jid, None) if _active_tasks.get(jid) is t else None
    )
    return task


async def cancel_job(job_id: str) -> None:
    """Stops a running ingestion job. Raises ValueError if the job is
    unknown or already terminal -- callers must check this themselves first
    if they want a specific HTTP status for each case (see
    api/routers/ingest.py), matching pipeline/runner.py's cancel_job's own
    validation style."""
    job = ingest_jobs.get_ingest_job(job_id)
    if job is None:
        raise ValueError(f"No ingestion job found for job_id={job_id}")
    if ingest_jobs.is_terminal(job_id):
        raise ValueError(f"Ingestion job {job_id} is already {job['status']!r}")

    task = _active_tasks.get(job_id)
    if task is not None and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    ingest_jobs.cancel_job(job_id)
