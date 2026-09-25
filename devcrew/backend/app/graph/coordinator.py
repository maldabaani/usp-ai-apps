"""Coordinator: the non-persona exception handler.

Phase 2 behavior is deterministic: every exception (iteration limit, repeated malformed tool
calls, invalid structured output, merge conflict) is escalated to the human via interrupt.
Phase 5 adds the LLM-driven decisions (replan / split / reassign / escalate) in front of this.
"""

from __future__ import annotations

import uuid
from typing import Any

from langgraph.graph import END
from langgraph.types import Command

from app.db.models import RunStatus
from app.graph.interrupts import InterruptKind, InterruptRequest, ResumeAction, request_input
from app.graph.runtime import GraphDeps, NodeFn
from app.graph.state import QAEntry, TaskState, TaskStatus, dump

ESCALATION_ACTIONS = [ResumeAction.APPROVE, ResumeAction.ANSWER, ResumeAction.REJECT]
FEEDBACK_KEYS = {"planner": "plan_feedback", "architect": "design_feedback"}


def _qa(task_id: str | None, reason: str, answer: str) -> dict[str, Any]:
    return dump(
        QAEntry(
            id=uuid.uuid4().hex[:12],
            task_id=task_id,
            asker="coordinator",
            target="human",
            question=reason,
            answer=answer,
        )
    )


def make_run_coordinator(deps: GraphDeps) -> NodeFn:
    """Handles planner/architect failures."""

    async def coordinator(state: dict[str, Any]) -> Command[str]:
        escalation = state.get("escalation") or {}
        node = escalation.get("node", "planner")
        reason = escalation.get("reason", "unknown error")
        payload = request_input(
            InterruptRequest(
                kind=InterruptKind.ESCALATION,
                title=f"The {node} could not finish",
                allowed_actions=ESCALATION_ACTIONS,
                data={
                    "node": node,
                    "reason": reason,
                    "options": {
                        "approve": "retry as is",
                        "answer": "retry with your guidance",
                        "reject": "abort the run",
                    },
                },
            )
        )
        if payload.action is ResumeAction.REJECT:
            return Command(
                goto=END,
                update={"status": RunStatus.FAILED, "escalation": None, "errors": [reason]},
            )
        update: dict[str, Any] = {"escalation": None}
        if payload.action is ResumeAction.ANSWER:
            update[FEEDBACK_KEYS.get(node, "plan_feedback")] = payload.answer
            update["qa_log"] = [_qa(None, reason, payload.answer or "")]
        status = RunStatus.PLANNING if node == "planner" else RunStatus.DESIGNING
        return Command(goto=node, update={**update, "status": status})

    return coordinator


def make_task_coordinator(deps: GraphDeps) -> NodeFn:
    """Handles a task that hit MAX_DEV_ITERATIONS or failed irrecoverably."""

    async def coordinator(state: dict[str, Any]) -> Command[str]:
        task_id = state["task"]["id"]
        ts = TaskState.model_validate(state["tasks"][task_id])
        reason = state.get("escalation_reason") or "unknown error"
        payload = request_input(
            InterruptRequest(
                kind=InterruptKind.ESCALATION,
                title=f"Task {task_id} needs a decision",
                allowed_actions=ESCALATION_ACTIONS,
                data={
                    "task_id": task_id,
                    "task": state["task"],
                    "reason": reason,
                    "iterations": ts.iterations,
                    "review": ts.review.model_dump(mode="json") if ts.review else None,
                    "test_results": (
                        ts.test_results.model_dump(mode="json") if ts.test_results else None
                    ),
                    "options": {
                        "approve": "give the developer another round of attempts",
                        "answer": "retry with your guidance",
                        "reject": "mark the task failed (dependents are blocked)",
                    },
                },
            )
        )
        if payload.action is ResumeAction.REJECT:
            ts.status = TaskStatus.FAILED
            ts.error = reason
            if deps.sandbox is not None:
                await deps.sandbox.release(state["run_id"], task_id)
            return Command(
                goto=END,
                update={"tasks": {task_id: dump(ts)}, "errors": [f"task {task_id}: {reason}"]},
            )
        update: dict[str, Any] = {"escalation_reason": None, "task_scratch": {"developer": None}}
        ts.iterations = 0
        ts.status = TaskStatus.IN_PROGRESS
        if payload.action is ResumeAction.ANSWER:
            guidance = payload.answer or ""
            ts.feedback = f"{ts.feedback or ''}\n\nHuman guidance: {guidance}".strip()
            update["qa_log"] = [_qa(task_id, reason, guidance)]
        return Command(goto="developer", update={**update, "tasks": {task_id: dump(ts)}})

    return coordinator
