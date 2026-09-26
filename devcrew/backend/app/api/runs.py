"""Run lifecycle endpoints: create, list, detail, events (SSE), resume, cancel."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.api.deps import ContainerDep
from app.api.schemas import (
    CreateRunRequest,
    MessageIn,
    MessageOut,
    PauseRequest,
    PauseState,
    PendingInput,
    ResumeRequest,
    RunDetail,
    RunSummary,
)
from app.api.sse import event_stream
from app.db.models import RunStatus
from app.events.types import EventType
from app.graph.interrupts import InterruptKind, ResumeAction, ResumePayload
from app.graph.steering import EXPIRED, PENDING
from app.services.run_manager import (
    InvalidResumeError,
    RunConflictError,
    RunManager,
    RunNotFoundError,
)
from app.services.workflow import Workflow, build_workflow

router = APIRouter(prefix="/runs", tags=["runs"])

STATE_FIELDS = (
    "plan",
    "design",
    "qa_log",
    "integration",
    "integration_branch",
    "errors",
    "target",
    "mode",
    "base_branch",
    "repo_info",
    "gates",
    "issue",
    "followup",
    "human_notes",
)


async def _run_or_404(manager: RunManager, run_id: str) -> Any:
    try:
        return await manager.get(run_id)
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"run {run_id} not found") from None


async def detail(manager: RunManager, run_id: str) -> RunDetail:
    run = await _run_or_404(manager, run_id)
    state = await manager.state(run_id)
    pending = [] if manager.is_busy(run_id) else await manager.pending(run_id)
    summary = RunSummary.of(run, busy=manager.is_busy(run_id))
    return RunDetail(
        **summary.model_dump(),
        **{k: state.get(k) for k in STATE_FIELDS if state.get(k) is not None},
        tasks=state.get("tasks") or {},
        wave=state.get("wave") or 0,
        pending=[PendingInput.of(p) for p in pending],
        pause_requested=await manager.deps.steering.pause_requested(run_id),
    )


@router.post("", response_model=RunSummary, status_code=status.HTTP_201_CREATED)
async def create_run(body: CreateRunRequest, container: ContainerDep) -> RunSummary:
    limit = container.settings.max_request_chars
    if len(body.request) > limit:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"the request has {len(body.request):,} characters; the limit is {limit:,} "
            "(MAX_REQUEST_CHARS: the Planner and Architect must fit it in their context window)",
        )
    if body.target == "existing" and container.deps.github is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "working on an existing repository needs GitHub access: set GITHUB_TOKEN and "
            "GITHUB_DELIVERY_ENABLED=true",
        )
    run = await container.manager.start(
        request=body.request,
        repo_target=body.repo_target,
        create_repo=body.create_repo and body.target == "new",
        target=body.target,
        mode=body.mode,
    )
    return RunSummary.of(run, busy=True)


@router.get("", response_model=list[RunSummary])
async def list_runs(
    container: ContainerDep, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> list[RunSummary]:
    manager = container.manager
    return [RunSummary.of(r, busy=manager.is_busy(r.id)) for r in await manager.runs.list(limit)]


@router.get("/{run_id}", response_model=RunDetail)
async def get_run(run_id: str, container: ContainerDep) -> RunDetail:
    return await detail(container.manager, run_id)


@router.get("/{run_id}/workflow", response_model=Workflow)
async def get_workflow(run_id: str, container: ContainerDep) -> Workflow:
    """The run as a workflow graph: stages, one node per task, statuses and activity."""
    manager = container.manager
    run = await _run_or_404(manager, run_id)
    state = await manager.state(run_id)
    pending = [] if manager.is_busy(run_id) else await manager.pending(run_id)
    events = [e async for e in container.events.replay(run_id)]
    return build_workflow(
        run_id=run_id,
        status=run.status,
        request=run.request,
        created_at=run.created_at,
        state=state,
        events=events,
        pending=pending,
        pr_url=run.pr_url,
        max_dev_iterations=container.settings.max_dev_iterations,
    )


@router.get("/{run_id}/events")
async def run_events(
    run_id: str,
    container: ContainerDep,
    last_event_id_header: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    last_event_id: Annotated[int | None, Query(ge=0)] = None,
) -> StreamingResponse:
    manager = container.manager
    await _run_or_404(manager, run_id)
    after = last_event_id
    if after is None and last_event_id_header:
        try:
            after = int(last_event_id_header)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid Last-Event-ID") from None

    async def terminal() -> bool:
        run = await manager.runs.get(run_id)
        return run is not None and RunStatus(run.status).is_terminal and not manager.is_busy(run_id)

    return StreamingResponse(
        event_stream(container.events, run_id, after, run_is_terminal=terminal),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{run_id}/resume", response_model=PendingInput, status_code=status.HTTP_202_ACCEPTED)
async def resume_run(run_id: str, body: ResumeRequest, container: ContainerDep) -> PendingInput:
    manager = container.manager
    await _run_or_404(manager, run_id)
    if body.action is ResumeAction.UPDATE:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "update is reserved for the GitHub watcher (PR activity)",
        )
    try:
        payload = body.payload()
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    try:
        answered = await manager.resume(run_id, payload, body.interrupt_id)
    except RunConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except InvalidResumeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return PendingInput.of(answered)


@router.post("/{run_id}/cancel", response_model=RunSummary)
async def cancel_run(run_id: str, container: ContainerDep) -> RunSummary:
    manager = container.manager
    await _run_or_404(manager, run_id)
    try:
        await manager.cancel(run_id)
    except RunConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return RunSummary.of(await manager.get(run_id))


# ------------------------------------------------------------------------------ steering
@router.get("/{run_id}/messages", response_model=list[MessageOut])
async def list_messages(run_id: str, container: ContainerDep) -> list[MessageOut]:
    run = await _run_or_404(container.manager, run_id)
    finished = RunStatus(run.status).is_terminal
    out = []
    for m in await container.deps.steering.messages(run_id):
        stale = finished and m.status == PENDING
        out.append(
            MessageOut(
                id=m.id,
                task_id=m.task_id,
                text=m.text,
                status=EXPIRED if stale else m.status,
                action=m.action,
                reply=m.reply,
                created_at=m.created_at,
                updated_at=m.updated_at,
            )
        )
    return out


@router.post("/{run_id}/messages", response_model=MessageOut, status_code=status.HTTP_201_CREATED)
async def post_message(run_id: str, body: MessageIn, container: ContainerDep) -> MessageOut:
    """Chat to the run (or one task). Applied at the next safe point: the Planner/Architect
    start, the scheduler before each wave, or the task's next developer turn."""
    manager = container.manager
    run = await _run_or_404(manager, run_id)
    if RunStatus(run.status).is_terminal:
        raise HTTPException(status.HTTP_409_CONFLICT, f"run is {run.status}")
    if body.task_id is not None:
        state = await manager.state(run_id)
        known = set(state.get("tasks") or {}) | {
            t["id"] for t in (state.get("plan") or {}).get("tasks") or []
        }
        if body.task_id not in known:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown task {body.task_id}"
            )
    m = await container.deps.steering.add_message(run_id, body.text, body.task_id)
    await container.events.publish(
        run_id,
        EventType.MESSAGE,
        node="steer",
        task_id=m.task_id,
        payload={"message_id": m.id, "text": m.text, "status": m.status},
    )
    return MessageOut(
        id=m.id,
        task_id=m.task_id,
        text=m.text,
        status=m.status,
        action=None,
        reply=None,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


@router.post("/{run_id}/pause", response_model=PauseState)
async def pause_run(run_id: str, body: PauseRequest, container: ContainerDep) -> PauseState:
    """Pause at the next safe point (before the next wave), or resume a paused run."""
    manager = container.manager
    run = await _run_or_404(manager, run_id)
    if RunStatus(run.status).is_terminal:
        raise HTTPException(status.HTTP_409_CONFLICT, f"run is {run.status}")
    steering = container.deps.steering
    await steering.set_pause(run_id, body.paused)
    if not body.paused and not manager.is_busy(run_id):
        paused = [
            p for p in await manager.pending(run_id) if p.value.get("kind") == InterruptKind.PAUSE
        ]
        if paused:
            try:
                await manager.resume(
                    run_id, ResumePayload(action=ResumeAction.APPROVE), paused[0].id
                )
            except RunConflictError as exc:
                raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    run = await manager.get(run_id)
    return PauseState(status=run.status, pause_requested=await steering.pause_requested(run_id))
