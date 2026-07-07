"""Covers the dedup/staleness fix in ingest_code.py/ingest_documents.py:
previously, re-ingesting duplicated every chunk on every run (no ids= passed
to aadd_documents at all). Deterministic per-chunk IDs make an unchanged
chunk's re-add a no-op upsert; delete_by_source (called per file before its
fresh chunks are added) plus a whole-collection diff against current files
(list_distinct_sources) together clean up chunks whose method/file was
renamed, removed, or deleted from disk since the last run.

Chroma's real client isn't used here -- a fake vector store standing in for
`langchain_chroma.Chroma` is enough to exercise the add/delete/get contract
this fix depends on, matching this codebase's mocked-client testing
convention rather than requiring a real ChromaDB + Ollama embeddings model.
"""
from __future__ import annotations

import uuid

import pytest

from ingestion import chroma_client, ingest_code, ingest_documents


def _matches_where(metadata: dict, where: dict) -> bool:
    """Evaluates the small subset of chromadb's where-filter grammar this
    codebase's delete_by_source/delete_by_source_and_type/
    delete_by_source_excluding_type actually use: flat exact-match keys
    (implicitly ANDed), "$and": [...], and a field value of {"$ne": ...}."""
    for key, value in where.items():
        if key == "$and":
            if not all(_matches_where(metadata, clause) for clause in value):
                return False
        elif isinstance(value, dict) and "$ne" in value:
            if metadata.get(key) == value["$ne"]:
                return False
        else:
            if metadata.get(key) != value:
                return False
    return True


class _FakeVectorStore:
    def __init__(self):
        self.docs: dict[str, object] = {}  # id -> Document
        self.add_call_count = 0
        self.delete_call_count = 0

    async def aadd_documents(self, documents, ids=None):
        self.add_call_count += 1
        ids = ids or [str(uuid.uuid4()) for _ in documents]
        for id_, doc in zip(ids, documents):
            self.docs[id_] = doc
        return ids

    async def adelete(self, ids=None, where=None):
        self.delete_call_count += 1
        if where:
            for id_ in [i for i, doc in self.docs.items() if _matches_where(doc.metadata, where)]:
                del self.docs[id_]
        elif ids:
            for id_ in ids:
                self.docs.pop(id_, None)

    def get(self, include=None):
        return {"metadatas": [doc.metadata for doc in self.docs.values()]}


@pytest.fixture
def fake_stores(monkeypatch):
    stores: dict[str, _FakeVectorStore] = {"codebase": _FakeVectorStore(), "entities": _FakeVectorStore(), "manuals": _FakeVectorStore()}

    def fake_get_vector_store(collection_key: str):
        return stores[collection_key]

    # Two separate name bindings need patching: chroma_client's own module
    # global (which delete_by_source/list_distinct_sources resolve against
    # internally) and ingest_code.py's/ingest_documents.py's already-imported
    # reference (a `from ... import get_vector_store` copies the name at
    # import time, so patching chroma_client's copy alone wouldn't affect it).
    monkeypatch.setattr(chroma_client, "get_vector_store", fake_get_vector_store)
    monkeypatch.setattr(ingest_code, "get_vector_store", fake_get_vector_store)
    monkeypatch.setattr(ingest_documents, "get_vector_store", fake_get_vector_store)
    return stores


_JAVA_TWO_METHODS = """
package com.example;

@Service
public class Widget {
    public void alpha() {
        int x = 1;
    }

    public void beta() {
        int y = 2;
    }
}
"""

_JAVA_ONE_METHOD = """
package com.example;

@Service
public class Widget {
    public void alpha() {
        int x = 1;
    }
}
"""

_JAVA_CHANGED_BODY = """
package com.example;

@Service
public class Widget {
    public void alpha() {
        int x = 999;
    }

    public void beta() {
        int y = 2;
    }
}
"""


def _write(repo: object, relative: str, content: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


async def _ingest(repo_path) -> dict:
    # Tier 2 (LLM-summary enrichment) is out of scope for this file -- see
    # tests/ingestion/enrichment/test_enrich.py -- and disabling it here
    # keeps these tests from making real network calls.
    return await ingest_code.ingest_code(str(repo_path), enable_llm_summary=False)


def test_reingesting_unchanged_repo_does_not_grow_chunk_count(tmp_path, fake_stores):
    import asyncio

    _write(tmp_path, "Widget.java", _JAVA_TWO_METHODS)

    asyncio.run(_ingest(tmp_path))
    first_count = len(fake_stores["codebase"].docs)

    asyncio.run(_ingest(tmp_path))
    second_count = len(fake_stores["codebase"].docs)

    assert first_count > 0
    assert second_count == first_count


def test_changed_method_body_updates_in_place(tmp_path, fake_stores):
    import asyncio

    _write(tmp_path, "Widget.java", _JAVA_TWO_METHODS)
    asyncio.run(_ingest(tmp_path))
    first_count = len(fake_stores["codebase"].docs)

    _write(tmp_path, "Widget.java", _JAVA_CHANGED_BODY)
    asyncio.run(_ingest(tmp_path))

    assert len(fake_stores["codebase"].docs) == first_count
    contents = [doc.page_content for doc in fake_stores["codebase"].docs.values()]
    assert any("999" in c for c in contents)
    assert not any("int x = 1;" in c for c in contents)


def test_removed_method_deletes_its_stale_chunk(tmp_path, fake_stores):
    import asyncio

    _write(tmp_path, "Widget.java", _JAVA_TWO_METHODS)
    asyncio.run(_ingest(tmp_path))
    metadatas_before = [doc.metadata for doc in fake_stores["codebase"].docs.values()]
    assert any(m.get("method_name") == "beta" for m in metadatas_before)

    _write(tmp_path, "Widget.java", _JAVA_ONE_METHOD)
    asyncio.run(_ingest(tmp_path))

    metadatas_after = [doc.metadata for doc in fake_stores["codebase"].docs.values()]
    assert not any(m.get("method_name") == "beta" for m in metadatas_after)
    assert any(m.get("method_name") == "alpha" for m in metadatas_after)


def test_deleted_file_purges_its_chunks_from_codebase_and_entities(tmp_path, fake_stores):
    import asyncio

    _write(tmp_path, "Widget.java", _JAVA_TWO_METHODS)
    _write(
        tmp_path,
        "Thing.java",
        "package com.example;\n\n@Entity\npublic class Thing {\n    public void gamma() { int z = 3; }\n}\n",
    )
    asyncio.run(_ingest(tmp_path))
    assert any(doc.metadata.get("source") == "Thing.java" for doc in fake_stores["entities"].docs.values())

    (tmp_path / "Thing.java").unlink()
    asyncio.run(_ingest(tmp_path))

    remaining_codebase_sources = {doc.metadata.get("source") for doc in fake_stores["codebase"].docs.values()}
    remaining_entity_sources = {doc.metadata.get("source") for doc in fake_stores["entities"].docs.values()}
    assert "Thing.java" not in remaining_codebase_sources
    assert "Thing.java" not in remaining_entity_sources
    assert "Widget.java" in remaining_codebase_sources


def test_entity_annotation_removed_purges_entities_collection(tmp_path, fake_stores):
    import asyncio

    _write(
        tmp_path,
        "Thing.java",
        "package com.example;\n\n@Entity\npublic class Thing {\n    public void gamma() { int z = 3; }\n}\n",
    )
    asyncio.run(_ingest(tmp_path))
    assert len(fake_stores["entities"].docs) > 0

    _write(
        tmp_path,
        "Thing.java",
        "package com.example;\n\n@Service\npublic class Thing {\n    public void gamma() { int z = 3; }\n}\n",
    )
    asyncio.run(_ingest(tmp_path))

    assert len(fake_stores["entities"].docs) == 0


def test_pdf_reingesting_unchanged_folder_does_not_grow_chunk_count(tmp_path, fake_stores, monkeypatch):
    import asyncio

    from langchain_core.documents import Document

    def fake_chunk_document(doc_path, folder_path):
        relative_source = str(doc_path.relative_to(folder_path))
        return [
            Document(page_content="hello world", metadata={"source": relative_source, "chunk_index": 0}),
            Document(page_content="second chunk", metadata={"source": relative_source, "chunk_index": 1}),
        ]

    monkeypatch.setattr(ingest_documents, "_chunk_document", fake_chunk_document)
    (tmp_path / "manual.pdf").write_bytes(b"%PDF-1.4 fake")

    asyncio.run(ingest_documents.ingest_documents(str(tmp_path)))
    first_count = len(fake_stores["manuals"].docs)

    asyncio.run(ingest_documents.ingest_documents(str(tmp_path)))
    second_count = len(fake_stores["manuals"].docs)

    assert first_count == 2
    assert second_count == first_count


def test_pdf_deleted_from_folder_purges_its_chunks(tmp_path, fake_stores, monkeypatch):
    import asyncio

    from langchain_core.documents import Document

    def fake_chunk_document(doc_path, folder_path):
        relative_source = str(doc_path.relative_to(folder_path))
        return [Document(page_content="content", metadata={"source": relative_source, "chunk_index": 0})]

    monkeypatch.setattr(ingest_documents, "_chunk_document", fake_chunk_document)
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4 fake a")
    (tmp_path / "b.pdf").write_bytes(b"%PDF-1.4 fake b")

    asyncio.run(ingest_documents.ingest_documents(str(tmp_path)))
    assert len(fake_stores["manuals"].docs) == 2

    (tmp_path / "b.pdf").unlink()
    asyncio.run(ingest_documents.ingest_documents(str(tmp_path)))

    remaining_sources = {doc.metadata.get("source") for doc in fake_stores["manuals"].docs.values()}
    assert remaining_sources == {"a.pdf"}


def test_mixed_format_folder_reingesting_does_not_grow_chunk_count(tmp_path, fake_stores, monkeypatch):
    import asyncio

    from langchain_core.documents import Document

    def fake_chunk_document(doc_path, folder_path):
        relative_source = str(doc_path.relative_to(folder_path))
        return [Document(page_content="content", metadata={"source": relative_source, "chunk_index": 0})]

    monkeypatch.setattr(ingest_documents, "_chunk_document", fake_chunk_document)
    (tmp_path / "manual.pdf").write_bytes(b"%PDF-1.4 fake")
    (tmp_path / "spec.docx").write_bytes(b"fake docx bytes")

    asyncio.run(ingest_documents.ingest_documents(str(tmp_path)))
    first_count = len(fake_stores["manuals"].docs)
    sources = {doc.metadata.get("source") for doc in fake_stores["manuals"].docs.values()}

    asyncio.run(ingest_documents.ingest_documents(str(tmp_path)))
    second_count = len(fake_stores["manuals"].docs)

    assert first_count > 0
    assert second_count == first_count
    assert sources == {"manual.pdf", "spec.docx"}
