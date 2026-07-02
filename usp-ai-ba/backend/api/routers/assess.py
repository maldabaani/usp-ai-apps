"""Assessment endpoints: submit an SDD for analysis, list jobs, and poll job state."""
from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile

from api.job_registry import list_assess_jobs, register_assess_job
from config import settings
from pipeline.runner import get_job_state, identify_retryable_failure, retry_failed_step, start_job
from pipeline.state import StoryForgeState, new_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assess", tags=["assess"])


async def _run_assessment(initial_state: StoryForgeState) -> None:
    job_id = initial_state["job_id"]
    logger.info(
        "Starting assessment job=%s output_mode=%s ppm=%s",
        job_id,
        settings.OUTPUT_MODE,
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
):
    job_id = str(uuid.uuid4())

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
    )

    register_assess_job(job_id, ppm_number, ppm_name, system_name)
    background_tasks.add_task(_run_assessment, initial_state)

    return {"job_id": job_id}


@router.post("/rerun/{job_id}")
async def rerun_assessment(job_id: str, background_tasks: BackgroundTasks):
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

    initial_state = new_state(
        job_id=new_job_id,
        ppm_number=original["ppm_number"],
        ppm_name=original["ppm_name"],
        system_name=original["system_name"],
        solution_doc_path=str(new_pdf),
        review_mode=False,
    )

    register_assess_job(new_job_id, original["ppm_number"], original["ppm_name"], original["system_name"])
    background_tasks.add_task(_run_assessment, initial_state)

    return {"job_id": new_job_id}


async def _run_retry(job_id: str) -> None:
    try:
        await retry_failed_step(job_id)
        logger.info("Retry job=%s completed", job_id)
    except Exception:
        logger.exception("Retry job=%s crashed — job is stuck", job_id)


@router.post("/retry/{job_id}")
async def retry_assessment(job_id: str, background_tasks: BackgroundTasks):
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


@router.get("/jobs")
async def list_jobs():
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
async def get_assessment_status(job_id: str):
    state = await get_job_state(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return state
