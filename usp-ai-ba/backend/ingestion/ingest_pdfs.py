"""One-time ingestion of User Manual PDFs into the ``sf_user_manuals`` ChromaDB collection."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

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


def _extract_pdf_text(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    pages_text = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if text.strip():
            pages_text.append(f"[Page {page_number}]\n{text}")
    return "\n\n".join(pages_text)


def _document_id(relative_source: str, chunk_index: int) -> str:
    """Deterministic per-chunk ID, matching ingest_code.py's fix for the same
    duplicate-on-rerun bug this module's own docstring used to document as a
    known limitation."""
    key = f"{relative_source}::{chunk_index}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:40]


def _chunk_pdf(pdf_path: Path, folder_path: Path) -> list[Document]:
    full_text = _extract_pdf_text(pdf_path)
    if not full_text.strip():
        logger.warning("No extractable text found in %s", pdf_path)
        return []

    relative_source = str(pdf_path.relative_to(folder_path))
    chunks = _splitter.split_text(full_text)
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
            },
        )
        for index, chunk in enumerate(chunks)
    ]


async def ingest_pdfs(
    folder_path: str,
    progress_callback=None,
) -> dict:
    """Chunk and embed every PDF found (recursively) under ``folder_path``.

    Stores all chunks into the ``sf_user_manuals`` collection. Safe to re-run:
    deterministic per-chunk IDs make an unchanged PDF's re-add a no-op upsert,
    each PDF's prior chunk set is cleared before its fresh one is added (so a
    changed PDF's stale chunks don't linger), and a PDF removed from the
    folder since the last run has its chunks purged too (diffed against what
    the collection already has, since nothing else here persists prior runs).
    """
    folder = Path(folder_path)
    if not folder.is_dir():
        raise FileNotFoundError(f"PDF folder not found: {folder_path}")

    pdf_paths = sorted(folder.rglob("*.pdf"))
    vector_store = get_vector_store("manuals")

    current_sources = {str(path.relative_to(folder)) for path in pdf_paths}
    previously_seen = await list_distinct_sources("manuals")
    for removed_source in previously_seen - current_sources:
        await delete_by_source("manuals", removed_source)

    total_files = len(pdf_paths)
    total_chunks = 0
    errors: list[str] = []

    for index, pdf_path in enumerate(pdf_paths, start=1):
        try:
            documents = _chunk_pdf(pdf_path, folder)
            if documents:
                relative_source = str(pdf_path.relative_to(folder))
                await delete_by_source("manuals", relative_source)
                ids = [_document_id(relative_source, doc.metadata["chunk_index"]) for doc in documents]
                await vector_store.aadd_documents(documents, ids=ids)
                total_chunks += len(documents)
        except Exception as exc:  # noqa: BLE001 - surfaced to caller via errors list
            logger.exception("Failed to ingest %s", pdf_path)
            errors.append(f"{pdf_path}: {exc}")

        if progress_callback:
            await progress_callback(index, total_files)

    return {
        "files_processed": total_files,
        "chunks_indexed": total_chunks,
        "errors": errors,
    }
