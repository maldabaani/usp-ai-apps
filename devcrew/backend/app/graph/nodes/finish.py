"""Integration, delivery and completion nodes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.graph import END
from langgraph.types import Command

from app.db.models import RunStatus
from app.events.types import EventType
from app.graph.runtime import TEST_COMMANDS, GraphDeps, NodeFn
from app.graph.state import TaskState, TaskStatus, TestResult, dump, get_plan
from app.llm.tokens import tail_text
from app.tools.git import GitRepo


def make_integration(deps: GraphDeps) -> NodeFn:
    async def integration(state: dict[str, Any]) -> Command[str]:
        run_id = state["run_id"]
        root = Path(state["workspace"])
        await GitRepo(root).checkout(state["integration_branch"])
        plan = get_plan(state)
        tasks = {tid: TaskState.model_validate(t) for tid, t in state["tasks"].items()}

        results: dict[str, Any] = {}
        for stack in sorted({t.stack for t in plan.tasks}):
            command = TEST_COMMANDS[stack]
            if deps.runner is None:
                result = TestResult(
                    ran=False,
                    passed=True,
                    command=command,
                    logs_excerpt="Sandbox runner not configured.",
                )
            else:
                out = await deps.runner.run(
                    run_id=run_id, task_id=None, workdir=root, command=command, stack=stack
                )
                result = TestResult(
                    ran=True,
                    passed=out.ok,
                    command=command,
                    logs_excerpt=tail_text(out.output, 800),
                )
            results[stack] = dump(result)

        summary = {
            "merged": sorted(t for t, s in tasks.items() if s.status is TaskStatus.MERGED),
            "failed": sorted(t for t, s in tasks.items() if s.status is TaskStatus.FAILED),
            "blocked": sorted(t for t, s in tasks.items() if s.status is TaskStatus.BLOCKED),
            "tests": results,
        }
        return Command(
            goto="approve_final",
            update={"integration": summary, "status": RunStatus.AWAITING_FINAL_APPROVAL},
        )

    return integration


def make_github_delivery(deps: GraphDeps) -> NodeFn:
    async def github_delivery(state: dict[str, Any]) -> Command[str]:
        # Phase 8 implements push + PR. Nothing is pushed before this node.
        await deps.emit(
            state["run_id"],
            EventType.TOOL_RESULT,
            node="github_delivery",
            tool="github",
            ok=False,
            skipped=True,
            result="GitHub delivery is not implemented yet (Phase 8); nothing was pushed.",
        )
        return Command(goto="done", update={"pr_url": None})

    return github_delivery


def make_done(deps: GraphDeps) -> NodeFn:
    async def done(state: dict[str, Any]) -> Command[str]:
        # Phase 4 deletes the run's Chroma collection here.
        return Command(goto=END, update={"status": RunStatus.COMPLETED})

    return done
