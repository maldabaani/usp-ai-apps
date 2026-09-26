"""Integration, delivery and completion nodes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.graph import END
from langgraph.types import Command

from app.db.models import RunStatus
from app.events.types import EventType
from app.gates.checks import parse_coverage
from app.github.delivery import DeliveryError
from app.graph.interrupts import InterruptKind, InterruptRequest, ResumeAction, request_input
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
        coverage: dict[str, float | None] = {}
        gates_on = deps.settings.gates_enabled
        for stack, entry in layout.items():
            # With gates on, the coverage variant runs the same tests and reports coverage.
            measure = gates_on and bool(entry.template.coverage_cmd)
            command = (entry.template.coverage_cmd or "") if measure else entry.template.test_cmd
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
                if measure:
                    coverage[stack] = parse_coverage(stack, out.output)
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
            "coverage": coverage,
        }
        return Command(
            goto="gates", update={"integration": summary, "status": RunStatus.CHECKING.value}
        )

    return integration


def make_github_delivery(deps: GraphDeps) -> NodeFn:
    async def github_delivery(state: dict[str, Any]) -> Command[str]:
        run_id = state["run_id"]
        if deps.github is None:
            await deps.emit(
                run_id,
                EventType.TOOL_RESULT,
                node="github_delivery",
                tool="github",
                ok=True,
                skipped=True,
                result="GitHub delivery disabled; nothing was pushed.",
            )
            return Command(goto="done", update={"pr_url": None})

        async def progress(message: str) -> None:
            await deps.emit(
                run_id,
                EventType.TOOL_CALL,
                node="github_delivery",
                tool="github",
                args={"step": message},
            )

        try:
            result = await deps.github.deliver(state, progress=progress)
        except DeliveryError as exc:
            await deps.emit(run_id, EventType.ERROR, node="github_delivery", message=str(exc))
            return Command(goto="delivery_failed", update={"delivery_error": str(exc)})
        await deps.emit(
            run_id,
            EventType.TOOL_RESULT,
            node="github_delivery",
            tool="github",
            ok=True,
            result=f"pull request opened: {result.pr_url}",
            pr_url=result.pr_url,
            repo_created=result.repo_created,
            pushed_main=result.pushed_main,
        )
        return Command(goto="done", update={"pr_url": result.pr_url, "delivery_error": None})

    return github_delivery


def make_delivery_failed(deps: GraphDeps) -> NodeFn:
    """Delivery failed (auth, permissions, network, non-empty repo): retry or finish without PR."""

    async def delivery_failed(state: dict[str, Any]) -> Command[str]:
        error = state.get("delivery_error") or "unknown error"
        payload = request_input(
            InterruptRequest(
                kind=InterruptKind.ESCALATION,
                title="GitHub delivery failed",
                allowed_actions=[ResumeAction.APPROVE, ResumeAction.REJECT],
                data={
                    "node": "github_delivery",
                    "reason": error,
                    "question": f"Delivery to {state.get('repo_target')} failed: {error}",
                    "options": {
                        "approve": "retry delivery (fix the token/repository first)",
                        "reject": "finish the run without a pull request",
                    },
                },
            )
        )
        if payload.action is ResumeAction.APPROVE:
            return Command(goto="github_delivery", update={"status": RunStatus.DELIVERING.value})
        return Command(
            goto="done",
            update={"pr_url": None, "errors": [f"delivery skipped: {error}"]},
        )

    return delivery_failed


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
