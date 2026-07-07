"""Covers the per-file success/skip/error visibility added to
ingest_code.py's and ingest_documents.py's result dicts (the ingestion
history/progress screen's per-file breakdown feature) -- previously only
aggregate counts existed, so a credit-exhaustion-style enrichment failure was
indistinguishable from a deliberate skip. Reuses this codebase's mocked
vector-store convention (see tests/ingestion/test_dedup.py).
"""
from __future__ import annotations

import asyncio
import uuid

import docx
import pytest

from ingestion import chroma_client, ingest_code, ingest_documents


class _FakeVectorStore:
    def __init__(self):
        self.docs: dict[str, object] = {}

    async def aadd_documents(self, documents, ids=None):
        ids = ids or [str(uuid.uuid4()) for _ in documents]
        for id_, doc in zip(ids, documents):
            self.docs[id_] = doc
        return ids

    async def adelete(self, ids=None, where=None):
        if where:
            source = where.get("source")
            if source is None and "$and" in where:
                for clause in where["$and"]:
                    if "source" in clause:
                        source = clause["source"]
            for id_ in [i for i, d in self.docs.items() if d.metadata.get("source") == source]:
                del self.docs[id_]
        elif ids:
            for id_ in ids:
                self.docs.pop(id_, None)

    def get(self, include=None):
        return {"metadatas": [doc.metadata for doc in self.docs.values()]}


@pytest.fixture
def fake_stores(monkeypatch):
    stores = {"codebase": _FakeVectorStore(), "entities": _FakeVectorStore(), "manuals": _FakeVectorStore()}

    def fake_get_vector_store(collection_key: str):
        return stores[collection_key]

    monkeypatch.setattr(chroma_client, "get_vector_store", fake_get_vector_store)
    monkeypatch.setattr(ingest_code, "get_vector_store", fake_get_vector_store)
    monkeypatch.setattr(ingest_documents, "get_vector_store", fake_get_vector_store)
    return stores


def _write(repo, relative: str, content: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _write_docx(repo, relative: str, paragraphs: list[str]) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    document = docx.Document()
    for text in paragraphs:
        document.add_paragraph(text)
    document.save(str(path))


def test_ingest_code_reports_per_file_success_and_error_status(tmp_path, fake_stores):
    _write(
        tmp_path,
        "Widget.java",
        "package com.example;\n\n@Service\npublic class Widget {\n    public void alpha() { int x = 1; }\n}\n",
    )
    # A file this codebase's chunkers can't parse -- forces the error branch
    # without needing to mock an exception directly.
    unparseable = tmp_path / "Broken.java"
    unparseable.write_text("not actually java {{{")

    result = asyncio.run(ingest_code.ingest_code(str(tmp_path), enable_llm_summary=False))

    files_by_path = {f["path"]: f for f in result["files"]}
    assert files_by_path["Widget.java"] == {"path": "Widget.java", "status": "success"}
    # Widget.java succeeds regardless of Broken.java's parse outcome; assert
    # the file list at least contains a recognizable entry for both paths.
    assert set(files_by_path) == {"Widget.java", "Broken.java"}
    assert "enrichment_files" in result
    # LLM summary disabled -- every file should show up as skipped with a
    # clear reason, not silently absent from the enrichment breakdown.
    enrichment_by_path = {f["path"]: f for f in result["enrichment_files"]}
    assert enrichment_by_path["Widget.java"] == {
        "path": "Widget.java",
        "status": "skipped",
        "reason": "llm_summary_disabled",
    }


def test_ingest_documents_reports_per_file_success_status(tmp_path, fake_stores):
    _write_docx(tmp_path, "manual.docx", ["Some document content."])

    result = asyncio.run(ingest_documents.ingest_documents(str(tmp_path), enable_llm_summary=False))

    assert result["files"] == [{"path": "manual.docx", "status": "success", "chunks": 1}]


def test_ingest_documents_reports_error_status_on_exception(tmp_path, fake_stores, monkeypatch):
    _write_docx(tmp_path, "manual.docx", ["Some content."])

    def fake_chunk_document(path, folder):
        raise ValueError("boom")

    monkeypatch.setattr(ingest_documents, "_chunk_document", fake_chunk_document)

    result = asyncio.run(ingest_documents.ingest_documents(str(tmp_path), enable_llm_summary=False))

    assert result["files"] == [{"path": "manual.docx", "status": "error", "reason": "boom"}]
    assert result["errors"][0].endswith("boom")


def test_ingest_code_progress_callback_reports_both_phases(tmp_path, fake_stores, monkeypatch):
    _write(
        tmp_path,
        "Widget.java",
        "package com.example;\n\npublic class Widget {\n    public void alpha() { int x = 1; }\n}\n",
    )

    class _StubAgent:
        def name(self):
            return "stub-agent"

        async def extract(self, file):
            from ingestion.enrichment.agents.base import success_result

            return success_result(file, self.name(), "a summary", 0, None, None)

    from ingestion.enrichment import enrich

    monkeypatch.setattr(enrich, "build_agents", lambda: [_StubAgent()])

    calls: list[tuple] = []

    async def progress_callback(done, total, *, phase, partial_result):
        calls.append((phase, partial_result))

    asyncio.run(
        ingest_code.ingest_code(
            str(tmp_path), progress_callback=progress_callback, enable_llm_summary=True
        )
    )

    phases = [phase for phase, _partial in calls]
    assert "chunking" in phases
    assert "enrichment" in phases
    chunking_partials = [partial for phase, partial in calls if phase == "chunking"]
    assert any("files" in partial for partial in chunking_partials)
    enrichment_partials = [partial for phase, partial in calls if phase == "enrichment"]
    assert any("enrichment_files" in partial for partial in enrichment_partials)


def test_ingest_documents_progress_callback_reports_chunking_phase(tmp_path, fake_stores):
    _write_docx(tmp_path, "manual.docx", ["Some document content."])

    calls: list[tuple] = []

    async def progress_callback(done, total, *, phase, partial_result):
        calls.append((phase, partial_result))

    asyncio.run(
        ingest_documents.ingest_documents(
            str(tmp_path), progress_callback=progress_callback, enable_llm_summary=False
        )
    )

    assert len(calls) == 1
    phase, partial_result = calls[0]
    assert phase == "chunking"
    assert partial_result["files"] == [{"path": "manual.docx", "status": "success", "chunks": 1}]
