"""Covers ingest_documents.py's Phase L-A widening from PDF-only to
PDF/Word (.docx)/Markdown/Confluence-HTML-export support: each format's text
extractor produces non-empty content, and a mixed-format folder ingestion
produces distinct sources tagged with the correct "format" metadata per file.

Uses a fake vector store standing in for `langchain_chroma.Chroma`, matching
this codebase's mocked-client testing convention (see tests/ingestion/test_dedup.py).
"""
from __future__ import annotations

import asyncio
import uuid

import docx
import pytest

from ingestion import chroma_client, ingest_documents


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
            for id_ in [i for i, doc in self.docs.items() if doc.metadata.get("source") == where.get("source")]:
                del self.docs[id_]
        elif ids:
            for id_ in ids:
                self.docs.pop(id_, None)

    def get(self, include=None):
        return {"metadatas": [doc.metadata for doc in self.docs.values()]}


@pytest.fixture
def fake_manuals_store(monkeypatch):
    store = _FakeVectorStore()

    def fake_get_vector_store(collection_key: str):
        return store

    monkeypatch.setattr(chroma_client, "get_vector_store", fake_get_vector_store)
    monkeypatch.setattr(ingest_documents, "get_vector_store", fake_get_vector_store)
    return store


def _write_docx(path, paragraphs: list[str]) -> None:
    document = docx.Document()
    for text in paragraphs:
        document.add_paragraph(text)
    document.save(str(path))


@pytest.mark.parametrize(
    "suffix,write_fn,expected_snippet",
    [
        (".md", lambda p: p.write_text("# Heading\n\nSome markdown body text.\n"), "markdown body text"),
        (
            ".html",
            lambda p: p.write_text(
                "<html><body><nav>Menu</nav><h1>Title</h1><p>Some html body text.</p></body></html>"
            ),
            "html body text",
        ),
    ],
)
def test_extract_text_produces_non_empty_content(tmp_path, suffix, write_fn, expected_snippet):
    path = tmp_path / f"doc{suffix}"
    write_fn(path)

    text = ingest_documents._extract_text(path)

    assert expected_snippet in text


def test_extract_docx_text_includes_paragraphs_and_table_cells(tmp_path):
    path = tmp_path / "doc.docx"
    document = docx.Document()
    document.add_paragraph("Some paragraph text.")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Key"
    table.rows[0].cells[1].text = "Value"
    document.save(str(path))

    text = ingest_documents._extract_docx_text(path)

    assert "Some paragraph text." in text
    assert "Key | Value" in text


def test_extract_html_text_strips_noise_tags(tmp_path):
    path = tmp_path / "page.html"
    path.write_text(
        "<html><body><nav>Nav link</nav><script>var x = 1;</script>"
        "<p>Real content.</p></body></html>"
    )

    text = ingest_documents._extract_html_text(path)

    assert "Real content." in text
    assert "Nav link" not in text
    assert "var x" not in text


def test_mixed_format_folder_ingestion_tags_correct_format_per_source(tmp_path, fake_manuals_store):
    (tmp_path / "manual.md").write_text("# Manual\n\nMarkdown manual content.\n")
    (tmp_path / "page.html").write_text("<html><body><p>Confluence export content.</p></body></html>")
    _write_docx(tmp_path / "spec.docx", ["Word document content."])

    result = asyncio.run(ingest_documents.ingest_documents(str(tmp_path)))

    assert result["files_processed"] == 3
    assert not result["errors"]

    formats_by_source = {
        doc.metadata["source"]: doc.metadata["format"] for doc in fake_manuals_store.docs.values()
    }
    assert formats_by_source == {
        "manual.md": "markdown",
        "page.html": "html",
        "spec.docx": "docx",
    }
