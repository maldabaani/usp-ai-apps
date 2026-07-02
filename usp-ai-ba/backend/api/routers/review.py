"""Review approval endpoint: resumes a job paused at the review gate toward ADO creation."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from api.deps import require_auth
from pipeline.runner import get_job_state, resume_after_review

router = APIRouter(prefix="/review", tags=["review"])


class ReviewApproveRequest(BaseModel):
    approved_stories: list[dict | None]


@router.post("/approve/{job_id}")
async def approve_review(
    job_id: str,
    request: ReviewApproveRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_auth),
):
    state = await get_job_state(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not state["review_mode"]:
        raise HTTPException(status_code=409, detail="Job was not run in review mode")

    stories = [s for s in request.approved_stories if s is not None]
    background_tasks.add_task(resume_after_review, job_id, stories)
    return {"status": "creating"}
