"""The fixed backbone graph.

planner -> approve_plan -> architect -> approve_design -> scaffold -> schedule
        -> task_worker (Send, per task) -> schedule -> ... -> integration
        -> approve_final -> github_delivery -> done

ask_human and coordinator are side exits that return to the calling node.
"""

from __future__ import annotations

from typing import Any, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Send

from app.db.models import RunStatus
from app.graph.coordinator import make_run_coordinator
from app.graph.nodes.approvals import make_approve_design, make_approve_final, make_approve_plan
from app.graph.nodes.architect import make_architect
from app.graph.nodes.finish import make_done, make_github_delivery, make_integration
from app.graph.nodes.human import make_ask_human
from app.graph.nodes.planner import make_planner
from app.graph.nodes.scaffold import make_scaffold
from app.graph.runtime import GraphDeps, NodeFn, instrument
from app.graph.state import RunState, TaskState, TaskStatus, dump, get_plan
from app.graph.task_subgraph import build_task_subgraph

# Phase 2 dispatches one task at a time; Phase 5 raises this to MAX_PARALLEL_DEVS.
SEQUENTIAL_DISPATCH = 1


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
            if any(s in (TaskStatus.FAILED, TaskStatus.BLOCKED) for s in deps):
                ts.status = TaskStatus.BLOCKED
                ts.error = "a dependency failed"
                updates[tid] = dump(ts)
            elif all(s is TaskStatus.MERGED for s in deps):
                ready.append(tid)
    return ready, updates


def make_schedule(deps: GraphDeps) -> NodeFn:
    async def schedule(state: dict[str, Any]) -> Command[Any]:
        ready, updates = ready_tasks(state)
        if not ready:
            return Command(
                goto="integration", update={"tasks": updates, "status": RunStatus.INTEGRATING}
            )
        plan = get_plan(state)
        sends = []
        for tid in ready[:SEQUENTIAL_DISPATCH]:
            ts = TaskState.model_validate(state["tasks"][tid])
            ts.status = TaskStatus.IN_PROGRESS
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
                    },
                )
            )
        return Command(goto=sends, update={"tasks": updates})

    return schedule


def build_graph(
    deps: GraphDeps, checkpointer: BaseCheckpointSaver[Any] | None = None
) -> CompiledStateGraph[Any, Any, Any, Any]:
    g = StateGraph(RunState)
    nodes: dict[str, tuple[NodeFn, tuple[str, ...]]] = {
        "planner": (make_planner(deps), ("approve_plan", "ask_human", "coordinator")),
        "approve_plan": (make_approve_plan(deps), ("architect", "planner")),
        "architect": (make_architect(deps), ("approve_design", "ask_human", "coordinator")),
        "approve_design": (make_approve_design(deps), ("scaffold", "architect")),
        "ask_human": (
            make_ask_human(deps, scratch_key="scratch", pending_key="pending_question"),
            ("planner", "architect"),
        ),
        "coordinator": (make_run_coordinator(deps), ("planner", "architect", END)),
        "scaffold": (make_scaffold(deps), ("schedule",)),
        "schedule": (make_schedule(deps), ("task_worker", "integration")),
        "integration": (make_integration(deps), ("approve_final",)),
        "approve_final": (make_approve_final(deps), ("github_delivery", "schedule")),
        "github_delivery": (make_github_delivery(deps), ("done",)),
        "done": (make_done(deps), (END,)),
    }
    for name, (fn, destinations) in nodes.items():
        g.add_node(name, cast(Any, instrument(deps, name, fn)), destinations=destinations)
    g.add_node("task_worker", build_task_subgraph(deps))
    g.add_edge(START, "planner")
    g.add_edge("task_worker", "schedule")
    return g.compile(checkpointer=checkpointer)


def initial_state(run_id: str, request: str, repo_target: str, create_repo: bool) -> RunState:
    return RunState(
        run_id=run_id,
        request=request,
        repo_target=repo_target,
        create_repo=create_repo,
        status=RunStatus.PLANNING,
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
    )
