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

Also truncates any individual chunk whose own content exceeds
ingest_code.MAX_CHUNK_CHARS -- confirmed in production that a still-stale
(not yet re-ingested since a chunking fix shipped) or otherwise-misbehaving
corpus can produce one single chunk large enough to blow the entire
cumulative budget by itself before the running-total check below ever gets
a chance to stop it. This per-chunk cap is what actually closes that gap,
independent of whether every upstream tier's own size cap has been applied
to already-ingested data yet.
"""
from __future__ import annotations

import logging

from ingestion import ingest_code

logger = logging.getLogger(__name__)

# ~30-40k tokens at ingest_code.py's own established ~3-4 chars/token ratio,
# comfortably under settings.OLLAMA_NUM_CTX's default (32768 tokens) with
# room left for the system prompt, SDD text, and generate_node's own
# MAX_OUTPUT_TOKENS (8192) reservation.
MAX_RAG_CONTEXT_CHARS = 120_000

_TRUNCATION_MARKER = "\n...[truncated]"


def cap_context(retrieved: dict[str, list[dict]], node_name: str, max_chars: int = MAX_RAG_CONTEXT_CHARS) -> dict[str, list[dict]]:
    """Keeps chunks in their existing retrieved order (manuals, then
    codebase, then entities -- matching retrieve_all_collections' own
    return shape and the order clarify.py/generate.py concatenate them in)
    until the combined character budget is spent; every chunk after that
    point, in this or any later collection, is dropped. Any individual
    chunk exceeding ingest_code.MAX_CHUNK_CHARS is truncated to that size
    (plus a short marker) before being counted against the budget, so one
    oversized chunk can never single-handedly exhaust it. Returns a dict
    with the same keys as `retrieved`, so callers never need a
    None/missing-key check downstream.
    """
    total_chunks = sum(len(chunks) for chunks in retrieved.values())
    capped: dict[str, list[dict]] = {}
    remaining = max_chars
    included = 0
    truncated_chunks = 0
    for key, chunks in retrieved.items():
        kept = []
        for chunk in chunks:
            if remaining <= 0:
                break
            content = chunk.get("content") or ""
            if len(content) > ingest_code.MAX_CHUNK_CHARS:
                content = content[: ingest_code.MAX_CHUNK_CHARS] + _TRUNCATION_MARKER
                truncated_chunks += 1
                chunk = {**chunk, "content": content}
            kept.append(chunk)
            included += 1
            remaining -= len(content)
        capped[key] = kept

    if included < total_chunks or truncated_chunks:
        logger.warning(
            "%s: retrieved context truncated at %d chars (%d of %d chunks included, %d chunk(s) individually truncated)",
            node_name,
            max_chars,
            included,
            total_chunks,
            truncated_chunks,
        )

    return capped
