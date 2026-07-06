"""Ingestion endpoints: one-time PDF and codebase indexing into ChromaDB."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import require_auth
from api.ingest_jobs import fail_job, finish_job, get_ingest_job, is_terminal, register_job, update_progress
from ingestion import ingest_job_registry, runner
from ingestion.enrichment.enrich import DEFAULT_MAX_CONCURRENCY
from ingestion.ingest_code import ingest_code
from ingestion.ingest_pdfs import ingest_pdfs

router = APIRouter(prefix="/ingest", tags=["ingest"])


class IngestPdfsRequest(BaseModel):
    folder_path: str


class IngestCodeRequest(BaseModel):
    repo_path: str
    # Per-request override for settings.INGEST_LLM_SUMMARY_ENABLED -- e.g. a
    # quick raw-only re-index of a huge repo without the LLM-cost tier.
    enable_llm_summary: bool | None = None
    max_concurrency: int | None = None


async def _run_pdf_ingestion(job_id: str, folder_path: str) -> None:
    async def on_progress(done: int, total: int) -> None:
        update_progress(job_id, done, total)

    try:
        result = await ingest_pdfs(folder_path, progress_callback=on_progress)
        finish_job(job_id, result)
        ingest_job_registry.record_completed_job(job_id, "pdfs", "done", result, result.get("errors", []))
    except Exception as exc:  # noqa: BLE001 - surfaced via the job status endpoint
        fail_job(job_id, str(exc))
        ingest_job_registry.record_completed_job(job_id, "pdfs", "error", None, [str(exc)])


async def _run_code_ingestion(
    job_id: str, repo_path: str, enable_llm_summary: bool | None, max_concurrency: int
) -> None:
    async def on_progress(done: int, total: int) -> None:
        update_progress(job_id, done, total)

    try:
        result = await ingest_code(
            repo_path,
            progress_callback=on_progress,
            enable_llm_summary=enable_llm_summary,
            max_concurrency=max_concurrency,
        )
        finish_job(job_id, result)
        ingest_job_registry.record_completed_job(job_id, "code", "done", result, result.get("errors", []))
    except Exception as exc:  # noqa: BLE001 - surfaced via the job status endpoint
        fail_job(job_id, str(exc))
        ingest_job_registry.record_completed_job(job_id, "code", "error", None, [str(exc)])


@router.post("/pdfs")
async def ingest_pdfs_endpoint(request: IngestPdfsRequest, user: dict = Depends(require_auth)):
    job_id = str(uuid.uuid4())
    register_job(job_id, kind="pdfs")
    runner.run_tracked(job_id, _run_pdf_ingestion(job_id, request.folder_path))
    return {"job_id": job_id, "status": "pending"}


@router.post("/code")
async def ingest_code_endpoint(request: IngestCodeRequest, user: dict = Depends(require_auth)):
    job_id = str(uuid.uuid4())
    register_job(job_id, kind="code")
    runner.run_tracked(
        job_id,
        _run_code_ingestion(
            job_id,
            request.repo_path,
            request.enable_llm_summary,
            request.max_concurrency or DEFAULT_MAX_CONCURRENCY,
        ),
    )
    return {"job_id": job_id, "status": "pending"}


@router.get("/status/{job_id}")
async def ingest_status_endpoint(job_id: str, user: dict = Depends(require_auth)):
    job = get_ingest_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    return {
        "status": job["status"],
        "progress": job["progress"],
        "errors": job["errors"],
        "result": job["result"],
    }


@router.post("/{job_id}/cancel")
async def cancel_ingest_job_endpoint(job_id: str, user: dict = Depends(require_auth)):
    """Stops a running (non-terminal) ingestion job -- see ingestion/runner.py's
    cancel_job for what "stop" means (cancels the tracked task; ingestion's own
    per-file/per-batch loops let asyncio.CancelledError propagate naturally)."""
    job = get_ingest_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    if is_terminal(job_id):
        raise HTTPException(status_code=409, detail=f"Job is already {job['status']!r}")

    await runner.cancel_job(job_id)
    ingest_job_registry.record_completed_job(job_id, job["kind"], "cancelled", job["result"], job["errors"])
    return {"status": "cancelled"}


@router.get("/history")
async def ingest_history_endpoint(user: dict = Depends(require_auth)):
    return ingest_job_registry.list_history()
