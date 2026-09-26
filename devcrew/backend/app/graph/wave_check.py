"""Tests after every wave (Phase 14): catch breakage from combining parallel tasks early.

Before dispatching the next wave, the scheduler runs the project's test suites on the
integration branch if the last wave merged something and more tasks follow (the last wave is
covered by the integration step). A failure adds a WAVEFIX task that carries the logs and that
every remaining task waits for, at most MAX_WAVE_FIX_TASKS per run.
"""

from __future__ import annotations

from typing import Any

from app.events.types import EventType
from app.graph.nodes.finish import run_project_tests
from app.graph.runtime import GraphDeps
from app.graph.state import Plan, PlanTask, TaskState, TaskStatus, TestResult, dump, get_plan

NODE = "wave_check"


def fix_task(plan: Plan, failures: dict[str, TestResult], wave: int, number: int) -> PlanTask:
    logs = "\n\n".join(
        f"### {stack}: `{r.command}`\n{r.logs_excerpt[-2500:]}" for stack, r in failures.items()
    )
    return PlanTask(
        id=f"WAVEFIX{number}",
        title=f"Fix the tests broken after wave {wave}",
        description=(
            "The project's tests fail on the integration branch after the last wave of tasks "
            "was merged; the tasks passed on their own, so the combination breaks something. "
            "Find the cause and fix it without removing or weakening tests.\n\n" + logs
        ),
        target_files=[],
        depends_on=[],
        stack=next(iter(failures)),  # a stack that is part of the plan
        story_ids=[],
    )


async def check_wave(
    deps: GraphDeps, state: dict[str, Any], ready: list[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Returns (state with a fix task if one was added, update to merge)."""
    wave = int(state.get("wave") or 0)
    if (
        not deps.settings.wave_tests_enabled
        or deps.sandbox is None
        or not ready
        or wave <= int(state.get("wave_checked") or 0)
    ):
        return state, {}
    tasks = state.get("tasks") or {}
    merged_now = [
        tid
        for tid, t in tasks.items()
        if t.get("wave") == wave and t.get("status") == TaskStatus.MERGED.value
    ]
    if not merged_now:
        return state, {"wave_checked": wave}
    results, _ = await run_project_tests(deps, state, node=NODE, measure_coverage=False)
    failures = {
        stack: r
        for stack, raw in results.items()
        if (r := TestResult.model_validate(raw)).ran and not r.passed
    }
    update: dict[str, Any] = {"wave_checked": wave}
    fixes = int(state.get("wave_fixes") or 0)
    if not failures:
        return state, update
    if fixes >= deps.settings.max_wave_fix_tasks:
        await deps.emit(
            state["run_id"],
            EventType.ERROR,
            node=NODE,
            message=(
                f"tests fail after wave {wave} ({', '.join(failures)}); no fix task left "
                f"(MAX_WAVE_FIX_TASKS={deps.settings.max_wave_fix_tasks}), continuing"
            ),
        )
        return state, update
    plan = get_plan(state)
    task = fix_task(plan, failures, wave, fixes + 1)
    pending = {tid for tid, t in tasks.items() if t.get("status") == TaskStatus.PENDING.value}
    # every task that has not started waits for the fix
    new_tasks = [
        t.model_copy(update={"depends_on": [*t.depends_on, task.id]}) if t.id in pending else t
        for t in plan.tasks
    ]
    new_plan = plan.model_copy(update={"tasks": [*new_tasks, task]})
    await deps.emit(
        state["run_id"],
        EventType.TOOL_RESULT,
        node=NODE,
        tool="wave_fix",
        ok=False,
        result=f"tests fail after wave {wave}: added {task.id} before {len(pending)} task(s)",
    )
    task_state = dump(TaskState(id=task.id))
    update |= {"plan": dump(new_plan), "wave_fixes": fixes + 1, "tasks": {task.id: task_state}}
    new_state = {
        **state,
        "plan": dump(new_plan),
        "tasks": {**tasks, task.id: task_state},
        "wave_fixes": fixes + 1,
    }
    return new_state, update
