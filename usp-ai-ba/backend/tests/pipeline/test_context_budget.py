"""Covers pipeline/nodes/context_budget.py's cap_context() -- the
defense-in-depth RAG-context size guard applied once by analyze_node right
after retrieval. Regression coverage for a real production incident: an
unbounded chunk (traced to ingestion/enrichment/enrich.py's own missing
size cap, fixed separately) produced a 656,961-token prompt that Ollama
silently truncated by 97%+.
"""
from __future__ import annotations

import logging

from ingestion import ingest_code
from pipeline.nodes.context_budget import cap_context


def _chunk(content: str, source: str = "file.py") -> dict:
    return {"content": content, "metadata": {"source": source}}


def test_no_truncation_when_already_under_budget(caplog):
    retrieved = {
        "manuals": [_chunk("a" * 100)],
        "codebase": [_chunk("b" * 100)],
        "entities": [],
    }

    with caplog.at_level(logging.WARNING):
        result = cap_context(retrieved, "test_node", max_chars=10_000)

    assert result == retrieved
    assert caplog.messages == []


def test_truncates_at_budget_preserving_order(caplog):
    retrieved = {
        "manuals": [_chunk("a" * 60, "m1")],
        "codebase": [_chunk("b" * 60, "c1"), _chunk("c" * 60, "c2")],
        "entities": [_chunk("d" * 60, "e1")],
    }

    with caplog.at_level(logging.WARNING):
        result = cap_context(retrieved, "test_node", max_chars=100)

    # First chunk (60 chars) fits; second chunk would push remaining <= 0,
    # so everything after it (in this and later collections) is dropped.
    assert result["manuals"] == [retrieved["manuals"][0]]
    assert result["codebase"] == [retrieved["codebase"][0]]
    assert result["entities"] == []
    assert any("retrieved context truncated at 100 chars" in message for message in caplog.messages)
    assert any("test_node" in message for message in caplog.messages)
    assert any("2 of 4 chunks included" in message for message in caplog.messages)


def test_returns_all_original_keys_even_when_fully_truncated(caplog):
    retrieved = {
        "manuals": [_chunk("a" * 200)],
        "codebase": [_chunk("b" * 200)],
        "entities": [_chunk("c" * 200)],
    }

    result = cap_context(retrieved, "test_node", max_chars=0)

    assert set(result.keys()) == {"manuals", "codebase", "entities"}
    assert result["manuals"] == []
    assert result["codebase"] == []
    assert result["entities"] == []


def test_one_oversized_chunk_is_truncated_to_max_chunk_chars(caplog):
    # Regression test: a still-stale (not yet re-ingested since a chunking
    # fix shipped) or otherwise-misbehaving corpus can produce one single
    # chunk large enough to blow the entire cumulative budget by itself --
    # confirmed in production (a 656,961-token prompt from one oversized
    # LLM-summary document). This per-chunk cap is the actual fix for that,
    # independent of whether enrich.py's own size cap has been applied to
    # already-ingested data yet.
    retrieved = {
        "manuals": [],
        "codebase": [_chunk("z" * 1_000_000, "huge.js")],
        "entities": [],
    }

    with caplog.at_level(logging.WARNING):
        result = cap_context(retrieved, "test_node", max_chars=120_000)

    truncated_content = result["codebase"][0]["content"]
    assert len(truncated_content) == ingest_code.MAX_CHUNK_CHARS + len("\n...[truncated]")
    assert truncated_content.endswith("...[truncated]")
    assert result["codebase"][0]["metadata"] == retrieved["codebase"][0]["metadata"]
    assert any("1 chunk(s) individually truncated" in message for message in caplog.messages)


def test_chunk_at_or_under_max_chunk_chars_is_left_unchanged(caplog):
    retrieved = {
        "manuals": [],
        "codebase": [_chunk("y" * ingest_code.MAX_CHUNK_CHARS, "normal.js")],
        "entities": [],
    }

    with caplog.at_level(logging.WARNING):
        result = cap_context(retrieved, "test_node", max_chars=120_000)

    assert result["codebase"] == retrieved["codebase"]
    assert caplog.messages == []
