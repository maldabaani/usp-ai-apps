"""Shared RAG retrieval over the three ingested Chroma collections
(manuals/codebase/entities). Extracted from pipeline/nodes/analyze.py so
StoryForge's assess pipeline and the new Ask Technical/Business endpoints
(api/routers/ask.py) share one retrieval implementation instead of each
having their own copy.

Retrieval is a hybrid of vector similarity search (up to TOP_K_PER_COLLECTION
per collection) and a literal keyword/substring pass
(chroma_client.keyword_search, up to KEYWORD_TOP_K_PER_COLLECTION more per
collection) -- vector search alone can miss an exact identifier/term the
question names literally, if that term isn't semantically close enough to
the question's phrasing to rank in the vector search's own top-k. Worst case
this is a deliberate, bounded increase to 15 chunks per collection (was a
flat 10), 45 total across all three (was 30) -- not unlimited growth.
"""
from __future__ import annotations

import asyncio

from ingestion.chroma_client import get_vector_store, keyword_search

TOP_K_PER_COLLECTION = 10


def _doc_to_dict(document) -> dict:
    return {"content": document.page_content, "metadata": document.metadata}


async def _retrieve(collection_key: str, query: str, top_k: int) -> list[dict]:
    vector_store = get_vector_store(collection_key)
    vector_results, keyword_results = await asyncio.gather(
        vector_store.asimilarity_search(query, k=top_k),
        keyword_search(collection_key, query),
    )
    results = [_doc_to_dict(doc) for doc in vector_results]
    seen = {(doc["metadata"].get("source"), doc["content"]) for doc in results}
    for doc in keyword_results:
        key = (doc["metadata"].get("source"), doc["content"])
        if key not in seen:
            seen.add(key)
            results.append(doc)
    return results


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
