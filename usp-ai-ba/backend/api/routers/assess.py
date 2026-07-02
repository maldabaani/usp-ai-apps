"""Assessment endpoints: submit an SDD for analysis, list jobs, and poll job state."""
from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile

from api.deps import require_auth
from api.job_registry import list_assess_jobs, register_assess_job
from config import settings
from pipeline.runner import (
    RECREATABLE_OUTPUT_MODES,
    UPDATABLE_OUTPUT_MODES,
    get_job_state,
    identify_retryable_failure,
    recreate_tasks,
    retry_failed_step,
    start_job,
    update_tasks,
)
from pipeline.state import StoryForgeState, new_state, resolve_output_mode

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assess", tags=["assess"])


async def _run_assessment(initial_state: StoryForgeState) -> None:
    job_id = initial_state["job_id"]
    logger.info(
        "Starting assessment job=%s output_mode=%s ppm=%s",
        job_id,
        initial_state["output_mode"],
        initial_state["ppm_number"],
    )
    try:
        await start_job(initial_state)
        logger.info("Assessment job=%s completed", job_id)
    except Exception:
        logger.exception("Assessment job=%s crashed — job is stuck", job_id)


@router.post("")
async def submit_assessment(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    ppm_number: str = Form(...),
    ppm_name: str = Form(...),
    system_name: str = Form(...),
    review_mode: bool = Form(False),
    output_mode: str = Form(default=None),
    user: dict = Depends(require_auth),
):
    job_id = str(uuid.uuid4())
    resolved_output_mode = output_mode or settings.OUTPUT_MODE

    uploads_dir = Path(settings.UPLOADS_DIR)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    solution_doc_path = uploads_dir / f"{job_id}.pdf"
    solution_doc_path.write_bytes(await file.read())

    initial_state = new_state(
        job_id=job_id,
        ppm_number=ppm_number,
        ppm_name=ppm_name,
        system_name=system_name,
        solution_doc_path=str(solution_doc_path),
        review_mode=review_mode,
        output_mode=resolved_output_mode,
    )

    register_assess_job(job_id, ppm_number, ppm_name, system_name, resolved_output_mode)
    background_tasks.add_task(_run_assessment, initial_state)

    return {"job_id": job_id}


@router.post("/rerun/{job_id}")
async def rerun_assessment(
    job_id: str, background_tasks: BackgroundTasks, user: dict = Depends(require_auth)
):
    """Create a new job using the stored PDF from a previous job."""
    original = next((j for j in list_assess_jobs() if j["job_id"] == job_id), None)
    if original is None:
        raise HTTPException(status_code=404, detail="Job not found")

    stored_pdf = Path(settings.UPLOADS_DIR) / f"{job_id}.pdf"
    if not stored_pdf.exists():
        raise HTTPException(status_code=404, detail="Original PDF no longer available for re-run")

    new_job_id = str(uuid.uuid4())
    new_pdf = Path(settings.UPLOADS_DIR) / f"{new_job_id}.pdf"
    shutil.copy2(stored_pdf, new_pdf)

    # Older registry rows predate output_mode; fall back to the current global
    # default so a re-run of one of those still works.
    resolved_output_mode = original.get("output_mode") or settings.OUTPUT_MODE

    initial_state = new_state(
        job_id=new_job_id,
        ppm_number=original["ppm_number"],
        ppm_name=original["ppm_name"],
        system_name=original["system_name"],
        solution_doc_path=str(new_pdf),
        review_mode=False,
        output_mode=resolved_output_mode,
    )

    register_assess_job(
        new_job_id,
        original["ppm_number"],
        original["ppm_name"],
        original["system_name"],
        resolved_output_mode,
    )
    background_tasks.add_task(_run_assessment, initial_state)

    return {"job_id": new_job_id}


async def _run_retry(job_id: str) -> None:
    try:
        await retry_failed_step(job_id)
        logger.info("Retry job=%s completed", job_id)
    except Exception:
        logger.exception("Retry job=%s crashed — job is stuck", job_id)


@router.post("/retry/{job_id}")
async def retry_assessment(
    job_id: str, background_tasks: BackgroundTasks, user: dict = Depends(require_auth)
):
    """Re-run just the step that failed (generate_node, or whichever create_*
    node OUTPUT_MODE selects), without redoing SDD parsing, RAG retrieval,
    clarification, generation, or review that already succeeded."""
    state = await get_job_state(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if state.get("status") != "error":
        raise HTTPException(status_code=409, detail="Job is not in an error state")
    if identify_retryable_failure(state) is None:
        raise HTTPException(
            status_code=409,
            detail="This failure isn't resumable (it happened before the first "
            "checkpoint) — submit a new assessment instead",
        )

    background_tasks.add_task(_run_retry, job_id)
    return {"status": "retrying"}


async def _run_recreate(job_id: str) -> None:
    try:
        await recreate_tasks(job_id)
        logger.info("Recreate job=%s completed", job_id)
    except Exception:
        logger.exception("Recreate job=%s crashed — job is stuck", job_id)


@router.post("/recreate/{job_id}")
async def recreate_assessment_tasks(
    job_id: str, background_tasks: BackgroundTasks, user: dict = Depends(require_auth)
):
    """Push a completed job's approved stories to Notion/ADO again from
    scratch (Notion: archives the old pages first; ADO: no delete capability
    exists, so this just creates a fresh hierarchy alongside the old one)."""
    state = await get_job_state(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if state.get("status") != "done":
        raise HTTPException(status_code=409, detail="Job must be complete before tasks can be re-created")
    if resolve_output_mode(state, settings.OUTPUT_MODE) not in RECREATABLE_OUTPUT_MODES:
        raise HTTPException(
            status_code=409,
            detail="Re-create is only available for Notion or ADO output modes",
        )

    background_tasks.add_task(_run_recreate, job_id)
    return {"status": "recreating"}


async def _run_update(job_id: str) -> None:
    try:
        await update_tasks(job_id)
        logger.info("Update job=%s completed", job_id)
    except Exception:
        logger.exception("Update job=%s crashed — job is stuck", job_id)


@router.post("/update/{job_id}")
async def update_assessment_tasks(
    job_id: str, background_tasks: BackgroundTasks, user: dict = Depends(require_auth)
):
    """Update a completed job's existing Notion pages in place (position-
    matched against its approved stories/tasks) instead of archiving and
    recreating them. Notion only -- see pipeline/runner.py's update_tasks."""
    state = await get_job_state(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if state.get("status") != "done":
        raise HTTPException(status_code=409, detail="Job must be complete before tasks can be updated")
    if resolve_output_mode(state, settings.OUTPUT_MODE) not in UPDATABLE_OUTPUT_MODES:
        raise HTTPException(
            status_code=409,
            detail="Updating tasks in place is only available for the Notion output mode",
        )

    background_tasks.add_task(_run_update, job_id)
    return {"status": "updating"}


@router.get("/jobs")
async def list_jobs(user: dict = Depends(require_auth)):
    summaries = []
    for job in list_assess_jobs():
        state = await get_job_state(job["job_id"])
        notion_count = len(state.get("notion_results") or []) if state else 0
        ado_count = len(state.get("ado_results") or []) if state else 0
        summaries.append(
            {
                **job,
                "status": state["status"] if state else "pending",
                "story_count": len(state.get("generated_stories") or []) if state else 0,
                "task_count": notion_count or ado_count,
            }
        )
    return summaries


@router.get("/status/{job_id}")
async def get_assessment_status(job_id: str, user: dict = Depends(require_auth)):
    state = await get_job_state(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Job not found")
    # Jobs created before output_mode/warnings existed won't have those keys
    # in their persisted state -- resolve/default them so the UI's
    # "Re-create tasks" button and warning banner both have something to work with.
    return {
        **state,
        "output_mode": resolve_output_mode(state, settings.OUTPUT_MODE),
        "warnings": state.get("warnings") or [],
    }
