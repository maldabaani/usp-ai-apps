"""Integration, delivery and completion nodes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.graph import END
from langgraph.types import Command

from app.db.models import RunStatus
from app.events.types import EventType
from app.graph.layout import resolve_layout, sandbox_target
from app.graph.runtime import GraphDeps, NodeFn, release_run_resources
from app.graph.state import TaskState, TaskStatus, TestResult, dump, get_design
from app.llm.tokens import tail_text
from app.tools.git import GitRepo, repo_lock

NOT_RUN = "Sandbox disabled: tests were not executed."


def make_integration(deps: GraphDeps) -> NodeFn:
    async def integration(state: dict[str, Any]) -> Command[str]:
        run_id = state["run_id"]
        root = Path(state["workspace"])
        await GitRepo(root).checkout(state["integration_branch"])
        design = get_design(state)
        layout = resolve_layout(design, deps.templates)
        target = sandbox_target(run_id, None, root, design, layout)
        tasks = {tid: TaskState.model_validate(t) for tid, t in state["tasks"].items()}

        results: dict[str, Any] = {}
        for stack, entry in layout.items():
            command = entry.template.test_cmd
            await deps.emit(
                run_id,
                EventType.TOOL_CALL,
                node="integration",
                tool="run_tests",
                args={"command": command, "cwd": entry.path},
            )
            if deps.sandbox is None:
                result = TestResult(ran=False, passed=True, command=command, logs_excerpt=NOT_RUN)
            else:
                out = await deps.sandbox.exec(target, layout, command, cwd=entry.path)
                result = TestResult(
                    ran=True,
                    passed=out.ok,
                    command=command,
                    logs_excerpt=tail_text(out.output, 800),
                )
            await deps.emit(
                run_id,
                EventType.TOOL_RESULT,
                node="integration",
                tool="run_tests",
                ok=result.passed,
                skipped=not result.ran,
                result=result.logs_excerpt[-2000:],
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
            update={"integration": summary, "status": RunStatus.AWAITING_FINAL_APPROVAL.value},
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
        await release_run_resources(deps, state["run_id"])
        main = Path(state["workspace"])
        repo = GitRepo(main)
        async with repo_lock(main):
            for tree in (await repo.worktrees())[1:]:  # the first entry is the main worktree
                await repo.worktree_remove(tree)
        return Command(goto=END, update={"status": RunStatus.COMPLETED.value})

    return done
