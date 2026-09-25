"""Plan changes decided by the Coordinator (replan / split), applied by the scheduler.

Changes travel from task subgraphs to the backbone through the append-only `plan_changes`
channel, so parallel tasks never write the `plan` key concurrently.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.graph.state import Plan, PlanTask, RevisedTask, TaskState, TaskStatus, dump


def replan_change(task_id: str, revised: RevisedTask, guidance: str) -> dict[str, Any]:
    return {"kind": "replan", "task_id": task_id, "revised": dump(revised), "guidance": guidance}


def split_change(task_id: str, subtasks: Sequence[PlanTask]) -> dict[str, Any]:
    return {"kind": "split", "task_id": task_id, "subtasks": [dump(t) for t in subtasks]}


def apply_replan(plan: Plan, task_id: str, revised: RevisedTask) -> Plan:
    tasks = [
        t.model_copy(update=revised.model_dump()) if t.id == task_id else t for t in plan.tasks
    ]
    return Plan.model_validate({**plan.model_dump(), "tasks": [dump(t) for t in tasks]})


def apply_split(plan: Plan, task_id: str, subtasks: Sequence[PlanTask]) -> Plan:
    """Replace `task_id` by `subtasks`; its dependents now depend on every subtask."""
    original = plan.task(task_id)
    sub_ids = [t.id for t in subtasks]
    tasks: list[PlanTask] = []
    for t in plan.tasks:
        if t.id == task_id:
            continue
        if task_id in t.depends_on:
            deps = [d for d in t.depends_on if d != task_id] + sub_ids
            t = t.model_copy(update={"depends_on": deps})
        tasks.append(t)
    subs = [t.model_copy(update={"story_ids": t.story_ids or original.story_ids}) for t in subtasks]
    return Plan.model_validate(
        {**plan.model_dump(), "tasks": [dump(t) for t in [*tasks, *subs]]}
    )  # raises on cycles / unknown ids


def apply_changes(
    plan: Plan, tasks: Mapping[str, dict[str, Any]], changes: Sequence[Mapping[str, Any]]
) -> tuple[Plan, dict[str, dict[str, Any]], list[str]]:
    """Apply changes in order. Returns (plan, task-state updates, errors)."""
    updates: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for change in changes:
        task_id = str(change["task_id"])
        ts = TaskState.model_validate(updates.get(task_id) or tasks[task_id])
        try:
            if change["kind"] == "replan":
                plan = apply_replan(plan, task_id, RevisedTask.model_validate(change["revised"]))
                ts = ts.model_copy(
                    update={
                        "status": TaskStatus.PENDING,
                        "iterations": 0,
                        "reset_branch": True,
                        "feedback": f"Coordinator replanned this task: {change['guidance']}",
                        "review": None,
                        "test_results": None,
                        "conflict_rounds": 0,
                        "conflict_files": [],
                    }
                )
            elif change["kind"] == "split":
                subtasks = [PlanTask.model_validate(t) for t in change["subtasks"]]
                plan = apply_split(plan, task_id, subtasks)
                ts = ts.model_copy(update={"status": TaskStatus.SPLIT})
                for sub in subtasks:
                    updates[sub.id] = dump(TaskState(id=sub.id))
            else:
                raise ValueError(f"unknown plan change {change['kind']!r}")
        except (ValueError, KeyError) as exc:
            ts = ts.model_copy(
                update={"status": TaskStatus.FAILED, "error": f"invalid plan change: {exc}"}
            )
            errors.append(f"task {task_id}: invalid plan change: {exc}")
        updates[task_id] = dump(ts)
    return plan, updates, errors
