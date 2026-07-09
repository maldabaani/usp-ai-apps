"""Shared RAG-context size guard, applied once by analyze_node right after
retrieval so clarify_node/generate_node never build a prompt off an
unbounded amount of retrieved content. This is a defense-in-depth safety
net, not the primary fix -- the actual per-chunk size ceiling lives in
ingest_code.py's mechanical chunking (MAX_CHUNK_CHARS) and, since a
production incident showed it was missing there, in
ingestion/enrichment/enrich.py's per-file LLM-summary documents too. Even
with every chunk properly capped, the theoretical worst case (today's
hybrid keyword+vector search can return up to 45 chunks across the three
collections) can still exceed what comfortably fits inside
settings.OLLAMA_NUM_CTX alongside the system prompt, SDD text, and
generate_node's own output reservation -- rather than let Ollama's own
silent, content-agnostic truncation handle that (which just cuts the raw
prompt string, discarding whichever chunks happen to land past the cutoff,
with zero visibility), this proactively trims to a char budget and logs
when it happens.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ~30-40k tokens at ingest_code.py's own established ~3-4 chars/token ratio,
# comfortably under settings.OLLAMA_NUM_CTX's default (32768 tokens) with
# room left for the system prompt, SDD text, and generate_node's own
# MAX_OUTPUT_TOKENS (8192) reservation.
MAX_RAG_CONTEXT_CHARS = 120_000


def cap_context(retrieved: dict[str, list[dict]], node_name: str, max_chars: int = MAX_RAG_CONTEXT_CHARS) -> dict[str, list[dict]]:
    """Keeps chunks in their existing retrieved order (manuals, then
    codebase, then entities -- matching retrieve_all_collections' own
    return shape and the order clarify.py/generate.py concatenate them in)
    until the combined character budget is spent; every chunk after that
    point, in this or any later collection, is dropped. Returns a dict with
    the same keys as `retrieved`, so callers never need a None/missing-key
    check downstream.
    """
    total_chunks = sum(len(chunks) for chunks in retrieved.values())
    capped: dict[str, list[dict]] = {}
    remaining = max_chars
    included = 0
    for key, chunks in retrieved.items():
        kept = []
        for chunk in chunks:
            if remaining <= 0:
                break
            kept.append(chunk)
            included += 1
            remaining -= len(chunk.get("content") or "")
        capped[key] = kept

    if included < total_chunks:
        logger.warning(
            "%s: retrieved context truncated at %d chars (%d of %d chunks included)",
            node_name,
            max_chars,
            included,
            total_chunks,
        )

    return capped
