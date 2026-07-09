"""Covers ingestion/retrieval.py's retrieve_all_collections()/_retrieve() --
specifically the hybrid vector+keyword merge added to guarantee an exact
identifier/term mentioned in a question surfaces even when vector search's
own top-k misses it. Every existing caller (api/routers/ask.py,
pipeline/nodes/analyze.py) mocks retrieve_all_collections directly in its
own tests, so this file is the only place retrieve_all_collections/_retrieve
themselves are exercised.
"""
from __future__ import annotations

import asyncio

from ingestion import chroma_client, retrieval


class _FakeDocument:
    def __init__(self, content: str, metadata: dict):
        self.page_content = content
        self.metadata = metadata


class _FakeVectorStore:
    def __init__(self, documents: list[_FakeDocument]):
        self._documents = documents

    async def asimilarity_search(self, query, k):
        return self._documents[:k]


def _patch_vector_store(monkeypatch, documents: list[_FakeDocument]) -> None:
    store = _FakeVectorStore(documents)
    monkeypatch.setattr(retrieval, "get_vector_store", lambda key: store)


def test_keyword_only_hit_still_surfaces_when_vector_search_misses_it(monkeypatch):
    _patch_vector_store(monkeypatch, documents=[])

    async def fake_keyword_search(collection_key, query, limit=None):
        return [{"content": "function accountToss() {}", "metadata": {"source": "full-code.js"}}]

    monkeypatch.setattr(retrieval, "keyword_search", fake_keyword_search)

    result = asyncio.run(retrieval.retrieve_all_collections("what does accounttoss do?"))

    assert any(doc["metadata"]["source"] == "full-code.js" for doc in result["codebase"])


def test_duplicate_hit_from_both_vector_and_keyword_search_appears_once(monkeypatch):
    shared_doc = _FakeDocument("shared content", {"source": "shared.js"})
    _patch_vector_store(monkeypatch, documents=[shared_doc])

    async def fake_keyword_search(collection_key, query, limit=None):
        return [{"content": "shared content", "metadata": {"source": "shared.js"}}]

    monkeypatch.setattr(retrieval, "keyword_search", fake_keyword_search)

    result = asyncio.run(retrieval.retrieve_all_collections("shared"))

    matches = [doc for doc in result["codebase"] if doc["metadata"]["source"] == "shared.js"]
    assert len(matches) == 1


def test_vector_only_hits_unchanged_when_no_keyword_match(monkeypatch):
    vector_doc = _FakeDocument("vector-matched content", {"source": "vector.js"})
    _patch_vector_store(monkeypatch, documents=[vector_doc])

    async def fake_keyword_search(collection_key, query, limit=None):
        return []

    monkeypatch.setattr(retrieval, "keyword_search", fake_keyword_search)

    result = asyncio.run(retrieval.retrieve_all_collections("some question"))

    assert result["codebase"] == [{"content": "vector-matched content", "metadata": {"source": "vector.js"}}]
