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
Bounded by asyncio.Semaphore(max_concurrency) at both the file level and
(for an oversized file split into multiple chunker.py parts) the individual
part level, so a single huge file's parts run concurrently instead of
strictly one-at-a-time -- see process_one()'s extract_part(). Incremental
re-runs skip re-summarizing files whose
content hasn't changed since the last run, via a per-repo content-hash
manifest (see manifest.py) -- tier 1's mechanical chunking always re-runs
(it's cheap); only tier 2's LLM cost is worth skipping.

For a multi-part file specifically, every part that succeeds is also
persisted immediately (see part_progress.py), independent of the
whole-file manifest above. This is what makes an interrupted run of a
single huge file resumable instead of a total loss: if a run stops partway
through (a cancelled job, a crashed process, or -- the motivating case --
Anthropic API credits running out after 200 of 412 parts), the next run
loads whatever parts already succeeded and only pays to re-attempt the
rest, rather than re-summarizing (and re-billing for) the whole file from
scratch. A file only earns its whole-file manifest entry -- and only gets
its combined summary written to Chroma -- once *every* part has succeeded;
a run that ends with some parts still missing reports an "error" status
naming how many parts are done so far and leaves the manifest untouched,
so the file is retried (resuming, not restarting) on the next run.

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
from ingestion import chroma_client, manifest
from ingestion.enrichment import chunker, filter as enrichment_filter, part_progress
from ingestion.enrichment.agents.selector import AgentSelector, build_agents
from ingestion.enrichment.models import Language, SourceFile

logger = logging.getLogger(__name__)

# A separate reference to time.monotonic(), so tests can patch just this
# module's notion of elapsed time (for deterministic enrichment_eta_seconds
# assertions) without also patching the real time.monotonic() that
# asyncio's own event loop relies on internally for scheduling -- patching
# `time.monotonic` itself broke asyncio's own timing and crashed the loop.
_monotonic = time.monotonic

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
    progress_root: Path | None = None,
    progress_callback=None,
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
    progress_root = progress_root or (Path(settings.JOBS_DIR) / ".enrichment-part-progress")
    previous_hashes = manifest.load(manifests_root, repo) or {}
    current_hashes: dict[str, str] = {}

    def _count_parts(path: Path) -> int:
        """How many separate LLM calls this file will need: 0 for a file
        that process_one() below will skip entirely (manifest-unchanged or
        filter-excluded), 1 for a normal file, or however many parts
        chunker.py splits an oversized file into. Mirrors process_one()'s
        own skip/split decisions (duplicated rather than shared -- both are
        cheap one-liners, not worth threading a shared helper through). Run
        for every file upfront (below) so enrichment_percent/eta_seconds
        have an exact total from the start instead of guessing at
        not-yet-started files' sizes from an average of files seen so far
        (which can make the percentage jump backwards if a later file turns
        out much bigger than the running average predicted).
        """
        relative_path = str(path.relative_to(repo))
        digest = manifest.compute_hash(path)
        if digest is not None and previous_hashes.get(relative_path) == digest:
            return 0
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return 0
        source_file = SourceFile(path, relative_path, content, len(content.encode("utf-8")))
        if enrichment_filter.skip_reason(source_file):
            return 0
        if content.count("\n") + 1 > MAX_LINES_BEFORE_SPLITTING:
            return len(chunker.chunk(path, relative_path, content, MAX_LINES_PER_CHUNK))
        return 1

    total_parts_by_file = {str(path.relative_to(repo)): _count_parts(path) for path in files}
    total_parts = sum(total_parts_by_file.values())
    run_started_at = _monotonic()

    semaphore = asyncio.Semaphore(max_concurrency)
    # Separate from `semaphore` above (which bounds concurrent *files*): this
    # bounds concurrent LLM calls across every part of every file, so a
    # single file split into hundreds of chunker.py parts still gets real
    # concurrency instead of running its parts strictly one-at-a-time --
    # `semaphore` alone never helps there, since there's only one file to
    # admit. Both share max_concurrency as their limit; see process_one()'s
    # extract_part() for how they nest.
    llm_semaphore = asyncio.Semaphore(max_concurrency)
    files_summarized = 0
    files_skipped_unchanged = 0
    errors: list[str] = []
    file_records: list[dict] = []
    # Files currently being summarized, keyed by relative path -> {"done":
    # parts already completed, "total": parts this file was split into (1
    # for a file small enough not to need chunker.py's split)}. Reported to
    # progress_callback alongside file_records (never merged into it: the
    # final returned "files" list must only ever contain terminal statuses),
    # so a long-running single-file enrichment shows live, quantified
    # activity instead of the phase label going silent -- and a percentage
    # that actually moves as parts complete -- from the moment tier 2 starts
    # until its first (and maybe only) file finishes.
    in_progress: dict[str, dict] = {}

    async def _report_progress(done: int) -> None:
        if not progress_callback:
            return
        combined = list(file_records)
        for path, progress in in_progress.items():
            file_done_parts, file_total_parts = progress["done"], progress["total"]
            if file_total_parts > 1:
                percent = round(file_done_parts / file_total_parts * 100)
                reason = f"summarizing part {file_done_parts + 1}/{file_total_parts} ({percent}%)"
            else:
                reason = "summarizing"
            combined.append({"path": path, "status": "in_progress", "reason": reason})

        # Exact parts-done count: full credit for every file that's reached
        # a terminal status (file_records), partial credit for parts already
        # completed within any file still in flight. This is the real unit
        # of LLM-call work, not a proxy -- unlike files, parts are uniform
        # cost, so this percentage moves smoothly even for a single huge
        # file split into many parts.
        done_parts = sum(total_parts_by_file.get(record["path"], 0) for record in file_records) + sum(
            progress["done"] for progress in in_progress.values()
        )
        percent = round((done_parts / total_parts) * 100, 1) if total_parts else 100.0

        elapsed = _monotonic() - run_started_at
        eta_seconds = None
        if done_parts > 0 and elapsed > 0:
            rate = done_parts / elapsed  # parts per second, observed so far
            remaining_parts = max(total_parts - done_parts, 0)
            eta_seconds = round(remaining_parts / rate)

        await progress_callback(
            done,
            len(files),
            phase="enrichment",
            partial_result={
                "enrichment_files": combined,
                "enrichment_percent": percent,
                "enrichment_eta_seconds": eta_seconds,
            },
        )

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

                async def extract_part(part: SourceFile) -> str | None:
                    # Gated by llm_semaphore (not `semaphore` above, which
                    # only bounds concurrent files) -- this is what lets a
                    # single file's many parts run at once instead of
                    # strictly one-at-a-time.
                    async with llm_semaphore:
                        agent = selector.next()
                        result = await agent.extract(part)
                    return result.content if (result.success and not result.skipped and result.content) else None

                if len(parts) == 1:
                    # Common case (most files never need chunker.py's
                    # split): await directly, no Task/gather overhead, and
                    # no part_progress bookkeeping -- a single-part file
                    # either succeeds or doesn't, so there's nothing
                    # meaningful to resume (a retry next run is identical
                    # in cost to a resume).
                    in_progress[relative_path] = {"done": 0, "total": 1}
                    await _report_progress(done_count)
                    extracted = await extract_part(parts[0])
                    summaries = [extracted] if extracted else []
                else:
                    # Multi-part file: resume from whatever parts already
                    # succeeded in a prior, interrupted run of this exact
                    # file content (persisted incrementally below) instead
                    # of re-attempting -- and re-paying for -- them. Empty
                    # if there's no saved progress, the content changed, or
                    # this file was re-split into a different part count.
                    completed: dict[int, str] = (
                        part_progress.load(progress_root, repo, relative_path, digest, len(parts))
                        if digest is not None
                        else {}
                    )
                    results: list[str | None] = [completed.get(i) for i in range(len(parts))]
                    pending = [i for i in range(len(parts)) if results[i] is None]
                    parts_done = len(parts) - len(pending)

                    in_progress[relative_path] = {"done": parts_done, "total": len(parts)}
                    await _report_progress(done_count)

                    async def process_part(index: int, part: SourceFile) -> None:
                        nonlocal parts_done
                        text = await extract_part(part)
                        if text:
                            results[index] = text
                            completed[index] = text
                            if digest is not None:
                                # Saved after every success (not just at the
                                # end) so a crash, cancellation, or a
                                # mid-run credit exhaustion loses at most
                                # the parts still in flight, never the ones
                                # already done.
                                part_progress.save(progress_root, repo, relative_path, digest, len(parts), completed)
                        # asyncio's single-threaded cooperative scheduling
                        # means this increment and the in_progress update
                        # below can't interleave with another gathered
                        # part's -- same guarantee process_and_report's
                        # done_count already relies on.
                        parts_done += 1
                        in_progress[relative_path] = {"done": parts_done, "total": len(parts)}
                        await _report_progress(done_count)

                    await asyncio.gather(*[process_part(i, parts[i]) for i in pending])

                    if any(text is None for text in results):
                        done_now = sum(1 for text in results if text)
                        message = (
                            f"{done_now}/{len(parts)} parts summarized so far; the rest failed "
                            "this run (e.g. a rate limit or exhausted API credits) -- already-"
                            "completed parts are saved and will be skipped (not re-billed) on "
                            "the next run"
                        )
                        errors.append(f"{relative_path}: {message}")
                        file_records.append({"path": relative_path, "status": "error", "reason": message})
                        return

                    summaries = [text for text in results if text]

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
                if len(parts) > 1:
                    part_progress.clear(progress_root, repo, relative_path)
                file_records.append({"path": relative_path, "status": "summarized"})
            except Exception as exc:  # noqa: BLE001 - per-file isolation
                logger.exception("Enrichment failed for %s", relative_path)
                errors.append(f"{relative_path}: {exc}")
                file_records.append({"path": relative_path, "status": "error", "reason": str(exc)})
            finally:
                # Cleared unconditionally (pop with a default is a no-op if
                # it was never added, e.g. the unchanged-since-last-run skip
                # above returns before ever setting it) so a finished file
                # never lingers as "in_progress" in the next reported tick.
                in_progress.pop(relative_path, None)

    done_count = 0

    async def process_and_report(path: Path) -> None:
        nonlocal done_count
        await process_one(path)
        # asyncio is single-threaded cooperative scheduling: done_count += 1
        # and file_records.append(...) (inside process_one) are both
        # synchronous, non-awaiting statements, so no two of these
        # semaphore-gated concurrent tasks can interleave mid-update -- same
        # guarantee files_summarized/files_skipped_unchanged above already
        # rely on. No asyncio.Lock needed.
        done_count += 1
        await _report_progress(done_count)

    await asyncio.gather(*[process_and_report(path) for path in files])

    manifest.save(manifests_root, repo, current_hashes)
    file_records.sort(key=lambda record: record["path"])

    return {
        "enabled": True,
        "files_summarized": files_summarized,
        "files_skipped_unchanged": files_skipped_unchanged,
        "errors": errors,
        "files": file_records,
    }
