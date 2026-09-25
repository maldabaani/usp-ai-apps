"""Human approval gates: plan, design, final result."""

from __future__ import annotations

from collections import Counter
from typing import Any

from langgraph.types import Command
from pydantic import ValidationError

from app.db.models import RunStatus
from app.graph.interrupts import InterruptKind, InterruptRequest, ResumeAction, request_input
from app.graph.runtime import GraphDeps, NodeFn
from app.graph.state import (
    Design,
    Plan,
    PlanTask,
    TaskState,
    dump,
    get_design,
    get_plan,
)

APPROVE_REJECT_EDIT = [ResumeAction.APPROVE, ResumeAction.REJECT, ResumeAction.EDIT]


def make_approve_plan(deps: GraphDeps) -> NodeFn:
    async def approve_plan(state: dict[str, Any]) -> Command[str]:
        plan = get_plan(state)
        error: str | None = None
        while True:
            payload = request_input(
                InterruptRequest(
                    kind=InterruptKind.APPROVAL,
                    artifact="plan",
                    title="Approve the plan",
                    allowed_actions=APPROVE_REJECT_EDIT,
                    data={"plan": dump(plan), "layers": plan.layers()},
                    error=error,
                )
            )
            if payload.action is ResumeAction.APPROVE:
                return Command(goto="architect", update={"status": RunStatus.DESIGNING.value})
            if payload.action is ResumeAction.REJECT:
                return Command(
                    goto="planner",
                    update={"plan_feedback": payload.feedback, "status": RunStatus.PLANNING.value},
                )
            try:
                edited = Plan.model_validate(payload.artifact)
            except ValidationError as exc:
                error = f"edited plan is invalid: {exc}"
                continue
            return Command(
                goto="architect", update={"plan": dump(edited), "status": RunStatus.DESIGNING.value}
            )

    return approve_plan


def make_approve_design(deps: GraphDeps) -> NodeFn:
    async def approve_design(state: dict[str, Any]) -> Command[str]:
        design = get_design(state)
        error: str | None = None
        while True:
            payload = request_input(
                InterruptRequest(
                    kind=InterruptKind.APPROVAL,
                    artifact="design",
                    title="Approve the design",
                    allowed_actions=APPROVE_REJECT_EDIT,
                    data={"design": dump(design)},
                    error=error,
                )
            )
            if payload.action is ResumeAction.APPROVE:
                return Command(goto="scaffold", update={"status": RunStatus.SCAFFOLDING.value})
            if payload.action is ResumeAction.REJECT:
                return Command(
                    goto="architect",
                    update={
                        "design_feedback": payload.feedback,
                        "status": RunStatus.DESIGNING.value,
                    },
                )
            try:
                edited = Design.model_validate(
                    payload.artifact,
                    context={
                        "templates": deps.templates.ids_by_stack(),
                        "task_stacks": {t.stack for t in get_plan(state).tasks},
                    },
                )
            except ValidationError as exc:
                error = f"edited design is invalid: {exc}"
                continue
            return Command(
                goto="scaffold",
                update={"design": dump(edited), "status": RunStatus.SCAFFOLDING.value},
            )

    return approve_design


def followup_task(plan: Plan, feedback: str, number: int) -> PlanTask:
    stack = Counter(t.stack for t in plan.tasks).most_common(1)[0][0]
    return PlanTask(
        id=f"FIX{number}",
        title="Address final review feedback",
        description=f"The human reviewed the integrated result and asked for:\n{feedback}",
        target_files=[],
        depends_on=[],
        stack=stack,
    )


def make_approve_final(deps: GraphDeps) -> NodeFn:
    async def approve_final(state: dict[str, Any]) -> Command[str]:
        plan = get_plan(state)
        payload = request_input(
            InterruptRequest(
                kind=InterruptKind.APPROVAL,
                artifact="final",
                title="Approve the final result",
                allowed_actions=[ResumeAction.APPROVE, ResumeAction.REJECT],
                data={
                    "integration_branch": state.get("integration_branch"),
                    "tasks": state.get("tasks", {}),
                    "integration": state.get("integration"),
                },
            )
        )
        if payload.action is ResumeAction.APPROVE:
            return Command(
                goto="github_delivery",
                update={"status": RunStatus.DELIVERING.value, "final_approved": True},
            )
        # Rejected: schedule a follow-up task carrying the feedback, then integrate again.
        number = state.get("followups", 0) + 1
        task = followup_task(plan, payload.feedback or "", number)
        new_plan = plan.model_copy(update={"tasks": [*plan.tasks, task]})
        return Command(
            goto="schedule",
            update={
                "plan": dump(new_plan),
                "tasks": {task.id: dump(TaskState(id=task.id))},
                "followups": number,
                "final_feedback": payload.feedback,
                "status": RunStatus.EXECUTING.value,
            },
        )

    return approve_final
