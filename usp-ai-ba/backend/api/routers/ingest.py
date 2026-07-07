"""Ingestion endpoints: one-time document (PDF/Word) and codebase indexing
into ChromaDB."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import require_admin, require_auth
from api.ingest_jobs import get_ingest_job, is_terminal, register_job
from ingestion import ingest_job_registry, runner, watcher
from ingestion.enrichment.enrich import DEFAULT_MAX_CONCURRENCY
from ingestion.runner_jobs import run_code_ingestion, run_document_ingestion

router = APIRouter(prefix="/ingest", tags=["ingest"])


class IngestDocumentsRequest(BaseModel):
    folder_path: str
    # Per-request override for settings.INGEST_LLM_SUMMARY_ENABLED -- see
    # IngestCodeRequest's identical field for why this is opt-out, not opt-in.
    enable_llm_summary: bool | None = None
    max_concurrency: int | None = None


class IngestCodeRequest(BaseModel):
    repo_path: str
    # Per-request override for settings.INGEST_LLM_SUMMARY_ENABLED -- e.g. a
    # quick raw-only re-index of a huge repo without the LLM-cost tier.
    enable_llm_summary: bool | None = None
    max_concurrency: int | None = None
    # Bypasses tier 1's chunking-manifest skip for this run only (every file
    # is re-chunked regardless of whether its content changed) -- e.g. if the
    # manifest is ever suspected stale. Does not disable the manifest going
    # forward; the next normal run goes back to skipping unchanged files.
    force_full_rechunk: bool = False


@router.post("/documents")
async def ingest_documents_endpoint(request: IngestDocumentsRequest, user: dict = Depends(require_auth)):
    if watcher.is_path_active(request.folder_path):
        raise HTTPException(status_code=409, detail="An ingestion run is already active for this path")
    job_id = str(uuid.uuid4())
    register_job(job_id, kind="documents", source_path=request.folder_path)
    watcher.mark_path_active(request.folder_path, job_id)
    runner.run_tracked(
        job_id,
        run_document_ingestion(
            job_id,
            request.folder_path,
            request.enable_llm_summary,
            request.max_concurrency or DEFAULT_MAX_CONCURRENCY,
        ),
    )
    return {"job_id": job_id, "status": "pending"}


@router.post("/code")
async def ingest_code_endpoint(request: IngestCodeRequest, user: dict = Depends(require_auth)):
    if watcher.is_path_active(request.repo_path):
        raise HTTPException(status_code=409, detail="An ingestion run is already active for this path")
    job_id = str(uuid.uuid4())
    register_job(job_id, kind="code", source_path=request.repo_path)
    watcher.mark_path_active(request.repo_path, job_id)
    runner.run_tracked(
        job_id,
        run_code_ingestion(
            job_id,
            request.repo_path,
            request.enable_llm_summary,
            request.max_concurrency or DEFAULT_MAX_CONCURRENCY,
            request.force_full_rechunk,
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
        "kind": job["kind"],
        "source_path": job["source_path"],
        "phase": job.get("phase"),
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
    ingest_job_registry.record_completed_job(
        job_id, job["kind"], "cancelled", job["result"], job["errors"], source_path=job["source_path"]
    )
    return {"status": "cancelled"}


@router.get("/history")
async def ingest_history_endpoint(user: dict = Depends(require_auth)):
    return ingest_job_registry.list_history()


@router.delete("/history", status_code=204)
async def clear_ingest_history_endpoint(user: dict = Depends(require_admin)) -> None:
    ingest_job_registry.clear_history()
