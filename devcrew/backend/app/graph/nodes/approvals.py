"""Human approval gates: plan, design, final result."""

from __future__ import annotations

from collections import Counter
from typing import Any

from langgraph.types import Command
from pydantic import ValidationError

from app.db.models import RunStatus
from app.gates.checks import GateReport
from app.graph.interrupts import InterruptKind, InterruptRequest, ResumeAction, request_input
from app.graph.nodes.planner import plan_validation_context
from app.graph.nodes.repository import quick_design
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


def after_plan(state: dict[str, Any], plan: Plan) -> Command[str]:
    """Full runs go to the Architect; quick fixes on existing repositories skip it."""
    if state.get("mode") == "quick" and state.get("repo_info"):
        return Command(
            goto="scaffold",
            update={
                "plan": dump(plan),
                "design": dump(quick_design(state, plan)),
                "status": RunStatus.SCAFFOLDING.value,
            },
        )
    return Command(
        goto="architect", update={"plan": dump(plan), "status": RunStatus.DESIGNING.value}
    )


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
                return after_plan(state, plan)
            if payload.action is ResumeAction.REJECT:
                return Command(
                    goto="planner",
                    update={"plan_feedback": payload.feedback, "status": RunStatus.PLANNING.value},
                )
            try:
                edited = Plan.model_validate(
                    payload.artifact, context=plan_validation_context(state)
                )
            except ValidationError as exc:
                error = f"edited plan is invalid: {exc}"
                continue
            return after_plan(state, edited)

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
        gates = GateReport.model_validate(state.get("gates") or {})
        blocked = bool(gates.blocking)
        payload = request_input(
            InterruptRequest(
                kind=InterruptKind.APPROVAL,
                artifact="final",
                title=(
                    "Secrets found: reject to have them removed"
                    if blocked
                    else "Approve the final result"
                    + (" (quality gates failed)" if gates.failed else "")
                ),
                # A failed gate can be allowed (it is noted in the PR), except secrets.
                allowed_actions=(
                    [ResumeAction.REJECT]
                    if blocked
                    else [ResumeAction.APPROVE, ResumeAction.REJECT]
                ),
                data={
                    "integration_branch": state.get("integration_branch"),
                    "tasks": state.get("tasks", {}),
                    "integration": state.get("integration"),
                    "gates": state.get("gates"),
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
