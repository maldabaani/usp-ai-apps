"""Clarification answer endpoint: resumes a job paused at the clarify gate."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from api.deps import require_auth
from pipeline.runner import get_job_state, resume_after_clarification

router = APIRouter(prefix="/clarify", tags=["clarify"])


class ClarifyAnswerRequest(BaseModel):
    answers: dict[str, str]


@router.post("/answer/{job_id}")
async def submit_clarification_answers(
    job_id: str,
    request: ClarifyAnswerRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_auth),
):
    state = await get_job_state(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not state["clarification_needed"]:
        raise HTTPException(status_code=409, detail="Job is not awaiting clarification")

    background_tasks.add_task(resume_after_clarification, job_id, request.answers)
    return {"status": "generating"}
