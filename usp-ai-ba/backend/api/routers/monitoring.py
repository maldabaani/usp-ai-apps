"""Exposes captured error records (see monitoring/log_capture.py) for the
Angular shell's monitoring page."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import require_auth
from monitoring.error_log import list_errors

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


@router.get("/errors")
async def get_errors(user: dict = Depends(require_auth)):
    return {"errors": list_errors()}
