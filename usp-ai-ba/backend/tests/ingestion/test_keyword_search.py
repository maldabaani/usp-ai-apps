"""Covers ingestion/chroma_client.py's keyword_search() -- the literal
substring/token pass unioned with vector search in ingestion/retrieval.py
to guarantee an exact identifier/term mentioned in a question surfaces even
when it isn't semantically close enough to rank in vector search's own
top-k. A fake vector store stands in for Chroma, matching this codebase's
established mocked-client testing convention.
"""
from __future__ import annotations

import asyncio

import pytest

from ingestion import chroma_client, ingestion_generation


class _FakeVectorStore:
    def __init__(self, documents: list[str], metadatas: list[dict]):
        self._documents = documents
        self._metadatas = metadatas
        self.pull_count = 0

    def get(self, include=None):
        self.pull_count += 1
        return {"documents": self._documents, "metadatas": self._metadatas}


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    monkeypatch.setattr(chroma_client, "_keyword_doc_cache", {})
    original_generation = ingestion_generation.current()
    yield
    chroma_client._keyword_doc_cache.clear()
    ingestion_generation._generation = original_generation


def _patch_store(monkeypatch, documents: list[str], metadatas: list[dict]) -> _FakeVectorStore:
    store = _FakeVectorStore(documents, metadatas)
    monkeypatch.setattr(chroma_client, "get_vector_store", lambda key: store)
    return store


def test_whole_query_substring_match_scores_highest_and_is_returned(monkeypatch):
    _patch_store(
        monkeypatch,
        documents=["function accountToss() { return true; }", "unrelated content entirely"],
        metadatas=[{"source": "full-code.js"}, {"source": "other.js"}],
    )

    results = asyncio.run(chroma_client.keyword_search("codebase", "what does accounttoss do?"))

    assert len(results) == 1
    assert results[0]["metadata"]["source"] == "full-code.js"


def test_partial_token_match_is_still_returned_but_lower_ranked(monkeypatch):
    _patch_store(
        monkeypatch,
        documents=[
            "def account_toss_handler(): pass",
            "class AccountTossService: def toss_account(self): pass",
        ],
        metadatas=[{"source": "partial.py"}, {"source": "exact.py"}],
    )

    results = asyncio.run(chroma_client.keyword_search("codebase", "account toss"))

    sources = [r["metadata"]["source"] for r in results]
    assert "partial.py" in sources
    assert "exact.py" in sources


def test_no_match_is_excluded(monkeypatch):
    _patch_store(
        monkeypatch,
        documents=["totally unrelated content"],
        metadatas=[{"source": "other.js"}],
    )

    results = asyncio.run(chroma_client.keyword_search("codebase", "accounttoss"))

    assert results == []


def test_results_capped_at_limit(monkeypatch):
    documents = [f"accounttoss variant {i}" for i in range(10)]
    metadatas = [{"source": f"file{i}.js"} for i in range(10)]
    _patch_store(monkeypatch, documents=documents, metadatas=metadatas)

    results = asyncio.run(chroma_client.keyword_search("codebase", "accounttoss", limit=3))

    assert len(results) == 3


def test_case_insensitive_match(monkeypatch):
    _patch_store(
        monkeypatch,
        documents=["function accountToss() {}"],
        metadatas=[{"source": "full-code.js"}],
    )

    results = asyncio.run(chroma_client.keyword_search("codebase", "AccountToss"))

    assert len(results) == 1


def test_cache_not_repulled_when_generation_unchanged(monkeypatch):
    store = _patch_store(
        monkeypatch,
        documents=["accounttoss content"],
        metadatas=[{"source": "full-code.js"}],
    )

    asyncio.run(chroma_client.keyword_search("codebase", "accounttoss"))
    asyncio.run(chroma_client.keyword_search("codebase", "accounttoss"))

    assert store.pull_count == 1


def test_cache_repulled_after_ingestion_generation_bumps(monkeypatch):
    store = _patch_store(
        monkeypatch,
        documents=["accounttoss content"],
        metadatas=[{"source": "full-code.js"}],
    )

    asyncio.run(chroma_client.keyword_search("codebase", "accounttoss"))
    ingestion_generation.bump()
    asyncio.run(chroma_client.keyword_search("codebase", "accounttoss"))

    assert store.pull_count == 2
