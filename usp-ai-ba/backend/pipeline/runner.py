"""Drives the StoryForge LangGraph: starts jobs, auto-resumes pauses that don't
need human input, and exposes explicit resume points for clarification answers,
review approval, and retrying a failed step.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Coroutine

from config import settings
from notion_export.client import get_notion_export_client
from pipeline.graph import (
    NODE_CLARIFY,
    NODE_CREATE_ADO,
    NODE_CREATE_NOTION,
    NODE_EXPORT_DOCUMENT,
    NODE_GENERATE,
    NODE_REVIEW,
    get_graph,
)
from pipeline.state import StoryForgeState, resolve_output_mode

RECREATABLE_OUTPUT_MODES = ("ado", "notion")
UPDATABLE_OUTPUT_MODES = ("notion",)
TERMINAL_STATUSES = {"done", "error", "cancelled"}

logger = logging.getLogger(__name__)

# Tracks the currently in-flight asyncio Task for each job_id (start/retry/
# recreate/update are mutually exclusive in practice, so one entry per job_id
# is enough) so cancel_job() has something to actually interrupt. FastAPI's
# BackgroundTasks doesn't expose a handle to what it schedules -- it just
# awaits callables after the response is sent -- so there'd otherwise be no
# way to stop a running job at all, unlike CodeMind's extraction jobs.
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

# Maps the node whose error prefix (e.g. "generate_node: ...") appears last in
# state["errors"] to (node to rewind the checkpoint to, status to resume with).
# Rewinding to a node "replays" the graph as if it had just finished that node,
# so the interrupt before the *next* node fires again and only the failed node
# (plus anything after it) re-runs -- earlier work (SDD parsing, RAG retrieval,
# clarification, generation, review) isn't redone.
_RETRYABLE_REWIND_POINTS: dict[str, tuple[str, str]] = {
    NODE_GENERATE: (NODE_CLARIFY, "generating"),
    NODE_CREATE_ADO: (NODE_REVIEW, "creating"),
    NODE_EXPORT_DOCUMENT: (NODE_REVIEW, "creating"),
    NODE_CREATE_NOTION: (NODE_REVIEW, "creating"),
}


def _config(job_id: str) -> dict:
    return {"configurable": {"thread_id": job_id}}


async def _drive(job_id: str, resume_value=None) -> StoryForgeState:
    """Advance the graph, auto-continuing past pauses that don't require human
    input, and stopping (without error) at pauses that do."""
    graph = await get_graph()
    config = _config(job_id)

    state = await graph.ainvoke(resume_value, config)
    snapshot = await graph.aget_state(config)

    while snapshot.next:
        next_node = snapshot.next[0]

        if next_node == NODE_GENERATE and not state.get("clarification_needed"):
            state = await graph.ainvoke(None, config)
        elif (
            next_node in (NODE_CREATE_ADO, NODE_EXPORT_DOCUMENT, NODE_CREATE_NOTION)
            and not state.get("review_mode")
        ):
            state = await graph.ainvoke(None, config)
        else:
            break

        snapshot = await graph.aget_state(config)

    return state


async def start_job(initial_state: StoryForgeState) -> StoryForgeState:
    """Run a brand-new job from analyze_node up to the first genuine human-in-the-loop pause."""
    return await _drive(initial_state["job_id"], initial_state)


async def get_job_state(job_id: str) -> StoryForgeState | None:
    """Return the current StoryForgeState for a job, or None if it doesn't exist."""
    graph = await get_graph()
    snapshot = await graph.aget_state(_config(job_id))
    return snapshot.values or None


async def resume_after_clarification(job_id: str, answers: dict) -> StoryForgeState:
    """Apply clarification answers and resume the graph through generate_node onward."""
    graph = await get_graph()
    config = _config(job_id)
    await graph.aupdate_state(
        config,
        {
            "clarification_answers": answers,
            "clarification_needed": False,
            "status": "generating",
        },
    )
    return await _drive(job_id)


async def resume_after_review(job_id: str, approved_stories: list[dict]) -> StoryForgeState:
    """Apply human-approved stories and resume the graph through create_ado_node /
    export_document_node / create_notion_node (whichever settings.OUTPUT_MODE selects)."""
    graph = await get_graph()
    config = _config(job_id)
    await graph.aupdate_state(
        config,
        {
            "approved_stories": approved_stories,
            "human_approved": True,
            "status": "creating",
        },
    )
    return await _drive(job_id)


def identify_retryable_failure(state: StoryForgeState) -> str | None:
    """Identify which node produced the last error, from its "<node_name>: ..."
    prefix -- every node in pipeline/nodes/ appends errors in that format.

    Returns None if the job has no errors, or if the failure isn't one
    retry_failed_step() can resume from (see its docstring)."""
    errors = state.get("errors") or []
    if not errors:
        return None
    prefix = errors[-1].split(":", 1)[0].strip()
    return prefix if prefix in _RETRYABLE_REWIND_POINTS else None


async def retry_failed_step(job_id: str) -> StoryForgeState:
    """Re-run whichever node last failed, without redoing earlier work (SDD
    parsing, RAG retrieval, clarification, generation, or review) that already
    succeeded.

    Only resumable for a failure in generate_node, create_ado_node,
    export_document_node, or create_notion_node -- each of those runs right
    after an interrupt point, so rewinding the checkpoint to the preceding
    node makes that same interrupt fire again. A failure inside analyze_node
    has no earlier checkpoint to rewind to; clarify_node fails open instead of
    ever leaving the job in status=="error". Both raise ValueError here since
    there's nothing to retry -- start a new assessment instead.

    Note: retrying create_ado_node/export_document_node/create_notion_node
    re-creates *all* approved stories/tasks from scratch, including ones that
    already succeeded before the failure -- these nodes isolate failures
    per-item but aren't otherwise idempotent, so a retry after a partial
    failure can produce duplicate ADO work items / Notion pages / a
    re-generated document for the items that succeeded on the first attempt.
    """
    graph = await get_graph()
    config = _config(job_id)
    snapshot = await graph.aget_state(config)
    state = snapshot.values
    if not state:
        raise ValueError(f"No job found for job_id={job_id}")
    if state.get("status") != "error":
        raise ValueError(f"Job {job_id} is not in an error state (status={state.get('status')!r})")

    failed_node = identify_retryable_failure(state)
    if failed_node is None:
        raise ValueError(
            f"Job {job_id}'s failure isn't resumable (it failed before the first "
            "checkpoint, or for an unrecognized reason) -- start a new assessment instead."
        )

    rewind_to_node, new_status = _RETRYABLE_REWIND_POINTS[failed_node]
    await graph.aupdate_state(config, {"status": new_status, "errors": []}, as_node=rewind_to_node)
    return await _drive(job_id)


async def recreate_tasks(job_id: str) -> StoryForgeState:
    """Push a completed job's approved stories to its task-management system
    again, from scratch. Only valid for output_mode "ado" or "notion" -- a
    "document" job has nothing to "recreate" (it's just a file download).

    For Notion, the job's previously-created pages (state["notion_results"])
    are archived first, so re-creating doesn't leave duplicates behind. ADO
    has no delete capability available to this codebase (the external MCP
    server at settings.MCP_SERVER_PATH exposes only create_* tools), so ADO
    re-create just creates a fresh Epic/Story/Tasks hierarchy alongside
    whatever was created before.

    Reuses the same checkpoint-rewind mechanism as retry_failed_step: rewind
    to NODE_REVIEW so the interrupt before the create node fires again, then
    let _drive continue into it.
    """
    graph = await get_graph()
    config = _config(job_id)
    snapshot = await graph.aget_state(config)
    state = snapshot.values
    if not state:
        raise ValueError(f"No job found for job_id={job_id}")
    if state.get("status") != "done":
        raise ValueError(f"Job {job_id} is not done (status={state.get('status')!r})")

    output_mode = resolve_output_mode(state, settings.OUTPUT_MODE)
    if output_mode not in RECREATABLE_OUTPUT_MODES:
        raise ValueError(
            f"Job {job_id}'s output mode ({output_mode!r}) doesn't support re-creating tasks "
            f"-- only {RECREATABLE_OUTPUT_MODES} do"
        )

    archive_warnings: list[str] = []
    if output_mode == "notion":
        client = get_notion_export_client()
        for result in state.get("notion_results") or []:
            page_id = result.get("page_id")
            try:
                await client.archive_page(page_id)
            except Exception as exc:  # noqa: BLE001 - one page failing to archive shouldn't block the rest
                logger.exception("Failed to archive prior Notion page %s for job %s", page_id, job_id)
                archive_warnings.append(f"Could not archive old Notion page {page_id}: {exc}")

    await graph.aupdate_state(
        config, {"status": "creating", "errors": [], "warnings": archive_warnings}, as_node=NODE_REVIEW
    )
    return await _drive(job_id)


async def update_tasks(job_id: str) -> StoryForgeState:
    """Push a completed job's approved stories to Notion again, updating its
    existing pages in place (position-matched) instead of creating fresh
    ones or archiving first. Notion only -- ADO has no update/delete
    capability available to this codebase (same gap as recreate_tasks).

    Reuses the same checkpoint-rewind mechanism as recreate_tasks/
    retry_failed_step: rewind to NODE_REVIEW so the interrupt before
    create_notion_node fires again, with notion_update_mode set so that node
    updates in place this time instead of creating fresh (see its own
    docstring for the position-matching rules).
    """
    graph = await get_graph()
    config = _config(job_id)
    snapshot = await graph.aget_state(config)
    state = snapshot.values
    if not state:
        raise ValueError(f"No job found for job_id={job_id}")
    if state.get("status") != "done":
        raise ValueError(f"Job {job_id} is not done (status={state.get('status')!r})")

    output_mode = resolve_output_mode(state, settings.OUTPUT_MODE)
    if output_mode not in UPDATABLE_OUTPUT_MODES:
        raise ValueError(
            f"Job {job_id}'s output mode ({output_mode!r}) doesn't support updating tasks in place "
            f"-- only {UPDATABLE_OUTPUT_MODES} do"
        )

    await graph.aupdate_state(
        config,
        {"status": "creating", "errors": [], "notion_update_mode": True},
        as_node=NODE_REVIEW,
    )
    return await _drive(job_id)


async def cancel_job(job_id: str) -> StoryForgeState:
    """Stops a running assessment job. Cancels the tracked asyncio Task (if
    one is currently in flight -- the human-in-the-loop pauses,
    "clarifying"/"reviewing" with review_mode on, have no task running at
    all, just a checkpoint waiting for an answer/approval that will now never
    come) and marks the checkpoint status "cancelled" either way, so
    GET /assess/status/{job_id} reflects it instead of staying stuck showing
    whatever status was current the moment cancellation was requested.

    Callers must check TERMINAL_STATUSES themselves before calling this (see
    api/routers/assess.py) -- raises ValueError as a last-line defense against
    a race, matching this module's existing validation style."""
    graph = await get_graph()
    config = _config(job_id)
    snapshot = await graph.aget_state(config)
    state = snapshot.values
    if not state:
        raise ValueError(f"No job found for job_id={job_id}")
    if state.get("status") in TERMINAL_STATUSES:
        raise ValueError(f"Job {job_id} is already {state.get('status')!r}")

    task = _active_tasks.get(job_id)
    if task is not None and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    await graph.aupdate_state(config, {"status": "cancelled"})
    snapshot = await graph.aget_state(config)
    return snapshot.values
