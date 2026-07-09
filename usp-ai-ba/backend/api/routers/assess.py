"""Assessment endpoints: submit an SDD for analysis, list jobs, and poll job state."""
from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from api.deps import require_auth
from api.job_registry import delete_assess_job, list_assess_jobs, register_assess_job
from config import settings
from pipeline.runner import (
    RECREATABLE_OUTPUT_MODES,
    TERMINAL_STATUSES,
    UPDATABLE_OUTPUT_MODES,
    cancel_job,
    delete_job,
    get_job_state,
    identify_retryable_failure,
    recreate_tasks,
    retry_failed_step,
    run_tracked,
    start_job,
    update_tasks,
)
from pipeline.state import StoryForgeState, new_state, resolve_output_mode

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assess", tags=["assess"])

# Deliberately narrower than ingestion.ingest_documents.SUPPORTED_EXTENSIONS
# (which also allows .md/.html for the unrelated corpus-ingestion feature) --
# SDD assessment stays PDF/Word only. Legacy binary .doc is out of scope:
# python-docx can't read it (a different, non-XML format), and adding a
# reader for it would mean a new external tool dependency.
_ALLOWED_SDD_EXTENSIONS = {".pdf", ".docx"}


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
    file: UploadFile | None = File(None),
    solution_doc_text: str | None = Form(None),
    ppm_number: str = Form(...),
    ppm_name: str = Form(...),
    system_name: str = Form(...),
    review_mode: bool = Form(False),
    output_mode: str = Form(default=None),
    user: dict = Depends(require_auth),
):
    has_file = file is not None and bool(file.filename)
    pasted_text = (solution_doc_text or "").strip()
    if has_file and pasted_text:
        raise HTTPException(status_code=400, detail="Provide either a file or pasted text, not both")
    if not has_file and not pasted_text:
        raise HTTPException(status_code=400, detail="Provide either a file or pasted text")

    job_id = str(uuid.uuid4())
    resolved_output_mode = output_mode or settings.OUTPUT_MODE

    if has_file:
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in _ALLOWED_SDD_EXTENSIONS:
            raise HTTPException(status_code=400, detail="Only .pdf and .docx files are supported")
        uploads_dir = Path(settings.UPLOADS_DIR)
        uploads_dir.mkdir(parents=True, exist_ok=True)
        solution_doc_path = uploads_dir / f"{job_id}{suffix}"
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
    else:
        initial_state = new_state(
            job_id=job_id,
            ppm_number=ppm_number,
            ppm_name=ppm_name,
            system_name=system_name,
            solution_doc_path="",
            review_mode=review_mode,
            output_mode=resolved_output_mode,
            solution_doc_text=pasted_text,
        )

    register_assess_job(job_id, ppm_number, ppm_name, system_name, resolved_output_mode)
    run_tracked(job_id, _run_assessment(initial_state))

    return {"job_id": job_id}


@router.post("/rerun/{job_id}")
async def rerun_assessment(job_id: str, user: dict = Depends(require_auth)):
    """Create a new job re-using the original job's SDD -- a copy of its
    uploaded file (preserving the real .pdf/.docx extension), or, for a
    pasted-text original, the same text carried forward with no file at all."""
    original = await get_job_state(job_id)
    if original is None:
        raise HTTPException(status_code=404, detail="Job not found")

    new_job_id = str(uuid.uuid4())
    original_path = original.get("solution_doc_path") or ""

    # Older registry rows predate output_mode; fall back to the current global
    # default so a re-run of one of those still works.
    resolved_output_mode = resolve_output_mode(original, settings.OUTPUT_MODE)

    if original_path:
        stored_file = Path(original_path)
        if not stored_file.exists():
            raise HTTPException(status_code=404, detail="Original file no longer available for re-run")
        new_file = Path(settings.UPLOADS_DIR) / f"{new_job_id}{stored_file.suffix}"
        shutil.copy2(stored_file, new_file)
        initial_state = new_state(
            job_id=new_job_id,
            ppm_number=original["ppm_number"],
            ppm_name=original["ppm_name"],
            system_name=original["system_name"],
            solution_doc_path=str(new_file),
            review_mode=False,
            output_mode=resolved_output_mode,
        )
    else:
        initial_state = new_state(
            job_id=new_job_id,
            ppm_number=original["ppm_number"],
            ppm_name=original["ppm_name"],
            system_name=original["system_name"],
            solution_doc_path="",
            review_mode=False,
            output_mode=resolved_output_mode,
            solution_doc_text=original.get("solution_doc_text") or "",
        )

    register_assess_job(
        new_job_id,
        original["ppm_number"],
        original["ppm_name"],
        original["system_name"],
        resolved_output_mode,
    )
    run_tracked(new_job_id, _run_assessment(initial_state))

    return {"job_id": new_job_id}


async def _run_retry(job_id: str) -> None:
    try:
        await retry_failed_step(job_id)
        logger.info("Retry job=%s completed", job_id)
    except Exception:
        logger.exception("Retry job=%s crashed — job is stuck", job_id)


@router.post("/retry/{job_id}")
async def retry_assessment(job_id: str, user: dict = Depends(require_auth)):
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

    run_tracked(job_id, _run_retry(job_id))
    return {"status": "retrying"}


async def _run_recreate(job_id: str) -> None:
    try:
        await recreate_tasks(job_id)
        logger.info("Recreate job=%s completed", job_id)
    except Exception:
        logger.exception("Recreate job=%s crashed — job is stuck", job_id)


@router.post("/recreate/{job_id}")
async def recreate_assessment_tasks(job_id: str, user: dict = Depends(require_auth)):
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

    run_tracked(job_id, _run_recreate(job_id))
    return {"status": "recreating"}


async def _run_update(job_id: str) -> None:
    try:
        await update_tasks(job_id)
        logger.info("Update job=%s completed", job_id)
    except Exception:
        logger.exception("Update job=%s crashed — job is stuck", job_id)


@router.post("/update/{job_id}")
async def update_assessment_tasks(job_id: str, user: dict = Depends(require_auth)):
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

    run_tracked(job_id, _run_update(job_id))
    return {"status": "updating"}


@router.post("/cancel/{job_id}")
async def cancel_assessment(job_id: str, user: dict = Depends(require_auth)):
    """Stops a running (non-terminal) assessment job -- see
    pipeline/runner.py's cancel_job for what "stop" means at each phase."""
    state = await get_job_state(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if state.get("status") in TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail=f"Job is already {state.get('status')!r}")

    state = await cancel_job(job_id)
    return {"status": state["status"]}


@router.delete("/{job_id}", status_code=204)
async def delete_assessment(job_id: str, user: dict = Depends(require_auth)) -> None:
    """Permanently deletes an assessment: cancels it first if still running
    (deleting checkpoint rows out from under a task still writing to them
    would be a race -- see pipeline/runner.py's delete_job), then removes its
    LangGraph checkpoint data, its uploaded PDF, and its entry in the
    dashboard's job registry -- in that order, so a failure partway through
    leaves the job still visible/retryable rather than silently vanishing
    from the list while orphaned data remains on disk."""
    if not any(job["job_id"] == job_id for job in list_assess_jobs()):
        raise HTTPException(status_code=404, detail="Job not found")

    # Capture the real uploaded-file path (if any) before delete_job() touches
    # the checkpoint that state lives in -- a pasted-text job has none, and a
    # .docx job's path won't match a hardcoded ".pdf" guess.
    state = await get_job_state(job_id)
    solution_doc_path = (state.get("solution_doc_path") if state else "") or ""

    await delete_job(job_id)
    if solution_doc_path:
        Path(solution_doc_path).unlink(missing_ok=True)
    delete_assess_job(job_id)


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
