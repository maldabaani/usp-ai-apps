"""Node 1: extract the Solution Design Document and retrieve RAG context in parallel."""
from __future__ import annotations

import logging

from pypdf import PdfReader

from ingestion.retrieval import retrieve_all_collections
from pipeline.state import StoryForgeState

logger = logging.getLogger(__name__)

# nomic-embed-text has a limited context window; truncate the query text so the
# embedding call stays well within it while still capturing the bulk of the SDD.
MAX_QUERY_CHARS = 8000


def _extract_pdf_text(pdf_path: str) -> str:
    reader = PdfReader(pdf_path)
    pages_text = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if text.strip():
            pages_text.append(f"[Page {page_number}]\n{text}")
    return "\n\n".join(pages_text)


async def analyze_node(state: StoryForgeState) -> StoryForgeState:
    """Extract SDD text and run parallel RAG retrieval against all three collections."""
    solution_doc_text = _extract_pdf_text(state["solution_doc_path"])
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

    return {
        **state,
        "solution_doc_text": solution_doc_text,
        "retrieved_context": retrieved,
        "status": "analyzing",
    }
