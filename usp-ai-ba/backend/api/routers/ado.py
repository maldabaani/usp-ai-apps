"""ADO creation results endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.deps import require_auth
from pipeline.runner import get_job_state

router = APIRouter(prefix="/ado", tags=["ado"])


@router.get("/status/{job_id}")
async def get_ado_status(job_id: str, user: dict = Depends(require_auth)):
    state = await get_job_state(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"ado_results": state["ado_results"], "errors": state["errors"]}
