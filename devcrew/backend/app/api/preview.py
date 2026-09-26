"""Live preview of the generated app (Phase 16, BL-110): start, status and logs, stop."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.api.deps import ContainerDep
from app.db.models import RunStatus
from app.graph.layout import LayoutEntry, resolve_layout, sandbox_target
from app.graph.state import get_design
from app.sandbox.preview import PREVIEW_TASK, PreviewError, PreviewState, preview_options
from app.services.run_manager import RunNotFoundError

router = APIRouter(prefix="/runs/{run_id}/preview", tags=["preview"])


class PreviewStart(BaseModel):
    stack: str | None = None  # mixed projects: which one (default: the first with a preview)


async def _context(container: Any, run_id: str) -> tuple[Any, dict[str, Any]]:
    try:
        run = await container.manager.get(run_id)
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"run {run_id} not found") from None
    return run, await container.manager.state(run_id)


def _layout(container: Any, state: dict[str, Any]) -> dict[str, LayoutEntry] | None:
    if not state.get("design") or not state.get("workspace"):
        return None
    if not Path(state["workspace"]).is_dir():
        return None
    try:
        return resolve_layout(get_design(state), container.deps.templates)
    except (KeyError, ValueError):
        return None


@router.get("", response_model=PreviewState)
async def preview_state(
    run_id: str, container: ContainerDep, logs: Annotated[bool, Query()] = False
) -> PreviewState:
    _, state = await _context(container, run_id)
    manager = container.deps.preview
    if manager is None:
        return PreviewState(enabled=False)
    layout = _layout(container, state)
    options = preview_options(layout) if layout else []
    return await manager.state(run_id, options=options, logs=logs)


@router.post("", response_model=PreviewState, status_code=status.HTTP_202_ACCEPTED)
async def start_preview(run_id: str, body: PreviewStart, container: ContainerDep) -> PreviewState:
    _, state = await _context(container, run_id)
    manager = container.deps.preview
    if manager is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "live preview is off (it needs the Docker sandbox and PREVIEW_ENABLED=true)",
        )
    layout = _layout(container, state)
    if not layout:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "nothing to preview yet: the project is not scaffolded"
        )
    target = sandbox_target(
        run_id, PREVIEW_TASK, Path(state["workspace"]), get_design(state), layout
    )
    try:
        return await manager.start(run_id, target, layout, body.stack)
    except PreviewError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def stop_preview(run_id: str, container: ContainerDep) -> None:
    run, _ = await _context(container, run_id)
    manager = container.deps.preview
    if manager is None or not manager.active(run_id):
        return
    await manager.stop(run_id)
    if RunStatus(run.status).is_terminal and container.deps.sandbox is not None:
        # the run already released its sandbox; the preview's dependency volumes go too
        await container.deps.sandbox.cleanup_run(run_id)
