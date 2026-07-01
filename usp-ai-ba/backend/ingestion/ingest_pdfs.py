"""One-time ingestion of User Manual PDFs into the ``sf_user_manuals`` ChromaDB collection."""
from __future__ import annotations

import logging
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

from ingestion.chroma_client import get_vector_store

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

    Stores all chunks into the ``sf_user_manuals`` collection. This is intended
    to be run once; re-running re-adds documents (IDs are not deduplicated
    across runs).
    """
    folder = Path(folder_path)
    if not folder.is_dir():
        raise FileNotFoundError(f"PDF folder not found: {folder_path}")

    pdf_paths = sorted(folder.rglob("*.pdf"))
    vector_store = get_vector_store("manuals")

    total_files = len(pdf_paths)
    total_chunks = 0
    errors: list[str] = []

    for index, pdf_path in enumerate(pdf_paths, start=1):
        try:
            documents = _chunk_pdf(pdf_path, folder)
            if documents:
                await vector_store.aadd_documents(documents)
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
