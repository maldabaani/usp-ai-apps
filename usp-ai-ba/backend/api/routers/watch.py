"""Watched-path management for auto re-ingestion (Phase L-C): CRUD over
ingestion/watch_registry.py's persisted targets, wired to
ingestion/watcher.py's live WatcherManager so add/enable/disable/remove take
effect immediately, without a backend restart.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import require_admin, require_auth
from ingestion import watch_registry
from ingestion.watcher import watcher

router = APIRouter(prefix="/watch", tags=["watch"])


class AddWatchTargetRequest(BaseModel):
    path: str
    kind: Literal["documents", "code"]


class SetWatchTargetEnabledRequest(BaseModel):
    enabled: bool


@router.get("/targets")
async def list_watch_targets(user: dict = Depends(require_auth)):
    return watch_registry.list_targets()


@router.post("/targets")
async def add_watch_target(request: AddWatchTargetRequest, user: dict = Depends(require_admin)):
    if not Path(request.path).expanduser().is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {request.path}")

    target = watch_registry.add_target(request.path, request.kind)
    watcher.start_target(target)
    return target


@router.patch("/targets/{target_id}")
async def set_watch_target_enabled(
    target_id: str, request: SetWatchTargetEnabledRequest, user: dict = Depends(require_admin)
):
    target = watch_registry.set_enabled(target_id, request.enabled)
    if target is None:
        raise HTTPException(status_code=404, detail="Watch target not found")

    if request.enabled:
        watcher.start_target(target)
    else:
        watcher.stop_target(target_id)
    return target


@router.delete("/targets/{target_id}")
async def delete_watch_target(target_id: str, user: dict = Depends(require_admin)):
    watcher.stop_target(target_id)
    removed = watch_registry.remove_target(target_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Watch target not found")
    return {"status": "deleted"}
