"""Generated-document download endpoint (used when settings.OUTPUT_MODE == "document")."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from api.deps import require_auth
from pipeline.runner import get_job_state

router = APIRouter(prefix="/export", tags=["export"])


@router.get("/document/{job_id}")
async def get_export_document(job_id: str, user: dict = Depends(require_auth)):
    state = await get_job_state(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Job not found")

    document_path = state.get("document_path", "")
    if not document_path or not os.path.exists(document_path):
        raise HTTPException(status_code=404, detail="Document not yet generated")

    return FileResponse(
        document_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=os.path.basename(document_path),
    )
