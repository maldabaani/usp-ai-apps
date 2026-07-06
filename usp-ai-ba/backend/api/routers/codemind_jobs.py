"""CodeMind extraction-job endpoints, ported from
com.jslogicextractor.web.ExtractionJobController. Preserves the exact
/api/v1/extraction-jobs* contract (camelCase field names, same status codes)
the Java controller served, since the plan calls this out as the one part of
CodeMind's REST surface that must not change shape.

The per-job/global SSE Q&A streaming routes (POST .../qa/stream and
POST /api/v1/ask/stream) are deferred to Phase F6 per the port plan; the
non-streaming POST .../qa endpoint is included here since codemind/qa.py
(Phase F3b) already covers it end to end.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator

from api.deps import require_auth
from codemind import extraction_stats, job_registry, manifest, output, qa
from codemind.agents.selector import get_agent_selector
from codemind.orchestrator import DEFAULT_OUTPUT_DIRECTORY, ExecutionMode, ExtractionJob, run as run_job
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/extraction-jobs", tags=["codemind-jobs"])

_OUTPUT_FILES_LIMIT = 50


class StartJobRequest(BaseModel):
    repositoryPath: str = Field(min_length=1)
    outputDirectory: Optional[str] = None
    maxConcurrency: Optional[int] = Field(default=None, gt=0)
    executionMode: Optional[str] = None


class JobResponse(BaseModel):
    jobId: str
    phase: str
    repositoryRoot: str
    outputDirectory: str
    executionMode: str
    incremental: bool
    totalFiles: int
    processedFiles: int
    succeededFiles: int
    failedFiles: int
    skippedFiles: int
    failureReason: Optional[str]
    createdAt: datetime
    finishedAt: Optional[datetime]

    @staticmethod
    def from_job(job: ExtractionJob) -> "JobResponse":
        return JobResponse(
            jobId=str(job.id),
            phase=job.phase.value,
            repositoryRoot=str(job.repository_root),
            outputDirectory=str(job.output_directory),
            executionMode=job.execution_mode.value,
            incremental=job.incremental,
            totalFiles=job.total_files,
            processedFiles=job.processed_files,
            succeededFiles=job.succeeded_files,
            failedFiles=job.failed_files,
            skippedFiles=job.skipped_files,
            failureReason=job.failure_reason,
            createdAt=job.created_at,
            finishedAt=job.finished_at,
        )


class OutputFileResponse(BaseModel):
    relativePath: str
    sizeBytes: int
    modifiedAt: datetime


class FailedFileResponse(BaseModel):
    relativePath: str
    errorMessage: str
    durationMillis: int


class QaRequest(BaseModel):
    question: str

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("question must not be blank")
        return value


class QaResponse(BaseModel):
    answer: str
    sourceFiles: list[str]


def _require_job(job_id: uuid.UUID) -> ExtractionJob:
    job = job_registry.find(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No such job: {job_id}")
    return job


def _parse_execution_mode(raw: Optional[str]) -> Optional[ExecutionMode]:
    if not raw or not raw.strip():
        return None
    try:
        return ExecutionMode(raw.strip().upper())
    except ValueError:
        allowed = [mode.value for mode in ExecutionMode]
        raise HTTPException(status_code=400, detail=f"executionMode must be one of {allowed}")


async def _run_job(job: ExtractionJob) -> None:
    try:
        await run_job(job, get_agent_selector())
    except Exception as exc:  # noqa: BLE001 - a crashed job must not crash the process
        logger.exception("codemind job=%s crashed", job.id)
        # An exception here means the run never got as far as orchestrator.run()'s
        # own failure handling (e.g. get_agent_selector() raised before any file
        # was scanned) -- without this, the job would otherwise sit at PENDING
        # forever with the only trace of what happened in the server log.
        job.mark_failed(str(exc))
    finally:
        job_registry.persist(job)


@router.post("", status_code=202, response_model=JobResponse)
async def start_job(
    request: StartJobRequest, background_tasks: BackgroundTasks, user: dict = Depends(require_auth)
) -> JobResponse:
    repository_root = Path(request.repositoryPath).expanduser().resolve()
    if not repository_root.is_dir():
        raise HTTPException(status_code=400, detail=f"repositoryPath is not a directory: {repository_root}")

    execution_mode = _parse_execution_mode(request.executionMode)
    # BATCH bypasses agent_selector entirely (codemind/batch.py talks to the
    # raw Anthropic Batches API directly, unlike SYNC mode which only reaches
    # Claude through an agent that get_agent_selector() already refuses to
    # register without a key) -- so unlike SYNC, nothing stops a BATCH job
    # from being submitted with no ANTHROPIC_API_KEY configured (e.g. an
    # Ollama-only setup). Left unchecked, it fails deep inside the batch
    # poll loop with the Anthropic SDK's raw, unhelpful auth error instead of
    # a clear one at submission time. Resolves the same live-settings
    # fallback register() itself would apply, since an unset request field
    # can still land on BATCH via the admin's configured default.
    effective_execution_mode = execution_mode or ExecutionMode(settings.CODEMIND_EXECUTION_MODE)
    if effective_execution_mode == ExecutionMode.BATCH and not settings.ANTHROPIC_API_KEY.strip():
        raise HTTPException(
            status_code=400,
            detail=(
                "BATCH execution mode requires ANTHROPIC_API_KEY to be configured, since it uses "
                "Anthropic's Batches API directly -- set it in Settings or select SYNC mode instead."
            ),
        )

    incremental = False
    if request.outputDirectory and request.outputDirectory.strip():
        output_directory: Optional[Path] = Path(request.outputDirectory).expanduser().resolve()
    else:
        # Auto-detect: if a manifest exists and its output directory is still
        # on disk, run incrementally reusing that directory; otherwise start
        # a fresh full run.
        loaded = manifest.load(DEFAULT_OUTPUT_DIRECTORY, repository_root)
        if loaded is not None and loaded.output_directory.is_dir():
            output_directory = loaded.output_directory
            incremental = True
        else:
            output_directory = None

    job = job_registry.register(repository_root, output_directory, request.maxConcurrency, execution_mode, incremental)
    background_tasks.add_task(_run_job, job)
    return JobResponse.from_job(job)


@router.get("", response_model=list[JobResponse])
async def list_jobs(user: dict = Depends(require_auth)) -> list[JobResponse]:
    return [JobResponse.from_job(job) for job in job_registry.find_all()]


@router.delete("", status_code=204)
async def clear_all_jobs(user: dict = Depends(require_auth)) -> None:
    job_registry.clear_all()


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: uuid.UUID, user: dict = Depends(require_auth)) -> JobResponse:
    return JobResponse.from_job(_require_job(job_id))


@router.post("/{job_id}/cancel", status_code=204)
async def cancel_job(job_id: uuid.UUID, user: dict = Depends(require_auth)) -> None:
    _require_job(job_id).request_cancel()


@router.delete("/{job_id}", status_code=204)
async def delete_job(job_id: uuid.UUID, user: dict = Depends(require_auth)) -> None:
    _require_job(job_id)
    job_registry.delete(job_id)


@router.get("/{job_id}/output-files", response_model=list[OutputFileResponse])
async def list_output_files(job_id: uuid.UUID, user: dict = Depends(require_auth)) -> list[OutputFileResponse]:
    job = _require_job(job_id)
    files = output.recent_files(job.output_directory, _OUTPUT_FILES_LIMIT)
    return [
        OutputFileResponse(relativePath=f.relative_path, sizeBytes=f.size_bytes, modifiedAt=f.modified_at)
        for f in files
    ]


@router.get("/{job_id}/output-file")
async def read_output_file(job_id: uuid.UUID, relativePath: str, user: dict = Depends(require_auth)) -> Response:
    job = _require_job(job_id)
    content = output.read_output_file(job.output_directory, relativePath)
    if content is None:
        raise HTTPException(status_code=404, detail="Output file not found")
    return Response(content=content, media_type="application/json")


@router.get("/{job_id}/failed-files", response_model=list[FailedFileResponse])
async def list_failed_files(job_id: uuid.UUID, user: dict = Depends(require_auth)) -> list[FailedFileResponse]:
    job = _require_job(job_id)
    failed = output.list_failed_files(job.output_directory)
    return [
        FailedFileResponse(relativePath=f.relative_path, errorMessage=f.error_message, durationMillis=f.duration_millis)
        for f in failed
    ]


@router.get("/{job_id}/export")
async def export_job(job_id: uuid.UUID, user: dict = Depends(require_auth)) -> Response:
    job = _require_job(job_id)
    export_bytes = _build_export_json(job)
    filename = f"codemind-{str(job_id).replace('-', '')[:8]}.json"
    return Response(
        content=export_bytes,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _build_export_json(job: ExtractionJob) -> bytes:
    files = []
    for output_file in output.recent_files(job.output_directory, limit=10**9):
        raw = output.read_output_file(job.output_directory, output_file.relative_path)
        if raw is None:
            continue
        try:
            result = json.loads(raw)
        except ValueError:
            continue
        if not result.get("success") or result.get("skipped") or not result.get("content"):
            continue
        parsed_content = extraction_stats.parse_extracted_content(result.get("content"))
        if parsed_content is None:
            continue
        files.append(parsed_content)

    export = {
        "jobId": str(job.id),
        "repositoryRoot": str(job.repository_root),
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "totalExtracted": len(files),
        "files": files,
    }
    return json.dumps(export, indent=2).encode("utf-8")


@router.post("/{job_id}/qa", response_model=QaResponse)
async def ask(job_id: uuid.UUID, request: QaRequest, user: dict = Depends(require_auth)) -> QaResponse:
    job = _require_job(job_id)
    answer = await qa.ask(job.output_directory, request.question)
    return QaResponse(answer=answer.answer, sourceFiles=answer.source_files)
