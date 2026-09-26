"""Helpers shared by the task-subgraph nodes.

Each task works in its own git worktree (WORKSPACES_DIR/<run_id>/worktrees/<task_id>) on its
own branch; the run's main repository (WORKSPACES_DIR/<run_id>/repo) stays on the integration
branch and is only touched under `repo_lock` (worktree add/remove, merges, re-indexing).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.types import Command

from app.events.types import EventType
from app.graph.layout import LayoutEntry, resolve_layout, sandbox_target
from app.graph.runtime import GraphDeps
from app.graph.state import Design, Plan, PlanTask, TaskState, TaskStatus, dump
from app.sandbox.runner import SandboxTarget
from app.tools.git import GitRepo
from app.tools.workspace import Workspace

TEST_GLOBS = {"python": "*tests/*", "java": "*src/test/*", "angular": "*.spec.ts"}


@dataclass
class TaskCtx:
    run_id: str
    task: PlanTask
    ts: TaskState
    plan: Plan
    design: Design
    main_root: Path  # the run's main repository (integration branch)
    worktree: Path  # this task's worktree
    integration_branch: str
    layout: dict[str, LayoutEntry]

    @property
    def workspace(self) -> Workspace:
        return Workspace(self.worktree)

    @property
    def repo(self) -> GitRepo:
        """The task's worktree (commits, diffs, conflict resolution)."""
        return GitRepo(self.worktree)

    @property
    def main_repo(self) -> GitRepo:
        return GitRepo(self.main_root)

    @property
    def target(self) -> SandboxTarget:
        return sandbox_target(self.run_id, self.task.id, self.worktree, self.design, self.layout)

    @property
    def project(self) -> LayoutEntry:
        """The layout entry (template + sub-directory) for this task's stack."""
        return self.layout.get(self.task.stack) or next(iter(self.layout.values()))

    @property
    def branch(self) -> str:
        return self.ts.branch or task_branch(self.run_id, self.task.id)

    def update(self, **extra: Any) -> dict[str, Any]:
        return {"tasks": {self.task.id: dump(self.ts)}, **extra}


def task_query(task: PlanTask) -> str:
    """Retrieval query describing a task."""
    return f"{task.title}\n{task.description}\n{' '.join(task.target_files)}"


def task_branch(run_id: str, task_id: str) -> str:
    return f"devcrew/{run_id[:8]}/task-{task_id}"


def worktree_path(main_root: Path, task_id: str) -> Path:
    return main_root.parent / "worktrees" / task_id


def load_task_ctx(deps: GraphDeps, state: dict[str, Any]) -> TaskCtx:
    task = PlanTask.model_validate(state["task"])
    main_root = Path(state["workspace"])
    design = Design.model_validate(state["design"])
    ts = TaskState.model_validate(state["tasks"][task.id])
    return TaskCtx(
        run_id=state["run_id"],
        task=task,
        ts=ts,
        plan=Plan.model_validate(state["plan"]),
        design=design,
        main_root=main_root,
        worktree=Path(ts.worktree) if ts.worktree else worktree_path(main_root, task.id),
        integration_branch=state["integration_branch"],
        layout=resolve_layout(design, deps.templates),
    )


async def to_coordinator(
    deps: GraphDeps,
    ctx: TaskCtx,
    node: str,
    reason: str,
    *,
    kind: str = "agent_error",
    extra: dict[str, Any] | None = None,
) -> Command[str]:
    """Record the failure (error event + state) and hand the task to the Coordinator.

    kind: iteration_limit | agent_error | merge_conflict. `node` is retried on "retry".
    """
    await deps.emit(
        ctx.run_id, EventType.ERROR, node=node, task_id=ctx.task.id, message=reason, kind=kind
    )
    ctx.ts.status = TaskStatus.NEEDS_HUMAN
    return Command(
        goto="coordinator",
        update=ctx.update(
            escalation_reason=reason,
            escalation_kind=kind,
            escalation_node=node,
            errors=[f"task {ctx.task.id}: {reason}"],
            task_scratch={"developer": None},
            **(extra or {}),
        ),
    )
