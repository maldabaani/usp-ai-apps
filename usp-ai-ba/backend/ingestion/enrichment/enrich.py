"""Optional LLM-summary enrichment tier for ingestion: for each eligible
source file, extracts a per-file business-logic summary (reusing CodeMind's
already-tuned per-file agents/prompts, moved into this package -- see
agents/, prompts.py) and embeds it into the ``sf_codebase`` Chroma collection
as a "llm_summary"-typed document, alongside the raw structural chunks
ingest_code.py's mechanical chunkers already produce for that same file.

This is what's meant to compensate for the 13 languages that only get
whole-file raw chunking (see ingest_code.py's docstring) -- an LLM-synthesized
summary is a reasonable substitute for symbol-level chunking precision, and
is added for Java/TS/JS files too (which already have precise raw chunks)
since the summary captures business-logic reasoning a raw code chunk doesn't.

Gated by settings.INGEST_LLM_SUMMARY_ENABLED (default on) plus a per-request
override; degrades gracefully (skips the whole tier with a logged warning,
never raises) when zero agents are configured -- unlike AgentSelector, which
still raises for callers that genuinely require at least one agent to exist.
Bounded by asyncio.Semaphore(max_concurrency), matching CodeMind's original
per-file fan-out pattern. Incremental re-runs skip re-summarizing files whose
content hasn't changed since the last run, via a per-repo content-hash
manifest (see manifest.py) -- tier 1's mechanical chunking always re-runs
(it's cheap); only tier 2's LLM cost is worth skipping.

The returned dict's "files" list gives per-file visibility into every
outcome this tier can produce: "summarized", or "skipped" (with a reason of
"unchanged_since_last_run", one of filter.py's skip_reason() strings, or
"no_summary_produced"), or "error" (with the exception message) -- surfaced
end-to-end through ingest_code.py's own result, the job registries, and the
ingestion screen's per-file breakdown, so a credit-exhaustion-style failure
(previously indistinguishable from a deliberate skip) is now visible by name.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from pathlib import Path

from langchain_core.documents import Document

from config import settings
from ingestion import chroma_client
from ingestion.enrichment import chunker, filter as enrichment_filter, manifest
from ingestion.enrichment.agents.selector import AgentSelector, build_agents
from ingestion.enrichment.models import Language, SourceFile

logger = logging.getLogger(__name__)

DOC_TYPE = "llm_summary"
DEFAULT_MAX_CONCURRENCY = 8
# Matches codemind/orchestrator.py's MAX_LINES_PER_CHUNK -- above this many
# lines, a file is split via chunker.py before summarizing so a single
# extraction call doesn't have to swallow an oversized file whole.
MAX_LINES_PER_CHUNK = 400
MAX_LINES_BEFORE_SPLITTING = 400


def _document_id(relative_path: str) -> str:
    key = f"{relative_path}::{DOC_TYPE}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:40]


def _module(relative_path: str) -> str:
    parts = relative_path.split("/")
    return parts[0] if parts else "root"


async def enrich_repository(
    repo: Path,
    files: list[Path],
    *,
    enabled: bool,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    manifests_root: Path | None = None,
) -> dict:
    """Runs tier-2 LLM-summary enrichment across every file in `files`
    (already filtered to eligible extensions by ingest_code.py's own file
    walk). Returns a result dict shaped like ingest_code.py's own return
    value, for the caller to fold into its overall stats."""
    if not enabled:
        return {
            "enabled": False,
            "files_summarized": 0,
            "files_skipped_unchanged": 0,
            "errors": [],
            "files": [
                {"path": str(path.relative_to(repo)), "status": "skipped", "reason": "llm_summary_disabled"}
                for path in files
            ],
        }

    agents = build_agents()
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
                {"path": str(path.relative_to(repo)), "status": "skipped", "reason": "no_agents_configured"}
                for path in files
            ],
        }

    selector = AgentSelector(agents)
    codebase_store = chroma_client.get_vector_store("codebase")

    manifests_root = manifests_root or (Path(settings.JOBS_DIR) / ".enrichment-manifests")
    previous_hashes = manifest.load(manifests_root, repo) or {}
    current_hashes: dict[str, str] = {}

    semaphore = asyncio.Semaphore(max_concurrency)
    files_summarized = 0
    files_skipped_unchanged = 0
    errors: list[str] = []
    file_records: list[dict] = []

    async def process_one(path: Path) -> None:
        nonlocal files_summarized, files_skipped_unchanged
        relative_path = str(path.relative_to(repo))
        digest = manifest.compute_hash(path)
        if digest is not None and previous_hashes.get(relative_path) == digest:
            current_hashes[relative_path] = digest
            files_skipped_unchanged += 1
            file_records.append({"path": relative_path, "status": "skipped", "reason": "unchanged_since_last_run"})
            return

        async with semaphore:
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
                source_file = SourceFile(path, relative_path, content, len(content.encode("utf-8")))
                reason = enrichment_filter.skip_reason(source_file)
                if reason:
                    file_records.append({"path": relative_path, "status": "skipped", "reason": reason})
                    return

                parts: list[SourceFile] = [source_file]
                if content.count("\n") + 1 > MAX_LINES_BEFORE_SPLITTING:
                    parts = chunker.chunk(path, relative_path, content, MAX_LINES_PER_CHUNK)

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
                    page_content=f"File: {relative_path}\n\n{combined}",
                    metadata={
                        "source": relative_path,
                        "type": DOC_TYPE,
                        "module": _module(relative_path),
                        "language": Language.from_path(relative_path).code_fence,
                        "ingested_at": time.time(),
                    },
                )
                await chroma_client.delete_by_source_and_type("codebase", relative_path, DOC_TYPE)
                await codebase_store.aadd_documents([document], ids=[_document_id(relative_path)])
                files_summarized += 1
                if digest is not None:
                    current_hashes[relative_path] = digest
                file_records.append({"path": relative_path, "status": "summarized"})
            except Exception as exc:  # noqa: BLE001 - per-file isolation
                logger.exception("Enrichment failed for %s", relative_path)
                errors.append(f"{relative_path}: {exc}")
                file_records.append({"path": relative_path, "status": "error", "reason": str(exc)})

    await asyncio.gather(*[process_one(path) for path in files])

    manifest.save(manifests_root, repo, current_hashes)
    file_records.sort(key=lambda record: record["path"])

    return {
        "enabled": True,
        "files_summarized": files_summarized,
        "files_skipped_unchanged": files_skipped_unchanged,
        "errors": errors,
        "files": file_records,
    }
