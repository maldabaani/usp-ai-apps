"""Shared job-running wrappers for document/code ingestion, used by both
api/routers/ingest.py's manual-trigger endpoints and ingestion/watcher.py's
auto-triggered runs -- one wrapper per ingestion kind, not duplicated per
caller, so a watcher-triggered run shows up in job status/history identically
to a manual one. This is also where the ingestion-generation cache-
invalidation counter (ingestion/ingestion_generation.py, backing
api/ask_cache.py's Phase L-F answer cache) gets bumped, in each function's
success branch only, right after finish_job() -- never in the except
branch, since a failed run didn't actually change the corpus.
"""
from __future__ import annotations

from api import ingest_jobs
from ingestion import ingest_job_registry, ingestion_generation
from ingestion.ingest_code import ingest_code
from ingestion.ingest_documents import ingest_documents


async def run_document_ingestion(
    job_id: str,
    folder_path: str,
    enable_llm_summary: bool | None = None,
    max_concurrency: int | None = None,
) -> None:
    async def on_progress(done: int, total: int, *, phase: str, partial_result: dict) -> None:
        ingest_jobs.update_progress(job_id, done, total)
        ingest_jobs.set_phase(job_id, phase)
        ingest_jobs.update_result(job_id, partial_result)

    try:
        result = await ingest_documents(
            folder_path,
            progress_callback=on_progress,
            enable_llm_summary=enable_llm_summary,
            max_concurrency=max_concurrency,
        )
        ingest_jobs.finish_job(job_id, result)
        ingestion_generation.bump()
        job = ingest_jobs.get_ingest_job(job_id)
        ingest_job_registry.record_completed_job(
            job_id, "documents", job["status"], result, result.get("errors", []), source_path=job["source_path"]
        )
    except Exception as exc:  # noqa: BLE001 - surfaced via the job status endpoint
        ingest_jobs.fail_job(job_id, str(exc))
        job = ingest_jobs.get_ingest_job(job_id)
        ingest_job_registry.record_completed_job(
            job_id, "documents", "error", job["result"], job["errors"], source_path=job["source_path"]
        )


async def run_code_ingestion(
    job_id: str,
    repo_path: str,
    enable_llm_summary: bool | None,
    max_concurrency: int,
    force_full_rechunk: bool = False,
) -> None:
    async def on_progress(done: int, total: int, *, phase: str, partial_result: dict) -> None:
        ingest_jobs.update_progress(job_id, done, total)
        ingest_jobs.set_phase(job_id, phase)
        ingest_jobs.update_result(job_id, partial_result)

    try:
        result = await ingest_code(
            repo_path,
            progress_callback=on_progress,
            enable_llm_summary=enable_llm_summary,
            max_concurrency=max_concurrency,
            force_full_rechunk=force_full_rechunk,
        )
        ingest_jobs.finish_job(job_id, result)
        ingestion_generation.bump()
        job = ingest_jobs.get_ingest_job(job_id)
        ingest_job_registry.record_completed_job(
            job_id, "code", job["status"], result, result.get("errors", []), source_path=job["source_path"]
        )
    except Exception as exc:  # noqa: BLE001 - surfaced via the job status endpoint
        ingest_jobs.fail_job(job_id, str(exc))
        job = ingest_jobs.get_ingest_job(job_id)
        ingest_job_registry.record_completed_job(
            job_id, "code", "error", job["result"], job["errors"], source_path=job["source_path"]
        )
