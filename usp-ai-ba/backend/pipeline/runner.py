"""Drives the StoryForge LangGraph: starts jobs, auto-resumes pauses that don't
need human input, and exposes explicit resume points for clarification answers
and review approval.
"""
from __future__ import annotations

import logging

from pipeline.graph import (
    NODE_CREATE_ADO,
    NODE_CREATE_NOTION,
    NODE_EXPORT_DOCUMENT,
    NODE_GENERATE,
    get_graph,
)
from pipeline.state import StoryForgeState

logger = logging.getLogger(__name__)


def _config(job_id: str) -> dict:
    return {"configurable": {"thread_id": job_id}}


async def _drive(job_id: str, resume_value=None) -> StoryForgeState:
    """Advance the graph, auto-continuing past pauses that don't require human
    input, and stopping (without error) at pauses that do."""
    graph = get_graph()
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
    graph = get_graph()
    snapshot = await graph.aget_state(_config(job_id))
    return snapshot.values or None


async def resume_after_clarification(job_id: str, answers: dict) -> StoryForgeState:
    """Apply clarification answers and resume the graph through generate_node onward."""
    graph = get_graph()
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
    graph = get_graph()
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
