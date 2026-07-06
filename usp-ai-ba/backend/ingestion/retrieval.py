"""Shared RAG retrieval over the three ingested Chroma collections
(manuals/codebase/entities). Extracted from pipeline/nodes/analyze.py so
StoryForge's assess pipeline and the new Ask Technical/Business endpoints
(api/routers/ask.py) share one retrieval implementation instead of each
having their own copy.
"""
from __future__ import annotations

import asyncio

from ingestion.chroma_client import get_vector_store

TOP_K_PER_COLLECTION = 10


def _doc_to_dict(document) -> dict:
    return {"content": document.page_content, "metadata": document.metadata}


async def _retrieve(collection_key: str, query: str, top_k: int) -> list[dict]:
    vector_store = get_vector_store(collection_key)
    results = await vector_store.asimilarity_search(query, k=top_k)
    return [_doc_to_dict(doc) for doc in results]


async def retrieve_all_collections(query: str, top_k: int = TOP_K_PER_COLLECTION) -> dict[str, list[dict]]:
    """Runs similarity search against all three collections in parallel,
    returning {"manuals": [...], "codebase": [...], "entities": [...]}, each
    a list of {"content", "metadata"} dicts."""
    manuals, codebase, entities = await asyncio.gather(
        _retrieve("manuals", query, top_k),
        _retrieve("codebase", query, top_k),
        _retrieve("entities", query, top_k),
    )
    return {"manuals": manuals, "codebase": codebase, "entities": entities}
