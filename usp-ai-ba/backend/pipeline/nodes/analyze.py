"""Node 1: extract the Solution Design Document and retrieve RAG context in parallel."""
from __future__ import annotations

import asyncio
import logging

from pypdf import PdfReader

from ingestion.chroma_client import get_vector_store
from pipeline.state import StoryForgeState

logger = logging.getLogger(__name__)

TOP_K_PER_COLLECTION = 10
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


def _doc_to_dict(document) -> dict:
    return {"content": document.page_content, "metadata": document.metadata}


async def _retrieve(collection_key: str, query: str) -> list[dict]:
    vector_store = get_vector_store(collection_key)
    results = await vector_store.asimilarity_search(query, k=TOP_K_PER_COLLECTION)
    return [_doc_to_dict(doc) for doc in results]


async def analyze_node(state: StoryForgeState) -> StoryForgeState:
    """Extract SDD text and run parallel RAG retrieval against all three collections."""
    solution_doc_text = _extract_pdf_text(state["solution_doc_path"])
    query_text = solution_doc_text[:MAX_QUERY_CHARS]

    try:
        manuals, codebase, entities = await asyncio.gather(
            _retrieve("manuals", query_text),
            _retrieve("codebase", query_text),
            _retrieve("entities", query_text),
        )
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
        "retrieved_context": {
            "manuals": manuals,
            "codebase": codebase,
            "entities": entities,
        },
        "status": "analyzing",
    }
