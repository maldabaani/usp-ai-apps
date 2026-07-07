"""Optional LLM-summary enrichment tier for document ingestion: for each
eligible document, extracts a per-document business-rule summary (reusing
the same Claude/Ollama agent classes ingestion/enrichment/enrich.py uses for
code, but pointed at doc_prompts.py's document-oriented prompt instead via
selector.build_agents()'s ``build_messages`` override) and embeds it into the
``sf_user_manuals`` Chroma collection as a "llm_summary"-typed document,
alongside the raw text chunks ingest_documents.py's mechanical tier already
produces for that same file.

See plan file section Q. Mirrors enrich.py's shape closely (semaphore-bounded
fan-out, manifest-based incremental skip, per-file status records, graceful
degrade-if-no-agents, same progress_callback contract) but differs in a few
ways specific to documents:

- Reads content via ingest_documents._extract_text() (handles PDF/DOCX),
  not plain path.read_text() -- these are binary formats.
- No filter.py-equivalent skip-reason module: the only skip is "no text could
  be extracted", mirroring ingest_documents.py's own mechanical-tier handling
  of the same condition.
- Oversized-document splitting reuses ingest_documents.py's own
  RecursiveCharacterTextSplitter-based ``_splitter`` (sized for safe prose
  boundaries), not chunker.py's brace/string-aware code splitter, which
  solves a different problem (safe code-block boundaries).
- Writes into "manuals" (not "codebase"), with format/ingested_at metadata
  but no code-only module/language/layer/class_name placeholder fields --
  no reason to carry meaningless stubs into new code.
- Uses its own manifest namespace (.document-enrichment-manifests/),
  independent from both ingest_code.py's .chunking-manifests/ and enrich.py's
  own .enrichment-manifests/.

Note: ingest_documents.py imports this module lazily (inside its
ingest_documents() function, not at module top) to avoid a circular import,
since this module in turn imports helpers from ingest_documents.py at its own
top level.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from pathlib import Path

from langchain_core.documents import Document

from config import settings
from ingestion import chroma_client, manifest
from ingestion.enrichment import doc_prompts
from ingestion.enrichment.agents.selector import AgentSelector, build_agents
from ingestion.enrichment.models import SourceFile
from ingestion.ingest_documents import _FORMAT_BY_SUFFIX, _extract_text, _splitter

logger = logging.getLogger(__name__)

DOC_TYPE = "llm_summary"
DEFAULT_MAX_CONCURRENCY = 8
# A document summarization call is sized in raw characters, not lines (unlike
# enrich.py's code-oriented MAX_LINES_BEFORE_SPLITTING), since prose has no
# meaningful line-length convention -- above this many characters, a document
# is split via ingest_documents.py's own text splitter before summarizing so
# a single extraction call doesn't have to swallow an oversized document whole.
MAX_CHARS_BEFORE_SPLITTING = 20_000


def _document_id(relative_path: str) -> str:
    key = f"{relative_path}::{DOC_TYPE}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:40]


async def enrich_documents(
    folder: Path,
    doc_paths: list[Path],
    *,
    enabled: bool,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    manifests_root: Path | None = None,
    progress_callback=None,
) -> dict:
    """Runs tier-2 LLM-summary enrichment across every document in
    `doc_paths` (already filtered to supported extensions by
    ingest_documents.py's own folder walk). Returns a result dict shaped like
    ingest_documents.py's own return value, for the caller to fold into its
    overall stats."""
    if not enabled:
        return {
            "enabled": False,
            "files_summarized": 0,
            "files_skipped_unchanged": 0,
            "errors": [],
            "files": [
                {"path": str(path.relative_to(folder)), "status": "skipped", "reason": "llm_summary_disabled"}
                for path in doc_paths
            ],
        }

    agents = build_agents(build_messages=doc_prompts.build_extraction_messages)
    if not agents:
        logger.warning(
            "LLM-summary enrichment is enabled but no agents are configured "
            "(no ANTHROPIC_API_KEY, INGEST_OLLAMA_ENABLED off) -- skipping "
            "tier 2 for this run; raw chunking (tier 1) is unaffected."
        )
        return {
            "enabled": False,
            "files_summarized": 0,
            "files_skipped_unchanged": 0,
            "errors": [],
            "files": [
                {"path": str(path.relative_to(folder)), "status": "skipped", "reason": "no_agents_configured"}
                for path in doc_paths
            ],
        }

    selector = AgentSelector(agents)
    manuals_store = chroma_client.get_vector_store("manuals")

    manifests_root = manifests_root or (Path(settings.JOBS_DIR) / ".document-enrichment-manifests")
    previous_hashes = manifest.load(manifests_root, folder) or {}
    current_hashes: dict[str, str] = {}

    semaphore = asyncio.Semaphore(max_concurrency)
    files_summarized = 0
    files_skipped_unchanged = 0
    errors: list[str] = []
    file_records: list[dict] = []

    async def process_one(path: Path) -> None:
        nonlocal files_summarized, files_skipped_unchanged
        relative_path = str(path.relative_to(folder))
        digest = manifest.compute_hash(path)
        if digest is not None and previous_hashes.get(relative_path) == digest:
            current_hashes[relative_path] = digest
            files_skipped_unchanged += 1
            file_records.append({"path": relative_path, "status": "skipped", "reason": "unchanged_since_last_run"})
            return

        async with semaphore:
            try:
                text = _extract_text(path)
                if not text.strip():
                    file_records.append(
                        {"path": relative_path, "status": "skipped", "reason": "no_content_extracted"}
                    )
                    return

                if len(text) <= MAX_CHARS_BEFORE_SPLITTING:
                    parts: list[SourceFile] = [SourceFile(path, relative_path, text, len(text.encode("utf-8")))]
                else:
                    parts = [
                        SourceFile(path, relative_path, part_text, len(part_text.encode("utf-8")))
                        for part_text in _splitter.split_text(text)
                    ]

                summaries: list[str] = []
                for part in parts:
                    agent = selector.next()
                    result = await agent.extract(part)
                    if result.success and not result.skipped and result.content:
                        summaries.append(result.content)

                if not summaries:
                    file_records.append({"path": relative_path, "status": "skipped", "reason": "no_summary_produced"})
                    return

                combined = "\n\n---\n\n".join(summaries) if len(summaries) > 1 else summaries[0]
                document = Document(
                    page_content=f"Document: {relative_path}\n\n{combined}",
                    metadata={
                        "source": relative_path,
                        "type": DOC_TYPE,
                        "format": _FORMAT_BY_SUFFIX.get(path.suffix.lower(), "unknown"),
                        "ingested_at": time.time(),
                    },
                )
                await chroma_client.delete_by_source_and_type("manuals", relative_path, DOC_TYPE)
                await manuals_store.aadd_documents([document], ids=[_document_id(relative_path)])
                files_summarized += 1
                if digest is not None:
                    current_hashes[relative_path] = digest
                file_records.append({"path": relative_path, "status": "summarized"})
            except Exception as exc:  # noqa: BLE001 - per-file isolation
                logger.exception("Enrichment failed for %s", relative_path)
                errors.append(f"{relative_path}: {exc}")
                file_records.append({"path": relative_path, "status": "error", "reason": str(exc)})

    done_count = 0

    async def process_and_report(path: Path) -> None:
        nonlocal done_count
        await process_one(path)
        # asyncio is single-threaded cooperative scheduling: done_count += 1
        # and file_records.append(...) (inside process_one) are both
        # synchronous, non-awaiting statements, so no two of these
        # semaphore-gated concurrent tasks can interleave mid-update -- same
        # guarantee enrich.py's own counters rely on. No asyncio.Lock needed.
        done_count += 1
        if progress_callback:
            await progress_callback(
                done_count,
                len(doc_paths),
                phase="enrichment",
                partial_result={"enrichment_files": list(file_records)},
            )

    await asyncio.gather(*[process_and_report(path) for path in doc_paths])

    manifest.save(manifests_root, folder, current_hashes)
    file_records.sort(key=lambda record: record["path"])

    return {
        "enabled": True,
        "files_summarized": files_summarized,
        "files_skipped_unchanged": files_skipped_unchanged,
        "errors": errors,
        "files": file_records,
    }
