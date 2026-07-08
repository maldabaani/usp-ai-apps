"""Covers ingest_code.py's widened language coverage -- folding in CodeMind's
broader 16-extension set (previously ingest_code.py only handled
.java/.ts/.js) as part of unifying ingestion into one pipeline (plan file
section I). Only .java/.ts/.js get symbol-level chunking; every other
extension falls through to chunk_generic_file's whole-file split.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from ingestion import chroma_client, ingest_code


class _FakeVectorStore:
    def __init__(self):
        self.docs: dict[str, object] = {}

    async def aadd_documents(self, documents, ids=None):
        ids = ids or [str(uuid.uuid4()) for _ in documents]
        for id_, doc in zip(ids, documents):
            self.docs[id_] = doc
        return ids

    async def adelete(self, ids=None, where=None):
        pass  # not exercised by these tests

    def get(self, include=None):
        return {"metadatas": [doc.metadata for doc in self.docs.values()]}


@pytest.fixture
def fake_stores(monkeypatch):
    stores = {"codebase": _FakeVectorStore(), "entities": _FakeVectorStore(), "manuals": _FakeVectorStore()}

    def fake_get_vector_store(collection_key: str):
        return stores[collection_key]

    monkeypatch.setattr(chroma_client, "get_vector_store", fake_get_vector_store)
    monkeypatch.setattr(ingest_code, "get_vector_store", fake_get_vector_store)
    return stores


def _write(repo, relative: str, content: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_all_sixteen_extensions_are_walked(tmp_path):
    expected = {
        ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",
        ".py", ".pyw", ".java", ".kt", ".kts",
        ".go", ".cs", ".rb", ".rs", ".php",
    }
    assert ingest_code.SOURCE_EXTENSIONS == expected


@pytest.mark.parametrize(
    "relative,expected_language",
    [
        ("main.py", "python"),
        ("script.pyw", "python"),
        ("Main.kt", "kotlin"),
        ("build.kts", "kotlin"),
        ("main.go", "go"),
        ("Program.cs", "csharp"),
        ("app.rb", "ruby"),
        ("lib.rs", "rust"),
        ("index.php", "php"),
    ],
)
def test_new_languages_fall_through_to_generic_whole_file_chunking(tmp_path, relative, expected_language):
    _write(tmp_path, relative, "some source content\nsecond line\n")

    result = ingest_code.chunk_file(tmp_path / relative, tmp_path)

    assert len(result.documents) == 1
    metadata = result.documents[0].metadata
    assert metadata["language"] == expected_language
    assert metadata["type"] == f"{expected_language}_file"
    assert metadata["source"] == relative
    assert not result.is_entity


def test_jsx_mjs_cjs_still_use_js_chunker(tmp_path):
    for relative in ("a.jsx", "b.mjs", "c.cjs"):
        _write(tmp_path, relative, "function alpha() {\n  return 1;\n}\n")
        result = ingest_code.chunk_file(tmp_path / relative, tmp_path)
        assert result.documents[0].metadata["type"] == "js_function"


def test_tsx_still_uses_ts_chunker(tmp_path):
    _write(
        tmp_path,
        "widget.tsx",
        "@Component({selector: 'app-widget'})\nexport class Widget {}\n",
    )
    result = ingest_code.chunk_file(tmp_path / "widget.tsx", tmp_path)
    assert result.documents[0].metadata["type"] == "angular_component"


def test_make_documents_drops_blank_pieces():
    # A blank/whitespace-only split piece would embed to zero real tokens,
    # which some Ollama/llama.cpp server versions reject as a 400 -- these
    # must never reach the embedding call at all.
    assert ingest_code._make_documents("   \n\n  ", {"source": "x"}) == []
    assert ingest_code._make_documents("", {"source": "x"}) == []


def test_make_documents_keeps_non_blank_overflow_pieces(monkeypatch):
    # A real overflow split can still legitimately produce more than one
    # piece; only genuinely blank ones are dropped, not real content.
    monkeypatch.setattr(ingest_code, "_split_overflow", lambda text: ["real content", "   ", "more content"])
    docs = ingest_code._make_documents("irrelevant, patched above", {"source": "x"})
    assert [doc.page_content for doc in docs] == ["real content", "more content"]


def test_excluded_dir_names_widened_to_match_codemind(tmp_path):
    expected = {
        "node_modules", ".git", "dist", "build", "coverage",
        "out", ".next", ".turbo", "vendor",
        "__pycache__", "target", ".venv", "venv",
        "bin", "obj", ".gradle", ".mypy_cache", ".pytest_cache",
        ".angular", ".cache",
    }
    assert ingest_code.SKIP_DIR_NAMES == expected


def test_ingest_code_indexes_a_python_file_end_to_end(tmp_path, fake_stores):
    _write(tmp_path, "app.py", "def handler():\n    return True\n")

    result = asyncio.run(ingest_code.ingest_code(str(tmp_path), enable_llm_summary=False))

    assert result["files_processed"] == 1
    assert result["chunks_indexed"] == 1
    sources = {doc.metadata["source"] for doc in fake_stores["codebase"].docs.values()}
    assert "app.py" in sources
