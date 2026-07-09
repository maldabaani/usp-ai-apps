"""Node 1: extract the Solution Design Document and retrieve RAG context in parallel."""
from __future__ import annotations

import logging
from pathlib import Path

from ingestion import ingest_documents
from ingestion.retrieval import retrieve_all_collections
from pipeline.nodes.context_budget import cap_context
from pipeline.state import StoryForgeState

logger = logging.getLogger(__name__)

# nomic-embed-text has a limited context window; truncate the query text so the
# embedding call stays well within it while still capturing the bulk of the SDD.
MAX_QUERY_CHARS = 8000


async def analyze_node(state: StoryForgeState) -> StoryForgeState:
    """Extract SDD text and run parallel RAG retrieval against all three collections."""
    if state["solution_doc_path"]:
        # PDF or DOCX upload -- dispatches on file extension via the same
        # extraction helper the corpus-ingestion pipeline uses, instead of
        # this node hand-rolling a second, separately-maintained PDF reader
        # (this used to be a private _extract_pdf_text here; now shared).
        solution_doc_text = ingest_documents._extract_text(Path(state["solution_doc_path"]))
    else:
        # Pasted-text submission (api/routers/assess.py's submit_assessment):
        # no file was ever uploaded, so state["solution_doc_text"] was
        # pre-seeded by new_state() with the user's text directly -- nothing
        # to extract.
        solution_doc_text = state["solution_doc_text"]
    query_text = solution_doc_text[:MAX_QUERY_CHARS]

    try:
        retrieved = await retrieve_all_collections(query_text)
    except Exception as exc:  # noqa: BLE001 - surfaced to caller via state errors
        logger.exception("RAG retrieval failed during analyze_node")
        return {
            **state,
            "solution_doc_text": solution_doc_text,
            "errors": state["errors"] + [f"analyze_node: {exc}"],
            "status": "error",
        }

    retrieved = cap_context(retrieved, "analyze_node")

    return {
        **state,
        "solution_doc_text": solution_doc_text,
        "retrieved_context": retrieved,
        # Named for the node about to run next (clarify_node), not this
        # node's own name -- analyze_node's real work is already done by
        # this point, so leaving this "analyzing" left the status frozen
        # and misleading for the entire duration of clarify_node's own
        # (often slow) LLM call. Deliberately distinct from "clarifying",
        # which the frontend uses to mean "clarify_node finished and is
        # genuinely paused waiting for human answers" -- reusing that value
        # here would cause a premature redirect before any questions exist.
        "status": "detecting_ambiguities",
    }
