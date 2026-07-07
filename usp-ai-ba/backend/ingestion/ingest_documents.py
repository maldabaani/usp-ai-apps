"""One-time ingestion of user-facing documents -- PDF, Word (.docx), Markdown,
and Confluence page exports (.html/.htm) -- into the ``sf_user_manuals``
ChromaDB collection.

Confluence support means reading already-exported HTML/XML/Markdown files
dropped into the ingested folder, not a live Confluence API integration --
export a space to HTML (Confluence's built-in "Export to HTML" or "Export to
Word" action) and point this at the resulting folder like any other manuals
directory.
"""
from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

import docx
from bs4 import BeautifulSoup
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

from ingestion.chroma_client import delete_by_source, get_vector_store, list_distinct_sources

logger = logging.getLogger(__name__)

CHUNK_SIZE_TOKENS = 1500
CHUNK_OVERLAP_TOKENS = 150
# Approximate characters-per-token ratio used to translate the token-based
# limits in the spec into character-based limits for the text splitter.
CHARS_PER_TOKEN = 4

_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE_TOKENS * CHARS_PER_TOKEN,
    chunk_overlap=CHUNK_OVERLAP_TOKENS * CHARS_PER_TOKEN,
    separators=["\n\n", "\n", ". ", " ", ""],
)

# Suffix -> ("format" metadata tag, extractor). Checked via a dict keyed on
# path.suffix.lower() rather than a chain of if/elif branches.
_FORMAT_BY_SUFFIX = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".md": "markdown",
    ".markdown": "markdown",
    ".html": "html",
    ".htm": "html",
}
SUPPORTED_EXTENSIONS = frozenset(_FORMAT_BY_SUFFIX)

# Tags that never carry document body text (nav chrome, scripts, styling) --
# stripped before extracting text from an HTML/Confluence export so they
# don't pollute the chunked content with menu labels and JS/CSS source.
_HTML_NOISE_TAGS = ("script", "style", "nav", "header", "footer")


def _extract_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    pages_text = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if text.strip():
            pages_text.append(f"[Page {page_number}]\n{text}")
    return "\n\n".join(pages_text)


def _extract_docx_text(path: Path) -> str:
    document = docx.Document(str(path))
    parts = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _extract_markdown_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _extract_html_text(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(raw, "html.parser")
    for tag_name in _HTML_NOISE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()
    root = soup.body or soup
    return root.get_text(separator="\n", strip=True)


def _extract_text(path: Path) -> str:
    format_tag = _FORMAT_BY_SUFFIX[path.suffix.lower()]
    if format_tag == "pdf":
        return _extract_pdf_text(path)
    if format_tag == "docx":
        return _extract_docx_text(path)
    if format_tag == "markdown":
        return _extract_markdown_text(path)
    return _extract_html_text(path)


def _document_id(relative_source: str, chunk_index: int) -> str:
    """Deterministic per-chunk ID, matching ingest_code.py's fix for the same
    duplicate-on-rerun bug this module's own docstring used to document as a
    known limitation."""
    key = f"{relative_source}::{chunk_index}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:40]


def _chunk_document(path: Path, folder_path: Path) -> list[Document]:
    full_text = _extract_text(path)
    if not full_text.strip():
        logger.warning("No extractable text found in %s", path)
        return []

    relative_source = str(path.relative_to(folder_path))
    format_tag = _FORMAT_BY_SUFFIX[path.suffix.lower()]
    chunks = _splitter.split_text(full_text)
    ingested_at = time.time()
    return [
        Document(
            page_content=chunk,
            metadata={
                "source": relative_source,
                "type": "user_manual",
                "module": "N/A",
                "layer": "documentation",
                "class_name": "N/A",
                "language": "N/A",
                "chunk_index": index,
                "format": format_tag,
                "ingested_at": ingested_at,
            },
        )
        for index, chunk in enumerate(chunks)
    ]


async def ingest_documents(
    folder_path: str,
    progress_callback=None,
) -> dict:
    """Chunk and embed every supported document found (recursively) under
    ``folder_path`` -- PDF, Word (.docx), Markdown, and Confluence HTML/XML
    page exports (.html/.htm).

    Stores all chunks into the ``sf_user_manuals`` collection. Safe to re-run:
    deterministic per-chunk IDs make an unchanged file's re-add a no-op
    upsert, each file's prior chunk set is cleared before its fresh one is
    added (so a changed file's stale chunks don't linger), and a file removed
    from the folder since the last run has its chunks purged too (diffed
    against what the collection already has, since nothing else here
    persists prior runs).
    """
    folder = Path(folder_path)
    if not folder.is_dir():
        raise FileNotFoundError(f"Documents folder not found: {folder_path}")

    doc_paths = sorted(
        path for path in folder.rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    vector_store = get_vector_store("manuals")

    current_sources = {str(path.relative_to(folder)) for path in doc_paths}
    previously_seen = await list_distinct_sources("manuals")
    for removed_source in previously_seen - current_sources:
        await delete_by_source("manuals", removed_source)

    total_files = len(doc_paths)
    total_chunks = 0
    errors: list[str] = []
    file_records: list[dict] = []

    for index, doc_path in enumerate(doc_paths, start=1):
        relative_source = str(doc_path.relative_to(folder))
        try:
            documents = _chunk_document(doc_path, folder)
            if documents:
                await delete_by_source("manuals", relative_source)
                ids = [_document_id(relative_source, doc.metadata["chunk_index"]) for doc in documents]
                await vector_store.aadd_documents(documents, ids=ids)
                total_chunks += len(documents)
                file_records.append({"path": relative_source, "status": "success", "chunks": len(documents)})
            else:
                file_records.append({"path": relative_source, "status": "skipped", "reason": "no_content_extracted"})
        except Exception as exc:  # noqa: BLE001 - surfaced to caller via errors list
            logger.exception("Failed to ingest %s", doc_path)
            errors.append(f"{doc_path}: {exc}")
            file_records.append({"path": relative_source, "status": "error", "reason": str(exc)})

        if progress_callback:
            await progress_callback(
                index, total_files, phase="chunking", partial_result={"files": file_records.copy()}
            )

    return {
        "files_processed": total_files,
        "chunks_indexed": total_chunks,
        "errors": errors,
        "files": file_records,
    }
