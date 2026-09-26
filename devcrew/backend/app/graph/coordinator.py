"""Coordinator: the non-persona exception handler.

It uses the LLM ONLY for exceptions, and only through a validated `CoordinatorDecision`:

- Task hit MAX_DEV_ITERATIONS          -> replan | split | escalate
- Agent error (malformed tool calls,    -> retry (with guidance) | replan | escalate
  invalid structured output)
- Planner/Architect error              -> retry | escalate
- Merge conflict                        -> deterministic: a Developer resolves it in the task's
                                           worktree against the integration branch, then QA
                                           re-runs; after MAX_CONFLICT_ROUNDS -> escalate
- Agent questions (ask_agent)           -> routed to the human when the target role says a
                                           human decision is needed (see tools/agents.py)

The decision and the human interrupt live in separate nodes (`coordinator` / `escalate`):
LangGraph re-runs a node from the top on resume, and the LLM decision must not be re-run.
Automatic decisions are capped (MAX_COORDINATOR_ACTIONS per task / node); past the cap, or if
the LLM output cannot be validated, the Coordinator escalates to the human.
"""

from __future__ import annotations

import uuid
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END
from langgraph.types import Command

from app.db.models import RunStatus
from app.events.types import EventType
from app.graph.context_builder import (
    Section,
    budget_for,
    fit_sections,
    render_contracts,
    render_plan_tasks,
    render_task,
)
from app.graph.interrupts import InterruptKind, InterruptRequest, ResumeAction, request_input
from app.graph.nodes.task_common import TaskCtx, load_task_ctx
from app.graph.replan import apply_replan, apply_split, replan_change, split_change
from app.graph.runtime import GraphDeps, NodeFn, release_run_resources
from app.graph.state import CoordinatorDecision, QAEntry, TaskStatus, dump
from app.llm.models_config import Role
from app.llm.structured import StructuredOutputError, generate_structured
from app.tools.git import repo_lock

ESCALATION_ACTIONS = [ResumeAction.APPROVE, ResumeAction.ANSWER, ResumeAction.REJECT]
FEEDBACK_KEYS = {"planner": "plan_feedback", "architect": "design_feedback"}
TASK_ACTIONS = {
    "iteration_limit": ["replan", "split", "escalate"],
    "agent_error": ["retry", "replan", "escalate"],
}


def _qa(task_id: str | None, question: str, answer: str) -> dict[str, Any]:
    return dump(
        QAEntry(
            id=uuid.uuid4().hex[:12],
            task_id=task_id,
            asker="coordinator",
            target="human",
            question=question,
            answer=answer,
        )
    )


async def decide(
    deps: GraphDeps,
    *,
    run_id: str,
    task_id: str | None,
    context: str,
    allowed: list[str],
    validation: dict[str, Any] | None = None,
) -> CoordinatorDecision | None:
    """Ask the Coordinator LLM for a decision; None if no valid decision could be obtained."""
    system = deps.prompts.get("coordinator", CoordinatorDecision)
    request = f"{context}\n## Allowed actions\n{', '.join(allowed)}"
    try:
        result = await generate_structured(
            deps.llm,
            Role.COORDINATOR,
            [SystemMessage(content=system), HumanMessage(content=request)],
            CoordinatorDecision,
            context={"allowed_actions": allowed, **(validation or {})},
        )
    except StructuredOutputError as exc:
        await deps.emit(
            run_id,
            EventType.ERROR,
            node="coordinator",
            task_id=task_id,
            message=f"coordinator decision invalid: {exc}",
        )
        return None
    decision = result.value
    await deps.emit(
        run_id,
        EventType.TOOL_RESULT,
        node="coordinator",
        task_id=task_id,
        tool="decision",
        ok=True,
        action=decision.action,
        reason=decision.reason,
        result=decision.reason,
    )
    return decision


# ============================================================================================
# Task level (inside the task subgraph)
# ============================================================================================
def _task_context(ctx: TaskCtx, reason: str, kind: str, budget: int) -> str:
    ts = ctx.ts
    evidence = []
    if ts.review and ts.review.issues:
        evidence.append(
            "Last review issues:\n"
            + "\n".join(
                f"- ({i.severity}) {i.file}:{i.line or ''} {i.message}" for i in ts.review.issues
            )
        )
    if ts.test_results and not ts.test_results.passed:
        evidence.append(f"Last test run (failed):\n{ts.test_results.logs_excerpt}")
    if ts.feedback:
        evidence.append(f"Last feedback given to the developer:\n{ts.feedback}")
    return fit_sections(
        [
            Section(
                "Problem",
                f"kind: {kind}\n{reason}\niterations used: {ts.iterations}",
                priority=0,
                required=True,
            ),
            Section("Task", render_task(ctx.task), priority=0, required=True),
            Section("Evidence", "\n\n".join(evidence) or "(none)", priority=1),
            Section("Design contracts", render_contracts(ctx.design, ctx.task.target_files), 3),
            Section("All tasks (ids are taken)", render_plan_tasks(ctx.plan), priority=2),
        ],
        budget,
    )


def make_task_coordinator(deps: GraphDeps) -> NodeFn:
    settings = deps.settings

    async def coordinator(state: dict[str, Any]) -> Command[str]:
        ctx = load_task_ctx(deps, state)
        ts = ctx.ts
        kind = state.get("escalation_kind") or "agent_error"
        reason = state.get("escalation_reason") or "unknown error"
        failed_node = state.get("escalation_node") or "developer"

        def escalate(question: str) -> Command[str]:
            ts.status = TaskStatus.NEEDS_HUMAN
            return Command(goto="escalate", update=ctx.update(escalation_question=question))

        # ---- merge conflicts: deterministic, a Developer resolves them in the worktree
        if kind == "merge_conflict":
            if ts.conflict_rounds >= settings.max_conflict_rounds:
                return escalate(
                    f"Task {ctx.task.id} still conflicts with the integration branch after "
                    f"{ts.conflict_rounds} resolution rounds ({reason}). Retry, give guidance, "
                    "or reject the task?"
                )
            ts.conflict_rounds += 1
            async with repo_lock(ctx.main_root):
                conflicts = await ctx.repo.merge_in(ctx.integration_branch)
            if not conflicts:  # the integration branch moved on and now merges cleanly
                await ctx.repo.commit_all(f"{ctx.task.id}: merge {ctx.integration_branch}")
                ts.status = TaskStatus.TESTING
                await deps.emit(
                    ctx.run_id,
                    EventType.TOOL_RESULT,
                    node="coordinator",
                    task_id=ctx.task.id,
                    tool="decision",
                    ok=True,
                    action="remerge",
                    result="merged cleanly; re-running QA",
                )
                return Command(goto="qa", update=ctx.update(escalation_reason=None))
            ts.conflict_files = conflicts
            ts.iterations = 0  # conflict resolution gets a fresh iteration budget
            ts.status = TaskStatus.IN_PROGRESS
            ts.feedback = (
                f"Merging into {ctx.integration_branch} conflicted with work merged by other "
                f"tasks. The integration branch has been merged into your branch; resolve the "
                f"conflicts in: {', '.join(conflicts)}. Keep both sides' intent (read each file, "
                "remove every <<<<<<< ======= >>>>>>> marker, write the complete file). Your "
                "change is then reviewed and tested again."
            )
            await deps.emit(
                ctx.run_id,
                EventType.TOOL_RESULT,
                node="coordinator",
                task_id=ctx.task.id,
                tool="decision",
                ok=True,
                action="resolve_conflict",
                result=", ".join(conflicts),
            )
            return Command(
                goto="developer",
                update=ctx.update(escalation_reason=None, task_scratch={"developer": None}),
            )

        # ---- LLM decision for iteration limits and agent errors
        if ts.coordinator_actions >= settings.max_coordinator_actions:
            return escalate(
                f"Task {ctx.task.id} failed again after {ts.coordinator_actions} automatic "
                f"recovery attempts: {reason}"
            )
        allowed = TASK_ACTIONS.get(kind, TASK_ACTIONS["agent_error"])
        existing = {t.id for t in ctx.plan.tasks} | set(state.get("tasks", {}))
        decision = await decide(
            deps,
            run_id=ctx.run_id,
            task_id=ctx.task.id,
            context=_task_context(
                ctx,
                reason,
                kind,
                budget_for(deps.llm.spec(Role.COORDINATOR).prompt_budget, ""),
            ),
            allowed=allowed,
            validation={"existing_ids": existing, "task_depends_on": ctx.task.depends_on},
        )
        if decision is None:
            return escalate(f"Task {ctx.task.id} needs a decision: {reason}")
        ts.coordinator_actions += 1

        if decision.action == "retry":
            ts.feedback = f"{ts.feedback or ''}\n\nCoordinator guidance: {decision.guidance}"
            ts.feedback = ts.feedback.strip()
            ts.status = TaskStatus.IN_PROGRESS
            return Command(
                goto=failed_node if failed_node in ("developer", "reviewer", "qa") else "developer",
                update=ctx.update(escalation_reason=None, task_scratch={"developer": None}),
            )
        if decision.action in ("replan", "split"):
            try:
                if decision.action == "replan":
                    assert decision.revised_task is not None
                    apply_replan(ctx.plan, ctx.task.id, decision.revised_task)
                    change = replan_change(ctx.task.id, decision.revised_task, decision.guidance)
                    ts.status = TaskStatus.PENDING
                else:
                    apply_split(ctx.plan, ctx.task.id, decision.subtasks)
                    change = split_change(ctx.task.id, decision.subtasks)
                    ts.status = TaskStatus.SPLIT
            except (ValueError, KeyError) as exc:
                return escalate(
                    f"The Coordinator's {decision.action} was invalid ({exc}). "
                    f"Original problem: {reason}"
                )
            if deps.sandbox is not None:
                await deps.sandbox.release(ctx.run_id, ctx.task.id)
            if decision.action == "split":
                async with repo_lock(ctx.main_root):
                    await ctx.main_repo.worktree_remove(ctx.worktree)
                ts.worktree = None
            # The scheduler applies the change and (re)dispatches the affected tasks.
            return Command(goto=END, update=ctx.update(plan_changes=[change]))
        return escalate(decision.question_for_human)

    return coordinator


def make_task_escalate(deps: GraphDeps) -> NodeFn:
    async def escalate(state: dict[str, Any]) -> Command[str]:
        ctx = load_task_ctx(deps, state)
        ts = ctx.ts
        kind = state.get("escalation_kind") or "agent_error"
        reason = state.get("escalation_reason") or "unknown error"
        question = state.get("escalation_question") or reason
        payload = request_input(
            InterruptRequest(
                kind=InterruptKind.ESCALATION,
                title=f"Task {ctx.task.id} needs a decision",
                allowed_actions=ESCALATION_ACTIONS,
                data={
                    "task_id": ctx.task.id,
                    "task": state["task"],
                    "kind": kind,
                    "reason": reason,
                    "question": question,
                    "iterations": ts.iterations,
                    "review": ts.review.model_dump(mode="json") if ts.review else None,
                    "test_results": (
                        ts.test_results.model_dump(mode="json") if ts.test_results else None
                    ),
                    "options": {
                        "approve": "try again with fresh attempts",
                        "answer": "try again with your guidance",
                        "reject": "mark the task failed (dependents are blocked)",
                    },
                },
            )
        )
        if payload.action is ResumeAction.REJECT:
            ts.status = TaskStatus.FAILED
            ts.error = reason
            if deps.sandbox is not None:
                await deps.sandbox.release(ctx.run_id, ctx.task.id)
            return Command(
                goto=END,
                update=ctx.update(
                    errors=[f"task {ctx.task.id}: {reason}"], escalation_question=None
                ),
            )
        update: dict[str, Any] = {"escalation_question": None, "task_scratch": {"developer": None}}
        ts.iterations = 0
        ts.conflict_rounds = 0
        ts.status = TaskStatus.IN_PROGRESS
        if payload.action is ResumeAction.ANSWER:
            guidance = payload.answer or ""
            ts.feedback = f"{ts.feedback or ''}\n\nHuman guidance: {guidance}".strip()
            update["qa_log"] = [_qa(ctx.task.id, question, guidance)]
        if kind == "merge_conflict":
            return Command(goto="coordinator", update=ctx.update(**update))
        node = state.get("escalation_node") or "developer"
        target = node if node in ("reviewer", "qa") and kind == "agent_error" else "developer"
        return Command(goto=target, update=ctx.update(escalation_reason=None, **update))

    return escalate


# ============================================================================================
# Run level (planner / architect failures)
# ============================================================================================
def make_run_coordinator(deps: GraphDeps) -> NodeFn:
    async def coordinator(state: dict[str, Any]) -> Command[str]:
        escalation = state.get("escalation") or {}
        node = escalation.get("node", "planner")
        reason = escalation.get("reason", "unknown error")
        retries = (state.get("coordinator_retries") or {}).get(node, 0)
        if retries < deps.settings.max_coordinator_actions:
            decision = await decide(
                deps,
                run_id=state["run_id"],
                task_id=None,
                context=fit_sections(
                    [
                        Section("Problem", f"The {node} failed: {reason}", 0, required=True),
                        Section("Feature request", state.get("request", ""), 1),
                    ],
                    budget_for(deps.llm.spec(Role.COORDINATOR).prompt_budget, ""),
                ),
                allowed=["retry", "escalate"],
            )
            if decision is not None and decision.action == "retry":
                status = (
                    RunStatus.PLANNING.value if node == "planner" else RunStatus.DESIGNING.value
                )
                return Command(
                    goto=node,
                    update={
                        "escalation": None,
                        "coordinator_retries": {node: retries + 1},
                        FEEDBACK_KEYS.get(node, "plan_feedback"): decision.guidance,
                        "status": status,
                    },
                )
            if decision is not None:
                escalation = {**escalation, "question": decision.question_for_human}
        return Command(goto="escalate", update={"escalation": escalation})

    return coordinator


def make_run_escalate(deps: GraphDeps) -> NodeFn:
    async def escalate(state: dict[str, Any]) -> Command[str]:
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
                    "question": escalation.get("question") or reason,
                    "options": {
                        "approve": "retry as is",
                        "answer": "retry with your guidance",
                        "reject": "abort the run",
                    },
                },
            )
        )
        if payload.action is ResumeAction.REJECT:
            await release_run_resources(deps, state["run_id"])
            return Command(
                goto=END,
                update={"status": RunStatus.FAILED.value, "escalation": None, "errors": [reason]},
            )
        update: dict[str, Any] = {"escalation": None, "coordinator_retries": {node: 0}}
        if payload.action is ResumeAction.ANSWER:
            if node in FEEDBACK_KEYS:
                update[FEEDBACK_KEYS[node]] = payload.answer
            update["qa_log"] = [_qa(None, reason, payload.answer or "")]
        status = {
            "prepare_repo": RunStatus.PREPARING.value,
            "planner": RunStatus.PLANNING.value,
        }.get(node, RunStatus.DESIGNING.value)
        return Command(goto=node, update={**update, "status": status})

    return escalate
