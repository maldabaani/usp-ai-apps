"""Drives the StoryForge LangGraph: starts jobs, auto-resumes pauses that don't
need human input, and exposes explicit resume points for clarification answers,
review approval, and retrying a failed step.
"""
from __future__ import annotations

import logging

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
from pipeline.state import StoryForgeState

RECREATABLE_OUTPUT_MODES = ("ado", "notion")

logger = logging.getLogger(__name__)

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

    output_mode = state.get("output_mode")
    if output_mode not in RECREATABLE_OUTPUT_MODES:
        raise ValueError(
            f"Job {job_id}'s output mode ({output_mode!r}) doesn't support re-creating tasks "
            f"-- only {RECREATABLE_OUTPUT_MODES} do"
        )

    if output_mode == "notion":
        client = get_notion_export_client()
        for result in state.get("notion_results") or []:
            try:
                await client.archive_page(result["page_id"])
            except Exception:  # noqa: BLE001 - one page failing to archive shouldn't block the rest
                logger.exception(
                    "Failed to archive prior Notion page %s for job %s", result.get("page_id"), job_id
                )

    await graph.aupdate_state(config, {"status": "creating", "errors": []}, as_node=NODE_REVIEW)
    return await _drive(job_id)
