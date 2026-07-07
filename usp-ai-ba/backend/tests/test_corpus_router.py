"""Covers GET /api/corpus/sources + POST /api/corpus/sources/delete
(api/routers/corpus.py) and ingestion/chroma_client.py's source_metadata()
helper the GET wraps -- the corpus browser's file-list-plus-metadata view
(no chunk-content drill-down) plus its per-source delete action. Canned
metadatas stand in for a real Chroma collection, matching this codebase's
mocked-client testing convention.
"""
from __future__ import annotations

import asyncio
import time

import jwt
from fastapi.testclient import TestClient

from api.main import app
from config import settings
from ingestion import chroma_client

client = TestClient(app, raise_server_exceptions=False)


def _token(username: str = "corpus_test_user", role: str = "user") -> str:
    payload = {"sub": username, "role": role, "exp": time.time() + 3600}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _auth_headers(role: str = "user") -> dict:
    return {"Authorization": f"Bearer {_token(role=role)}"}


class _FakeVectorStore:
    def __init__(self, metadatas: list[dict]):
        self._metadatas = metadatas
        self.delete_calls: list[dict] = []

    def get(self, include=None):
        return {"metadatas": self._metadatas}

    async def adelete(self, ids=None, where=None):
        self.delete_calls.append(where)
        if where and "source" in where:
            self._metadatas = [m for m in self._metadatas if m.get("source") != where["source"]]


def _patch_stores(monkeypatch, manuals: list[dict], codebase: list[dict]) -> dict:
    stores = {
        "manuals": _FakeVectorStore(manuals),
        "codebase": _FakeVectorStore(codebase),
        "entities": _FakeVectorStore([]),
    }
    monkeypatch.setattr(chroma_client, "get_vector_store", lambda key: stores[key])
    return stores


def test_source_metadata_computes_chunk_count_and_llm_summary_flag(monkeypatch):
    _patch_stores(
        monkeypatch,
        manuals=[],
        codebase=[
            {"source": "Widget.java", "type": "class", "format": None, "ingested_at": 100},
            {"source": "Widget.java", "type": "method", "format": None, "ingested_at": 200},
            {"source": "Widget.java", "type": "llm_summary", "format": None, "ingested_at": 300},
        ],
    )

    rows = asyncio.run(chroma_client.source_metadata("codebase"))

    assert rows == [
        {"source": "Widget.java", "chunk_count": 2, "has_llm_summary": True, "format": None, "ingested_at": 300}
    ]


def test_source_metadata_handles_missing_format_and_ingested_at(monkeypatch):
    _patch_stores(
        monkeypatch,
        manuals=[{"source": "old.pdf", "type": "user_manual"}],
        codebase=[],
    )

    rows = asyncio.run(chroma_client.source_metadata("manuals"))

    assert rows == [
        {"source": "old.pdf", "chunk_count": 1, "has_llm_summary": False, "format": None, "ingested_at": None}
    ]


def test_source_metadata_reports_format_and_max_ingested_at(monkeypatch):
    _patch_stores(
        monkeypatch,
        manuals=[
            {"source": "manual.md", "type": "user_manual", "format": "markdown", "ingested_at": 100},
            {"source": "manual.md", "type": "user_manual", "format": "markdown", "ingested_at": 200},
        ],
        codebase=[],
    )

    rows = asyncio.run(chroma_client.source_metadata("manuals"))

    assert rows == [
        {"source": "manual.md", "chunk_count": 2, "has_llm_summary": False, "format": "markdown", "ingested_at": 200}
    ]


def test_corpus_sources_endpoint_returns_manuals_and_codebase_only(monkeypatch):
    _patch_stores(
        monkeypatch,
        manuals=[{"source": "manual.pdf", "type": "user_manual", "format": "pdf", "ingested_at": 1}],
        codebase=[{"source": "Widget.java", "type": "class", "format": None, "ingested_at": 2}],
    )

    resp = client.get("/api/corpus/sources", headers=_auth_headers())

    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"manuals", "codebase"}
    assert body["manuals"][0]["source"] == "manual.pdf"
    assert body["codebase"][0]["source"] == "Widget.java"


def test_corpus_sources_endpoint_requires_auth():
    resp = client.get("/api/corpus/sources")

    assert resp.status_code == 401


def test_delete_source_removes_manuals_chunks_only(monkeypatch):
    stores = _patch_stores(
        monkeypatch,
        manuals=[{"source": "manual.pdf", "type": "user_manual", "format": "pdf", "ingested_at": 1}],
        codebase=[],
    )

    resp = client.post(
        "/api/corpus/sources/delete",
        json={"collection_key": "manuals", "source": "manual.pdf"},
        headers=_auth_headers("admin"),
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted"}
    assert stores["manuals"].delete_calls == [{"source": "manual.pdf"}]
    assert stores["entities"].delete_calls == []


def test_delete_source_for_codebase_also_clears_entities(monkeypatch):
    stores = _patch_stores(
        monkeypatch,
        manuals=[],
        codebase=[{"source": "Widget.java", "type": "class", "format": None, "ingested_at": 1}],
    )

    resp = client.post(
        "/api/corpus/sources/delete",
        json={"collection_key": "codebase", "source": "Widget.java"},
        headers=_auth_headers("admin"),
    )

    assert resp.status_code == 200
    assert stores["codebase"].delete_calls == [{"source": "Widget.java"}]
    assert stores["entities"].delete_calls == [{"source": "Widget.java"}]


def test_delete_source_rejects_entities_as_a_direct_target(monkeypatch):
    _patch_stores(monkeypatch, manuals=[], codebase=[])

    resp = client.post(
        "/api/corpus/sources/delete",
        json={"collection_key": "entities", "source": "Widget.java"},
        headers=_auth_headers("admin"),
    )

    assert resp.status_code == 422


def test_delete_source_is_idempotent_for_a_never_ingested_source(monkeypatch):
    _patch_stores(monkeypatch, manuals=[], codebase=[])

    resp = client.post(
        "/api/corpus/sources/delete",
        json={"collection_key": "manuals", "source": "does-not-exist.pdf"},
        headers=_auth_headers("admin"),
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted"}


def test_delete_source_requires_admin(monkeypatch):
    _patch_stores(monkeypatch, manuals=[{"source": "manual.pdf"}], codebase=[])

    resp = client.post(
        "/api/corpus/sources/delete",
        json={"collection_key": "manuals", "source": "manual.pdf"},
        headers=_auth_headers("user"),
    )

    assert resp.status_code == 403


def test_delete_source_requires_auth():
    resp = client.post("/api/corpus/sources/delete", json={"collection_key": "manuals", "source": "manual.pdf"})

    assert resp.status_code == 401
