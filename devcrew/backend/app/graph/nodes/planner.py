from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.types import Command

from app.db.models import RunStatus
from app.events.types import EventType
from app.graph.context_builder import budget_for, planner_context
from app.graph.requirements import effective_request, search_requirements_tool, with_digest
from app.graph.runtime import (
    GraphDeps,
    NodeFn,
    agent_turn,
    pending_question,
    qa_entries,
    questions_asked,
    save_transcript,
)
from app.graph.state import Plan, PlanTask, dump
from app.graph.steering import notes_text, with_notes
from app.llm.models_config import Role
from app.llm.structured import StructuredOutputError, generate_structured
from app.tools.base import ToolSpec
from app.tools.human import ask_human_tool
from app.tools.search import search_codebase_tool
from app.tools.workspace import Workspace, is_test_path, read_file_tool

NODE = "planner"


def repo_tools(deps: GraphDeps, state: dict[str, Any]) -> list[ToolSpec]:
    """Existing repositories: let the agent read and search the checked-out code."""
    if not state.get("repo_info") or not state.get("workspace"):
        return []
    tools = [read_file_tool(Workspace(Path(state["workspace"])))]
    if deps.rag is not None:
        tools.append(search_codebase_tool(deps.rag, state["run_id"]))
    return tools


def plan_validation_context(state: dict[str, Any]) -> dict[str, Any]:
    projects = (state.get("repo_info") or {}).get("projects") or []
    return {"allowed_stacks": {p["stack"] for p in projects}} if projects else {}


def _tests_only(task: PlanTask) -> bool:
    return bool(task.target_files) and all(
        is_test_path(path, [task.stack]) for path in task.target_files
    )


def fold_test_tasks(plan: Plan) -> Plan:
    """Merge "write tests for X" tasks into the task they test.

    QA writes the tests of every task, and may not write another task's files. A separate
    tests-only task therefore leaves QA of the tested task without its test file, and a whole
    task (developer, reviewer, QA) for work QA already does (Mac run: T3/T4 were tests-only).
    The test files move to the most downstream dependency; dependents are rewired to it.
    """
    tasks = {t.id: t.model_copy(deep=True) for t in plan.tasks}
    ancestors: dict[str, set[str]] = {}
    for layer in plan.layers():
        for tid in layer:
            deps = tasks[tid].depends_on
            ancestors[tid] = set(deps).union(*(ancestors[d] for d in deps))
    folded: list[str] = []
    for layer in plan.layers():
        for tid in layer:
            task = tasks.get(tid)
            if task is None or not task.depends_on or not _tests_only(task):
                continue
            deps = [d for d in task.depends_on if d in tasks]
            candidates = [d for d in deps if not any(d in ancestors[o] for o in deps if o != d)]
            same_stack = [d for d in candidates if tasks[d].stack == task.stack]
            if not same_stack:
                continue
            host = tasks[same_stack[-1]]
            host.target_files += [f for f in task.target_files if f not in host.target_files]
            host.story_ids += [s for s in task.story_ids if s not in host.story_ids]
            host.description += f"\n\nTests (QA): {task.title}. {task.description}"
            del tasks[tid]
            for other in tasks.values():
                if tid in other.depends_on:
                    rewired = [host.id if d == tid else d for d in other.depends_on]
                    other.depends_on = [
                        d for i, d in enumerate(rewired) if d != other.id and d not in rewired[:i]
                    ]
            folded.append(f"{tid} into {host.id}")
    if not folded:
        return plan
    summary = (
        f"{plan.summary}\n\nTests-only tasks merged into the task they test (QA writes "
        f"every task's tests): {', '.join(folded)}."
    )
    return Plan.model_validate(
        {**dump(plan), "summary": summary, "tasks": [dump(t) for t in tasks.values()]}
    )


SMALL_PLAN_FILES = 8
MIN_CHAIN_TO_MERGE = 3  # model -> endpoint is a fair split; longer chains are overhead


def merge_serial_plan(plan: Plan) -> Plan:
    """A small plan whose tasks can only run one after another becomes one task.

    Every task costs a full developer/review/QA cycle; a chain gains no parallelism from the
    split. A real run planned one endpoint as model -> service -> controller -> endpoint: four
    serial cycles (and four chances to fail review) for about 40 lines of code.
    """
    layers = plan.layers()
    files = list(dict.fromkeys(f for t in plan.tasks for f in t.target_files))
    stacks = {t.stack for t in plan.tasks}
    if len(plan.tasks) < MIN_CHAIN_TO_MERGE or any(len(layer) > 1 for layer in layers):
        return plan
    if len(files) > SMALL_PLAN_FILES or len(stacks) > 1:
        return plan
    ordered = [plan.task(layer[0]) for layer in layers]
    steps = "\n".join(f"{i}. {t.title}: {t.description}" for i, t in enumerate(ordered, 1))
    merged = PlanTask(
        id=ordered[0].id,
        title=f"{ordered[-1].title} (complete feature)",
        description=f"Build the whole feature in one task, in this order:\n{steps}",
        target_files=files,
        depends_on=[],
        stack=ordered[0].stack,
        story_ids=list(dict.fromkeys(s for t in ordered for s in t.story_ids)),
    )
    summary = (
        f"{plan.summary}\n\nThe {len(ordered)} planned tasks could only run one after another, "
        f"so they were merged into one task ({', '.join(t.id for t in ordered)})."
    )
    return Plan.model_validate({**dump(plan), "summary": summary, "tasks": [dump(merged)]})


def make_planner(deps: GraphDeps) -> NodeFn:
    async def planner(state: dict[str, Any]) -> Command[str]:
        run_id = state["run_id"]
        qa_log = state.get("qa_log", [])
        limit_reached = questions_asked(qa_log, NODE, None) >= deps.settings.max_questions_per_task
        system = deps.prompts.get(NODE, Plan)
        previous = Plan.model_validate(state["plan"]) if state.get("plan") else None

        def context() -> str:
            return planner_context(
                effective_request(state),
                budget_for(deps.llm.spec(Role.PLANNER).prompt_budget, system),
                feedback=state.get("plan_feedback"),
                previous_plan=previous,
                qa=qa_entries(qa_log, asker=NODE),
                repo=state.get("repo_info"),
                mode=state.get("mode"),
                notes=notes_text(state.get("human_notes") or []),
            )

        outcome = await agent_turn(
            deps,
            role=Role.PLANNER,
            run_id=run_id,
            node=NODE,
            task_id=None,
            system=system,
            build_context=context,
            tools=[
                ask_human_tool(limit_reached=limit_reached),
                *repo_tools(deps, state),
                *(
                    [search_requirements_tool(state["request"])]
                    if state.get("request_digest")
                    else []
                ),
            ],
            saved=state.get("scratch", {}).get(NODE),
        )
        if outcome.kind == "ask_human":
            question = await pending_question(deps, run_id, NODE, None, outcome)
            return Command(
                goto="ask_human",
                update={
                    "scratch": {NODE: save_transcript(outcome.messages)},
                    "pending_question": dump(question),
                },
            )
        if outcome.kind == "error":
            return await escalate(deps, run_id, NODE, outcome.error)
        try:
            result = await generate_structured(
                deps.llm,
                Role.PLANNER,
                outcome.messages[:-1],
                Plan,
                context=plan_validation_context(state),
                first_response=outcome.final_text,
            )
        except StructuredOutputError as exc:
            return await escalate(deps, run_id, NODE, str(exc))
        return Command(
            goto="approve_plan",
            update={
                "plan": dump(merge_serial_plan(fold_test_tasks(result.value))),
                "plan_feedback": None,
                "scratch": {NODE: None},
                "status": RunStatus.AWAITING_PLAN_APPROVAL.value,
            },
        )

    return with_digest(deps, with_notes(deps, "Planner", planner))


async def escalate(deps: GraphDeps, run_id: str, node: str, reason: str) -> Command[str]:
    """Record the failure (error event + state) and hand over to the Coordinator."""
    await deps.emit(run_id, EventType.ERROR, node=node, message=reason)
    return Command(
        goto="coordinator",
        update={
            "escalation": {"node": node, "reason": reason},
            "scratch": {node: None},
            "errors": [f"{node}: {reason}"],
        },
    )
