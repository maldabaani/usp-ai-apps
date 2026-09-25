"""Helpers shared by the task-subgraph nodes."""

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


@dataclass
class TaskCtx:
    run_id: str
    task: PlanTask
    ts: TaskState
    plan: Plan
    design: Design
    workspace: Workspace
    repo: GitRepo
    integration_branch: str
    layout: dict[str, LayoutEntry]

    @property
    def target(self) -> SandboxTarget:
        """This task's sandbox (Phase 5 points workdir at the task's own worktree)."""
        return sandbox_target(
            self.run_id, self.task.id, self.workspace.root, self.design, self.layout
        )

    @property
    def project(self) -> LayoutEntry:
        """The layout entry (template + sub-directory) for this task's stack."""
        return self.layout.get(self.task.stack) or next(iter(self.layout.values()))

    @property
    def branch(self) -> str:
        return self.ts.branch or task_branch(self.run_id, self.task.id)

    def update(self, **extra: Any) -> dict[str, Any]:
        return {"tasks": {self.task.id: dump(self.ts)}, **extra}


TEST_GLOBS = {"python": "*tests/*", "java": "*src/test/*", "angular": "*.spec.ts"}


def task_query(task: PlanTask) -> str:
    """Retrieval query describing a task."""
    return f"{task.title}\n{task.description}\n{' '.join(task.target_files)}"


def task_branch(run_id: str, task_id: str) -> str:
    return f"devcrew/{run_id[:8]}/task-{task_id}"


def load_task_ctx(deps: GraphDeps, state: dict[str, Any]) -> TaskCtx:
    task = PlanTask.model_validate(state["task"])
    root = Path(state["workspace"])
    design = Design.model_validate(state["design"])
    return TaskCtx(
        run_id=state["run_id"],
        task=task,
        ts=TaskState.model_validate(state["tasks"][task.id]),
        plan=Plan.model_validate(state["plan"]),
        design=design,
        workspace=Workspace(root),
        repo=GitRepo(root),
        integration_branch=state["integration_branch"],
        layout=resolve_layout(design, deps.templates),
    )


async def to_coordinator(deps: GraphDeps, ctx: TaskCtx, node: str, reason: str) -> Command[str]:
    """Record the failure (error event + state) and hand the task to the Coordinator."""
    await deps.emit(ctx.run_id, EventType.ERROR, node=node, task_id=ctx.task.id, message=reason)
    ctx.ts.status = TaskStatus.NEEDS_HUMAN
    return Command(
        goto="coordinator",
        update=ctx.update(
            escalation_reason=reason,
            errors=[f"task {ctx.task.id}: {reason}"],
            task_scratch={"developer": None},
        ),
    )
