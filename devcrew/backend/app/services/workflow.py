"""The run as a workflow graph (for the UI's node view), derived from graph state + events.

Everything here is recomputed from persisted data (run status, checkpointed state, pending
interrupts and the event log), so the graph is identical after a reload or a backend restart.

Stages run left to right: requirements -> planner -> plan approval -> architect -> design
approval -> scaffold -> one node per plan task (wired by dependencies) -> integration -> final
approval -> GitHub PR. The run status decides which stage is current; events add timing, run
counts and a short activity feed per node.

After the PR, follow-up rounds on review comments / CI / conflicts are appended to the right:
PR -> round 1 triage -> round 1 tasks -> round 1 push & reply -> round 2 ... -> watching PR.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from itertools import pairwise
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.db.models import RunStatus
from app.events.types import Event, EventType
from app.graph.runner import PendingInterrupt

NodeStatus = Literal["pending", "running", "waiting", "done", "failed", "skipped"]
NodeKind = Literal["input", "agent", "approval", "system", "task", "output"]

ACTIVITY_LIMIT = 8
DEVELOPMENT = "development"  # placeholder stage until the plan (and so the tasks) exists

# (node id, kind, label, graph node that emits its events)
STAGES: list[tuple[str, NodeKind, str, str | None]] = [
    ("requirements", "input", "Requirements", None),
    ("prepare_repo", "system", "Repository", "prepare_repo"),
    ("planner", "agent", "Planner", "planner"),
    ("approve_plan", "approval", "Plan approval", "approve_plan"),
    ("architect", "agent", "Architect", "architect"),
    ("approve_design", "approval", "Design approval", "approve_design"),
    ("scaffold", "system", "Scaffold", "scaffold"),
    (DEVELOPMENT, "system", "Development", None),
    ("integration", "system", "Integration tests", "integration"),
    ("gates", "system", "Quality gates", "gates"),
    ("approve_final", "approval", "Final approval", "approve_final"),
    ("delivery", "output", "GitHub PR", "github_delivery"),
]
STAGE_IDS = [s[0] for s in STAGES]
PRIMARY_NODE = {sid: graph for sid, _, _, graph in STAGES if graph}
GRAPH_NODE_TO_STAGE = {graph: sid for sid, _, _, graph in STAGES if graph} | {
    "delivery_failed": "delivery",
    "done": "delivery",
    "schedule": DEVELOPMENT,
}

STATUS_STAGE: dict[RunStatus, tuple[str, NodeStatus]] = {
    RunStatus.PENDING: ("planner", "pending"),
    RunStatus.PREPARING: ("prepare_repo", "running"),
    RunStatus.CHECKING: ("gates", "running"),
    RunStatus.PLANNING: ("planner", "running"),
    RunStatus.AWAITING_PLAN_APPROVAL: ("approve_plan", "waiting"),
    RunStatus.DESIGNING: ("architect", "running"),
    RunStatus.AWAITING_DESIGN_APPROVAL: ("approve_design", "waiting"),
    RunStatus.SCAFFOLDING: ("scaffold", "running"),
    RunStatus.EXECUTING: (DEVELOPMENT, "running"),
    RunStatus.INTEGRATING: ("integration", "running"),
    RunStatus.AWAITING_FINAL_APPROVAL: ("approve_final", "waiting"),
    RunStatus.DELIVERING: ("delivery", "running"),
}

TASK_STATUS: dict[str, NodeStatus] = {
    "pending": "pending",
    "in_progress": "running",
    "in_review": "running",
    "testing": "running",
    "needs_human": "waiting",
    "merged": "done",
    "failed": "failed",
    "blocked": "skipped",
    "split": "skipped",
}
TASK_STEPS = {
    "prepare": "preparing worktree",
    "developer": "developer",
    "ask_human": "waiting for your answer",
    "reviewer": "reviewer",
    "qa": "QA tests",
    "merge": "merging",
    "coordinator": "coordinator",
    "escalate": "escalated to you",
}
RUN_LEVEL_HELPERS = {"ask_human", "coordinator", "escalate"}
ARTIFACT_STAGE = {"plan": "approve_plan", "design": "approve_design", "final": "approve_final"}
WATCH = "watch"
ROUND_TASK_RE = re.compile(r"^R(\d+)-")
# graph nodes that belong to a follow-up round once one started
ROUND_TRIAGE_NODES = {"followup", "approve_followup"}
ROUND_PUSH_NODES = {
    "schedule",
    "integration",
    "gates",
    "github_delivery",
    "delivery_failed",
    "report_followup",
}
CLOSED_AS = {
    "merged": "pull request merged",
    "closed": "pull request closed",
    "stopped": "stopped watching",
}


class Activity(BaseModel):
    at: datetime
    kind: str
    text: str
    ok: bool | None = None


class WorkflowNode(BaseModel):
    id: str
    kind: NodeKind
    label: str
    status: NodeStatus = "pending"
    detail: str | None = None
    step: str | None = None  # task nodes: the current sub-step (developer, reviewer, qa, ...)
    task_id: str | None = None
    stack: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    runs: int = 0  # how often the node started (re-runs after a rejection, retries)
    counters: dict[str, int] = Field(default_factory=dict)
    pending_interrupt_ids: list[str] = Field(default_factory=list)
    activity: list[Activity] = Field(default_factory=list)


class WorkflowEdge(BaseModel):
    id: str
    source: str
    target: str
    kind: Literal["flow", "loop"] = "flow"
    active: bool = False
    label: str | None = None


class Workflow(BaseModel):
    run_id: str
    status: str
    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge]
    attention: list[str] = Field(default_factory=list)  # node ids waiting for the human


def task_node_id(task_id: str) -> str:
    return f"task:{task_id}"


def triage_node_id(round_: int) -> str:
    return f"followup:{round_}"


def push_node_id(round_: int) -> str:
    return f"push:{round_}"


def task_round(task_id: str) -> int | None:
    match = ROUND_TASK_RE.match(task_id)
    return int(match.group(1)) if match else None


def _short(value: Any, limit: int = 120) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _tool_args(args: Any) -> str:
    if isinstance(args, Mapping):
        for key in ("path", "command", "query", "question", "step", "target"):
            if args.get(key):
                return _short(args[key], 80)
        return _short(args, 80) if args else ""
    return _short(args, 80)


def describe(event: Event) -> Activity | None:
    """A one-line summary of an event for a node's activity feed (None: not shown)."""
    p = event.payload
    match event.type:
        case EventType.TOOL_CALL:
            text = f"{p.get('tool')}({_tool_args(p.get('args'))})"
        case EventType.TOOL_RESULT:
            ok = bool(p.get("ok", True))
            state = "skipped" if p.get("skipped") else ("ok" if ok else "failed")
            result = _short(p.get("result", ""), 100)
            return Activity(
                at=event.created_at,
                kind=event.type.value,
                text=f"{p.get('tool')}: {state}" + (f" — {result}" if result else ""),
                ok=ok,
            )
        case EventType.QUESTION:
            text = f"asks {p.get('target', 'human')}: {_short(p.get('question', ''))}"
        case EventType.ANSWER:
            text = f"answer: {_short(p.get('answer', ''))}"
        case EventType.MERGE:
            text = f"merged into {p.get('into')}"
        case EventType.ERROR:
            return Activity(
                at=event.created_at,
                kind=event.type.value,
                text=_short(p.get("message", "error"), 160),
                ok=False,
            )
        case EventType.AWAITING_INPUT:
            if p.get("kind") == "watch":
                text = "watching for review comments, CI results and conflicts"
            else:
                text = f"waiting for you: {p.get('title', '')}"
        case _:
            return None
    return Activity(at=event.created_at, kind=event.type.value, text=text)


def _interrupt_stage(value: Mapping[str, Any]) -> str | None:
    """Which node a pending interrupt belongs to."""
    data = value.get("data") or {}
    if value.get("kind") == "watch":
        return WATCH
    if value.get("artifact") == "followup":
        return triage_node_id(int(data.get("round") or 1))
    if data.get("task_id"):
        return task_node_id(str(data["task_id"]))
    artifact = value.get("artifact")
    if artifact in ARTIFACT_STAGE:
        return ARTIFACT_STAGE[str(artifact)]
    node = data.get("node") or data.get("role")
    if node in GRAPH_NODE_TO_STAGE:
        return GRAPH_NODE_TO_STAGE[str(node)]
    return None


def build_workflow(
    *,
    run_id: str,
    status: str,
    request: str,
    created_at: datetime | None,
    state: Mapping[str, Any],
    events: Sequence[Event],
    pending: Sequence[PendingInterrupt],
    pr_url: str | None,
    max_dev_iterations: int,
) -> Workflow:
    run_status = RunStatus(status)
    plan_tasks: list[dict[str, Any]] = list((state.get("plan") or {}).get("tasks") or [])
    task_states: dict[str, dict[str, Any]] = dict(state.get("tasks") or {})
    task_ids = [str(t["id"]) for t in plan_tasks]
    task_ids += [tid for tid in task_states if tid not in task_ids]  # e.g. split parents

    existing = state.get("target") == "existing"
    followup = state.get("followup") or {}
    rounds = int(followup.get("round") or 0)
    watching = bool(followup) or run_status is RunStatus.WATCHING
    nodes: dict[str, WorkflowNode] = {}
    for sid, kind, label, _ in STAGES:
        if (sid == DEVELOPMENT and task_ids) or (sid == "prepare_repo" and not existing):
            continue
        nodes[sid] = WorkflowNode(id=sid, kind=kind, label=label)
    plan_by_id = {str(t["id"]): t for t in plan_tasks}
    for tid in task_ids:
        spec = plan_by_id.get(tid, {})
        nodes[task_node_id(tid)] = WorkflowNode(
            id=task_node_id(tid),
            kind="task",
            label=f"{tid} · {spec.get('title', tid)}",
            task_id=tid,
            stack=spec.get("stack"),
        )
    for n in range(1, rounds + 1):
        nodes[triage_node_id(n)] = WorkflowNode(
            id=triage_node_id(n), kind="agent", label=f"Round {n} · triage"
        )
        nodes[push_node_id(n)] = WorkflowNode(
            id=push_node_id(n), kind="output", label=f"Round {n} · push & reply"
        )
    if watching:
        nodes[WATCH] = WorkflowNode(id=WATCH, kind="system", label="Watching PR")

    _apply_events(nodes, events)
    # once the PR exists the main pipeline is finished; follow-up rounds carry the status
    _apply_stage_status(
        nodes, RunStatus.COMPLETED if watching else run_status, pending, events, pr_url
    )
    _apply_task_status(nodes, task_states, run_status, max_dev_iterations)
    if watching:
        _apply_followup(nodes, followup, rounds, run_status, pending, task_ids)

    requirements = nodes["requirements"]
    requirements.status = "done"
    requirements.started_at = requirements.finished_at = created_at
    requirements.detail = f"{len(request):,} characters"
    if existing:
        mode = "quick fix" if state.get("mode") == "quick" else "full"
        requirements.detail += f" · {mode} change"
    _apply_repository_and_gates(nodes, state, run_status)

    attention: list[str] = []
    for p in pending:
        target = _interrupt_stage(p.value)
        if target in nodes:
            nodes[target].pending_interrupt_ids.append(p.id)
            if target == WATCH:  # the poller answers it, not the human
                continue
            nodes[target].status = "waiting"
            attention.append(target)

    edges = _edges(nodes, plan_tasks, task_ids)
    edges += _followup_edges(nodes, task_ids, rounds)
    return Workflow(
        run_id=run_id,
        status=run_status.value,
        nodes=list(nodes.values()),
        edges=edges,
        attention=list(dict.fromkeys(attention)),
    )


def _apply_repository_and_gates(
    nodes: dict[str, WorkflowNode], state: Mapping[str, Any], status: RunStatus
) -> None:
    repo = state.get("repo_info") or {}
    if "prepare_repo" in nodes and repo:
        projects = ", ".join(f"{p['stack']} ({p['path']})" for p in repo.get("projects") or [])
        nodes["prepare_repo"].detail = f"{projects} · base {repo.get('base_branch')}"
    if state.get("target") == "existing" and state.get("mode") == "quick":
        for sid in ("architect", "approve_design"):
            nodes[sid].status = "skipped"
            nodes[sid].detail = "quick fix: no design step"
    gates = state.get("gates") or {}
    results = gates.get("results") or []
    if results:
        counts: dict[str, int] = {}
        for r in results:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        node = nodes["gates"]
        node.detail = ", ".join(f"{n} {s}" for s, n in sorted(counts.items()))
        node.counters["fix_rounds"] = int(state.get("gate_fix_rounds") or 0)
        if counts.get("failed") and node.status == "done" and status is not RunStatus.CHECKING:
            node.status = (
                "failed"
                if any(r["status"] == "failed" and not r.get("allowable", True) for r in results)
                else "done"
            )
            node.detail = "failed: " + ", ".join(
                r["name"] for r in results if r["status"] == "failed"
            )


def _event_target(
    event: Event, nodes: Mapping[str, WorkflowNode], last_agent: str, round_: int
) -> str | None:
    if event.task_id and task_node_id(event.task_id) in nodes:
        return task_node_id(event.task_id)
    if event.type is EventType.AWAITING_INPUT:
        return _interrupt_stage(event.payload) or last_agent
    node = event.node or ""
    if node in ("watch_pr", "done") and WATCH in nodes:
        return WATCH
    if round_ and node in ROUND_TRIAGE_NODES:
        return triage_node_id(round_)
    if round_ and node in ROUND_PUSH_NODES:
        return push_node_id(round_)
    if node in RUN_LEVEL_HELPERS:  # planner/architect questions, retries and escalations
        return last_agent
    return GRAPH_NODE_TO_STAGE.get(node)


def _apply_events(nodes: dict[str, WorkflowNode], events: Iterable[Event]) -> None:
    last_agent = "planner"
    round_ = 0
    feeds: dict[str, list[Activity]] = {}
    for event in events:
        if event.type is EventType.NODE_STARTED and event.node in ("planner", "architect"):
            last_agent = event.node
        if event.type is EventType.NODE_STARTED and event.node == "followup":
            round_ += 1
        target = _event_target(event, nodes, last_agent, round_)
        if target is None or target not in nodes:
            continue
        node = nodes[target]
        if event.type is EventType.NODE_STARTED:
            if node.started_at is None:
                node.started_at = event.created_at
            if node.kind == "task":
                node.step = event.node
                node.runs += event.node == "developer"  # one per development attempt
            elif event.node == PRIMARY_NODE.get(target):
                node.runs += 1
            continue
        if event.type is EventType.NODE_FINISHED:
            if node.kind != "task" or event.node == "merge":
                node.finished_at = event.created_at
            continue
        activity = describe(event)
        if activity is not None:
            feeds.setdefault(target, []).append(activity)
            if event.type is EventType.QUESTION:
                node.counters["questions"] = node.counters.get("questions", 0) + 1
    for target, feed in feeds.items():
        nodes[target].activity = feed[-ACTIVITY_LIMIT:]


def _last_started_stage(events: Sequence[Event]) -> str | None:
    for event in reversed(events):
        if event.type is EventType.NODE_STARTED and not event.task_id:
            stage = GRAPH_NODE_TO_STAGE.get(event.node or "")
            if stage:
                return stage
    return None


def _apply_stage_status(
    nodes: dict[str, WorkflowNode],
    status: RunStatus,
    pending: Sequence[PendingInterrupt],
    events: Sequence[Event],
    pr_url: str | None,
) -> None:
    stages = [s for s in STAGE_IDS if s in nodes or s == DEVELOPMENT]
    current: str | None
    state: NodeStatus
    if status is RunStatus.COMPLETED:
        current, state = None, "done"
    elif status in STATUS_STAGE:
        current, state = STATUS_STAGE[status]
    elif status is RunStatus.NEEDS_HUMAN:
        targets = [_interrupt_stage(p.value) for p in pending]
        stage = next((t for t in targets if t in STAGE_IDS), None)
        if stage is None and any(t and t.startswith("task:") for t in targets):
            stage = DEVELOPMENT
        current = stage or _last_started_stage(events) or DEVELOPMENT
        state = "waiting"
    else:  # failed / cancelled: the stage that was running when it stopped
        current = _last_started_stage(events) or "planner"
        state = "failed"

    index = stages.index(current) if current in stages else len(stages)
    for i, sid in enumerate(stages):
        node = nodes.get(sid)
        if node is None:  # development without a placeholder: task nodes carry the status
            continue
        if current is None or i < index:
            node.status = "done"
        elif i == index:
            node.status = state
            if status is RunStatus.CANCELLED:
                node.detail = "cancelled"
        else:
            node.status = "skipped" if status.is_terminal else "pending"

    delivery = nodes["delivery"]
    if pr_url:
        delivery.detail = pr_url
    elif status is RunStatus.COMPLETED:
        delivery.status = "skipped"
        delivery.detail = "finished without a PR"
    for sid in ("planner", "architect"):
        node = nodes[sid]
        if node.runs > 1:
            node.detail = f"revised {node.runs - 1} times"


def _apply_task_status(
    nodes: dict[str, WorkflowNode],
    task_states: Mapping[str, Mapping[str, Any]],
    status: RunStatus,
    max_dev_iterations: int,
) -> None:
    for node in nodes.values():
        if node.kind != "task" or node.task_id is None:
            continue
        ts = task_states.get(node.task_id) or {}
        raw = str(ts.get("status") or "pending")
        node_status: NodeStatus = TASK_STATUS.get(raw, "pending")
        if node_status == "pending" and node.step and status is RunStatus.EXECUTING:
            node_status = "running"  # started, state not checkpointed yet
        if node_status == "running" and status.is_terminal:
            node_status = "failed"
            node.detail = "run stopped"
        node.status = node_status
        iterations = int(ts.get("iterations") or 0)
        node.counters.update(
            {
                "iterations": iterations,
                "coordinator_actions": int(ts.get("coordinator_actions") or 0),
                "conflict_rounds": int(ts.get("conflict_rounds") or 0),
            }
        )
        if node_status in ("running", "waiting"):
            step = TASK_STEPS.get(node.step or "", node.step or "starting")
            node.detail = f"{step} · iteration {max(iterations, 1)}/{max_dev_iterations}"
        elif node_status == "done":
            node.step = None
            node.detail = f"merged after {iterations} iteration{'s' if iterations != 1 else ''}"
        elif raw == "split":
            node.detail = "split into smaller tasks"
        elif raw == "blocked":
            node.detail = "blocked by a failed dependency"
        elif raw == "failed":
            node.detail = _short(ts.get("error") or "failed", 120)
        elif node_status == "pending":
            node.detail = "planned"


def _apply_followup(
    nodes: dict[str, WorkflowNode],
    followup: Mapping[str, Any],
    rounds: int,
    status: RunStatus,
    pending: Sequence[PendingInterrupt],
    task_ids: Sequence[str],
) -> None:
    """Statuses of the follow-up round nodes and the watching node."""
    for n in range(1, rounds + 1):
        triage, push = nodes[triage_node_id(n)], nodes[push_node_id(n)]
        round_tasks = [t for t in task_ids if task_round(t) == n]
        triage.detail = (
            f"{len(round_tasks)} task{'s' if len(round_tasks) != 1 else ''}"
            if round_tasks
            else "replies only"
        )
        if n < rounds or status is RunStatus.WATCHING or status is RunStatus.COMPLETED:
            triage.status = push.status = "done"
            continue
        # the current round
        waiting_triage = any(_interrupt_stage(p.value) == triage.id for p in pending)
        if waiting_triage:
            triage.status, push.status = "waiting", "pending"
            triage.detail = "big changes need your approval"
        elif status in (RunStatus.FAILED, RunStatus.CANCELLED):
            triage.status = "done" if round_tasks else "failed"
            push.status = "failed" if push.started_at else "skipped"
        else:
            triage.status = "done" if round_tasks or push.started_at else "running"
            push.status = (
                "running"
                if status
                in (
                    RunStatus.INTEGRATING,
                    RunStatus.CHECKING,
                    RunStatus.DELIVERING,
                )
                or (push.started_at and not round_tasks)
                else "pending"
            )
    watch = nodes[WATCH]
    closed = followup.get("closed_as")
    if closed:
        watch.status = "done"
        watch.detail = CLOSED_AS.get(str(closed), str(closed))
    elif status is RunStatus.WATCHING:
        watch.status = "running"
        watch.detail = f"{rounds} follow-up round{'s' if rounds != 1 else ''}"
    elif status.is_terminal:
        watch.status = "skipped"
    else:
        watch.status = "pending"
    ignored = followup.get("ignored") or []
    if ignored:
        watch.counters["ignored_comments"] = len(ignored)


def _followup_edges(
    nodes: Mapping[str, WorkflowNode], task_ids: Sequence[str], rounds: int
) -> list[WorkflowEdge]:
    """PR -> round 1 triage -> round 1 tasks -> round 1 push -> round 2 ... -> watching."""
    if WATCH not in nodes:
        return []
    edges: list[WorkflowEdge] = []

    def add(source: str, target: str) -> None:
        active = nodes[target].status in ("running", "waiting") and nodes[source].status in (
            "done",
            "running",
        )
        edges.append(
            WorkflowEdge(id=f"{source}->{target}", source=source, target=target, active=active)
        )

    previous = "delivery"
    for n in range(1, rounds + 1):
        triage, push = triage_node_id(n), push_node_id(n)
        add(previous, triage)
        round_tasks = [t for t in task_ids if task_round(t) == n]
        for tid in round_tasks:
            add(triage, task_node_id(tid))
            add(task_node_id(tid), push)
        if not round_tasks:
            add(triage, push)
        previous = push
    add(previous, WATCH)
    return edges


def _edges(
    nodes: Mapping[str, WorkflowNode],
    plan_tasks: Sequence[Mapping[str, Any]],
    task_ids: Sequence[str],
) -> list[WorkflowEdge]:
    edges: list[WorkflowEdge] = []

    def add(source: str, target: str) -> None:
        if source in nodes and target in nodes:
            # animated while work flows into a node that is running or waiting
            active = nodes[target].status in ("running", "waiting") and nodes[source].status in (
                "done",
                "running",
            )
            edges.append(
                WorkflowEdge(id=f"{source}->{target}", source=source, target=target, active=active)
            )

    chain = [
        "requirements",
        "prepare_repo",
        "planner",
        "approve_plan",
        "architect",
        "approve_design",
        "scaffold",
    ]
    chain = [c for c in chain if c in nodes]
    for a, b in pairwise(chain):
        add(a, b)
    tail = ["integration", "gates", "approve_final", "delivery"]
    for a, b in pairwise(tail):
        add(a, b)

    if not task_ids:
        add("scaffold", DEVELOPMENT)
        add(DEVELOPMENT, "integration")
    else:
        deps = {str(t["id"]): [str(d) for d in t.get("depends_on") or []] for t in plan_tasks}
        known = set(task_ids)
        has_dependents = {d for ds in deps.values() for d in ds if d in known}
        for tid in task_ids:
            if task_round(tid) is not None:  # follow-up tasks hang off their round
                continue
            inner = [d for d in deps.get(tid, []) if d in known]
            if not inner:
                add("scaffold", task_node_id(tid))
            for d in inner:
                add(task_node_id(d), task_node_id(tid))
            if tid not in has_dependents:
                add(task_node_id(tid), "integration")

    for sid, gate in (("planner", "approve_plan"), ("architect", "approve_design")):
        if nodes[sid].runs > 1:
            edges.append(
                WorkflowEdge(
                    id=f"{gate}->{sid}:loop",
                    source=gate,
                    target=sid,
                    kind="loop",
                    label=f"rejected {nodes[sid].runs - 1}x",
                )
            )
    fix_tasks = [t for t in task_ids if t.startswith("GATEFIX")]
    if fix_tasks:
        edges.append(
            WorkflowEdge(
                id=f"gates->{task_node_id(fix_tasks[0])}:loop",
                source="gates",
                target=task_node_id(fix_tasks[0]),
                kind="loop",
                label=f"auto-fix {len(fix_tasks)}x",
            )
        )
    integration_runs = nodes["integration"].runs - len(fix_tasks)
    if integration_runs > 1:
        main = [t for t in task_ids if task_round(t) is None]
        first = task_node_id(main[-1]) if main else DEVELOPMENT
        edges.append(
            WorkflowEdge(
                id=f"approve_final->{first}:loop",
                source="approve_final",
                target=first,
                kind="loop",
                label=f"follow-up {integration_runs - 1}x",
            )
        )
    return edges
