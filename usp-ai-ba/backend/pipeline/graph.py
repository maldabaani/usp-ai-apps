"""LangGraph definition wiring the 6 StoryForge nodes together.

Flow: ANALYZE -> CLARIFY -> GENERATE -> REVIEW -> (CREATE_ADO | EXPORT_DOCUMENT | CREATE_NOTION)

Every edge after a node is conditional on ``status``: if a node failed and
set ``status == "error"``, the graph routes straight to END instead of
letting downstream nodes run against incomplete state. Without this, a node
that fails open (catches its own exception, records it in ``errors``, but
returns normally) would let the rest of the linear chain execute anyway;
those downstream nodes find empty input (e.g. no stories to review/create)
and finish "successfully", overwriting ``status`` back to something like
"done" and silently masking the original failure.

After ``review_node``, the graph branches on ``settings.OUTPUT_MODE``: the
default "document" mode writes approved stories to a .docx via
``export_document_node``; "ado" mode pushes to Azure DevOps via
``create_ado_node``; "notion" mode pushes to a Notion database via
``create_notion_node``. All three nodes are registered unconditionally so the
mode can be flipped at runtime without recompiling.

The graph always interrupts before ``generate_node`` and before whichever of
``create_ado_node`` / ``export_document_node`` / ``create_notion_node`` is
reachable. Whether a given pause is a genuine human-in-the-loop wait or one
that should be auto-resumed immediately (no ambiguities found / review_mode
disabled) is decided by the orchestration layer in ``pipeline.runner``, based
on ``clarification_needed`` and ``review_mode`` in the state at the time of
the pause.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, StateGraph

from config import settings
from pipeline.nodes.analyze import analyze_node
from pipeline.nodes.clarify import clarify_node
from pipeline.nodes.create_ado import create_ado_node
from pipeline.nodes.create_notion import create_notion_node
from pipeline.nodes.export_document import export_document_node
from pipeline.nodes.generate import generate_node
from pipeline.nodes.review import review_node
from pipeline.state import StoryForgeState

NODE_ANALYZE = "analyze_node"
NODE_CLARIFY = "clarify_node"
NODE_GENERATE = "generate_node"
NODE_REVIEW = "review_node"
NODE_CREATE_ADO = "create_ado_node"
NODE_EXPORT_DOCUMENT = "export_document_node"
NODE_CREATE_NOTION = "create_notion_node"


def _route_unless_error(next_node: str):
    """Build a conditional-edge function that short-circuits to END on status=='error'."""

    def _route(state: StoryForgeState) -> str:
        return END if state.get("status") == "error" else next_node

    return _route


def _route_after_review(state: StoryForgeState) -> str:
    if state.get("status") == "error":
        return END
    if settings.OUTPUT_MODE == "ado":
        return NODE_CREATE_ADO
    if settings.OUTPUT_MODE == "notion":
        return NODE_CREATE_NOTION
    return NODE_EXPORT_DOCUMENT


def build_graph(checkpointer):
    """Compile the StoryForge LangGraph with the given checkpointer and
    human-in-the-loop interrupts."""
    builder = StateGraph(StoryForgeState)

    builder.add_node(NODE_ANALYZE, analyze_node)
    builder.add_node(NODE_CLARIFY, clarify_node)
    builder.add_node(NODE_GENERATE, generate_node)
    builder.add_node(NODE_REVIEW, review_node)
    builder.add_node(NODE_CREATE_ADO, create_ado_node)
    builder.add_node(NODE_EXPORT_DOCUMENT, export_document_node)
    builder.add_node(NODE_CREATE_NOTION, create_notion_node)

    builder.set_entry_point(NODE_ANALYZE)
    builder.add_conditional_edges(
        NODE_ANALYZE, _route_unless_error(NODE_CLARIFY), [NODE_CLARIFY, END]
    )
    builder.add_conditional_edges(
        NODE_CLARIFY, _route_unless_error(NODE_GENERATE), [NODE_GENERATE, END]
    )
    builder.add_conditional_edges(
        NODE_GENERATE, _route_unless_error(NODE_REVIEW), [NODE_REVIEW, END]
    )
    builder.add_conditional_edges(
        NODE_REVIEW,
        _route_after_review,
        [NODE_CREATE_ADO, NODE_EXPORT_DOCUMENT, NODE_CREATE_NOTION, END],
    )
    builder.add_edge(NODE_CREATE_ADO, END)
    builder.add_edge(NODE_EXPORT_DOCUMENT, END)
    builder.add_edge(NODE_CREATE_NOTION, END)

    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=[
            NODE_GENERATE,
            NODE_CREATE_ADO,
            NODE_EXPORT_DOCUMENT,
            NODE_CREATE_NOTION,
        ],
    )


_graph = None
_checkpointer_stack: AsyncExitStack | None = None
_init_lock = asyncio.Lock()


async def get_graph():
    """Return the singleton compiled StoryForge graph, opening its persistent
    SQLite checkpointer (settings.JOBS_DIR/checkpoints.sqlite) on first call.

    Jobs are checkpointed per job_id (== LangGraph thread_id), so a job's full
    state -- including one paused mid-review or one that failed and hasn't
    been retried yet -- survives a backend restart, instead of only living in
    process memory for as long as the server stays up.
    """
    global _graph, _checkpointer_stack
    if _graph is not None:
        return _graph

    async with _init_lock:
        if _graph is not None:  # another caller won the race while we waited
            return _graph

        os.makedirs(settings.JOBS_DIR, exist_ok=True)
        db_path = os.path.join(settings.JOBS_DIR, "checkpoints.sqlite")

        _checkpointer_stack = AsyncExitStack()
        checkpointer = await _checkpointer_stack.enter_async_context(
            AsyncSqliteSaver.from_conn_string(db_path)
        )
        await checkpointer.setup()

        _graph = build_graph(checkpointer)

    return _graph


async def close_graph() -> None:
    """Close the checkpointer's DB connection. Call on app shutdown."""
    global _graph, _checkpointer_stack
    if _checkpointer_stack is not None:
        await _checkpointer_stack.aclose()
    _graph = None
    _checkpointer_stack = None
