"""The fixed backbone graph.

planner -> approve_plan -> architect -> approve_design -> scaffold -> schedule
        -> task_worker x N (Send, one per ready task) -> schedule -> ... -> integration
        -> approve_final -> github_delivery -> done

ask_human and coordinator/escalate are side exits that return to the calling node.
Before every wave the scheduler is a safe point for steering: it pauses on request (`pause`)
and applies the human's chat messages (app/graph/steering.py).

Scheduling runs in waves: every task whose dependencies are merged is dispatched, up to
MAX_PARALLEL_DEVS at a time, as parallel task_worker subgraphs (one worktree each). When the
wave finishes (merged, failed, split or replanned), the scheduler applies Coordinator plan
changes and dispatches the next wave.
"""

from __future__ import annotations

from typing import Any, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Send

from app.db.models import RunStatus
from app.events.types import EventType
from app.graph.coordinator import make_run_coordinator, make_run_escalate
from app.graph.interrupts import InterruptKind, InterruptRequest, ResumeAction, request_input
from app.graph.nodes.approvals import make_approve_design, make_approve_final, make_approve_plan
from app.graph.nodes.architect import make_architect
from app.graph.nodes.finish import (
    make_delivery_failed,
    make_done,
    make_github_delivery,
    make_integration,
)
from app.graph.nodes.followup import (
    make_approve_followup,
    make_followup,
    make_report_followup,
    make_watch_pr,
)
from app.graph.nodes.gates import make_gates
from app.graph.nodes.human import make_ask_human
from app.graph.nodes.planner import make_planner
from app.graph.nodes.repository import make_prepare_repo
from app.graph.nodes.scaffold import make_scaffold
from app.graph.replan import apply_changes
from app.graph.runtime import GraphDeps, NodeFn, instrument
from app.graph.state import RunState, TaskState, TaskStatus, dump, get_plan
from app.graph.steering import apply_at_schedule
from app.graph.task_subgraph import build_task_subgraph


def ready_tasks(state: dict[str, Any]) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """Return (dispatchable task ids in topological order, status updates for blocked tasks)."""
    plan = get_plan(state)
    tasks = {tid: TaskState.model_validate(t) for tid, t in state.get("tasks", {}).items()}
    updates: dict[str, dict[str, Any]] = {}
    ready: list[str] = []
    for layer in plan.layers():
        for tid in layer:
            ts = tasks[tid]
            if ts.status is not TaskStatus.PENDING:
                continue
            deps = [tasks[d].status for d in plan.task(tid).depends_on]
            if any(
                s in (TaskStatus.FAILED, TaskStatus.BLOCKED, TaskStatus.CANCELLED) for s in deps
            ):
                ts.status = TaskStatus.BLOCKED
                ts.error = "a dependency failed"
                updates[tid] = dump(ts)
            elif all(s is TaskStatus.MERGED for s in deps):
                ready.append(tid)
    return ready, updates


def make_schedule(deps: GraphDeps) -> NodeFn:
    async def schedule(state: dict[str, Any]) -> Command[Any]:
        run_id = state["run_id"]
        # 0. Safe point (Phase 13): pause on request, then apply the human's chat messages.
        if await deps.steering.pause_requested(run_id):
            return Command(goto="pause", update={"status": RunStatus.PAUSED.value})
        state, extra = await apply_at_schedule(deps, state)
        steered_tasks = extra.pop("tasks", {})
        # 1. Apply Coordinator plan changes (replan/split) produced by the last wave.
        changes = state.get("plan_changes") or []
        applied = state.get("plan_changes_applied", 0)
        if len(changes) > applied:
            plan, task_updates, errors = apply_changes(
                get_plan(state), state.get("tasks", {}), changes[applied:]
            )
            state = {**state, "plan": dump(plan), "tasks": {**state["tasks"], **task_updates}}
            extra |= {"plan": dump(plan), "plan_changes_applied": len(changes), "errors": errors}
            for change in changes[applied:]:
                await deps.emit(
                    run_id,
                    EventType.TOOL_RESULT,
                    node="schedule",
                    task_id=str(change["task_id"]),
                    tool="plan_change",
                    ok=True,
                    result=change["kind"],
                )
        else:
            task_updates = {}

        # 2. Dispatch the next wave.
        ready, blocked = ready_tasks(state)
        updates = {**steered_tasks, **task_updates, **blocked}
        if not ready:
            return Command(
                goto="integration",
                update={"tasks": updates, "status": RunStatus.INTEGRATING.value, **extra},
            )
        plan = get_plan(state)
        wave = state.get("wave", 0) + 1
        dispatch = ready[: deps.settings.max_parallel_devs]
        await deps.emit(
            run_id,
            EventType.TOOL_RESULT,
            node="schedule",
            tool="dispatch",
            ok=True,
            wave=wave,
            tasks=dispatch,
            waiting=ready[len(dispatch) :],
            result=f"wave {wave}: {', '.join(dispatch)}",
        )
        sends = []
        for lane, tid in enumerate(dispatch):
            ts = TaskState.model_validate(state["tasks"][tid])
            ts.status = TaskStatus.IN_PROGRESS
            ts.wave, ts.lane = wave, lane
            updates[tid] = dump(ts)
            sends.append(
                Send(
                    "task_worker",
                    {
                        "run_id": state["run_id"],
                        "task": dump(plan.task(tid)),
                        "plan": state["plan"],
                        "design": state["design"],
                        "workspace": state["workspace"],
                        "integration_branch": state["integration_branch"],
                        "tasks": {tid: dump(ts)},
                        "human_notes": state.get("human_notes") or [],
                    },
                )
            )
        return Command(goto=sends, update={"tasks": updates, "wave": wave, **extra})

    return schedule


def make_pause(deps: GraphDeps) -> NodeFn:
    """Paused between waves until the human resumes (approve)."""

    async def pause(state: dict[str, Any]) -> Command[str]:
        request_input(
            InterruptRequest(
                kind=InterruptKind.PAUSE,
                title="Paused",
                allowed_actions=[ResumeAction.APPROVE],
                data={"node": "schedule", "wave": state.get("wave", 0)},
            )
        )
        await deps.steering.set_pause(state["run_id"], False)
        return Command(goto="schedule", update={"status": RunStatus.EXECUTING.value})

    return pause


def build_graph(
    deps: GraphDeps, checkpointer: BaseCheckpointSaver[Any] | None = None
) -> CompiledStateGraph[Any, Any, Any, Any]:
    g = StateGraph(RunState)
    nodes: dict[str, tuple[NodeFn, tuple[str, ...]]] = {
        "prepare_repo": (make_prepare_repo(deps), ("planner", "escalate")),
        "planner": (make_planner(deps), ("approve_plan", "ask_human", "coordinator")),
        "approve_plan": (make_approve_plan(deps), ("architect", "planner", "scaffold")),
        "architect": (make_architect(deps), ("approve_design", "ask_human", "coordinator")),
        "approve_design": (make_approve_design(deps), ("scaffold", "architect")),
        "ask_human": (
            make_ask_human(deps, scratch_key="scratch", pending_key="pending_question"),
            ("planner", "architect"),
        ),
        "coordinator": (make_run_coordinator(deps), ("planner", "architect", "escalate")),
        "escalate": (make_run_escalate(deps), ("prepare_repo", "planner", "architect", END)),
        "scaffold": (make_scaffold(deps), ("schedule",)),
        "schedule": (make_schedule(deps), ("task_worker", "integration", "pause")),
        "pause": (make_pause(deps), ("schedule",)),
        "integration": (make_integration(deps), ("gates",)),
        "gates": (make_gates(deps), ("approve_final", "schedule", "github_delivery")),
        "approve_final": (make_approve_final(deps), ("github_delivery", "schedule")),
        "github_delivery": (
            make_github_delivery(deps),
            ("done", "delivery_failed", "watch_pr", "report_followup"),
        ),
        "watch_pr": (make_watch_pr(deps), ("followup", "done", "watch_pr")),
        "followup": (
            make_followup(deps),
            ("approve_followup", "schedule", "report_followup"),
        ),
        "approve_followup": (make_approve_followup(deps), ("schedule", "report_followup")),
        "report_followup": (make_report_followup(deps), ("watch_pr",)),
        "delivery_failed": (make_delivery_failed(deps), ("github_delivery", "done")),
        "done": (make_done(deps), (END,)),
    }
    for name, (fn, destinations) in nodes.items():
        g.add_node(name, cast(Any, instrument(deps, name, fn)), destinations=destinations)
    g.add_node("task_worker", build_task_subgraph(deps))
    g.add_conditional_edges(START, start_node, ["prepare_repo", "planner"])
    g.add_edge("task_worker", "schedule")
    return g.compile(checkpointer=checkpointer)


def start_node(state: dict[str, Any]) -> str:
    """Existing repositories are cloned and analyzed before planning."""
    return "prepare_repo" if state.get("target") == "existing" else "planner"


def initial_state(
    run_id: str,
    request: str,
    repo_target: str,
    create_repo: bool,
    *,
    target: str = "new",
    mode: str = "full",
    issue: dict[str, Any] | None = None,
) -> RunState:
    existing = target == "existing"
    state = RunState(
        run_id=run_id,
        request=request,
        repo_target=repo_target,
        create_repo=create_repo and not existing,
        target=target,
        mode=mode if existing else "full",
        repo_info=None,
        gate_baseline=None,
        gates=None,
        gate_fix_rounds=0,
        issue=issue,
        followup=None,
        followup_items=None,
        followup_triage=None,
        followup_active=False,
        status=(RunStatus.PREPARING if existing else RunStatus.PLANNING).value,
        plan=None,
        design=None,
        tasks={},
        qa_log=[],
        scratch={},
        errors=[],
        pending_question=None,
        escalation=None,
        followups=0,
        pr_url=None,
        final_approved=False,
        delivery_error=None,
        wave=0,
        plan_changes=[],
        plan_changes_applied=0,
        coordinator_retries={},
    )
    if not existing:
        state["base_branch"] = "main"
    return state
