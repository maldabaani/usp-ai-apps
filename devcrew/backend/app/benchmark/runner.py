"""Headless benchmark runs: auto-approve, auto-answer, score with hidden acceptance tests.

Each task is a normal DevCrew run driven without a human. Afterwards the integration branch is
exported into a fresh directory next to the run's workspace, the task's hidden tests are copied
in, and the suite is executed in the sandbox (never on the host). GitHub delivery is always off.
"""

from __future__ import annotations

import asyncio
import io
import logging
import shutil
import tarfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from app.benchmark.results import (
    HiddenTestResult,
    TaskResult,
    TokenCounts,
    parse_test_output,
)
from app.benchmark.tasks import BenchmarkTask
from app.db.repository import new_run_id
from app.events.types import EventType
from app.graph.backbone import build_graph
from app.graph.interrupts import InterruptKind, ResumeAction, ResumePayload
from app.graph.layout import resolve_layout, sandbox_target
from app.graph.runner import RunDriver, RunOutcome
from app.graph.runtime import GraphDeps, release_run_resources
from app.graph.state import Design, TaskState, TaskStatus
from app.llm.client import UsageTracker
from app.tools.git import GitRepo

logger = logging.getLogger(__name__)

AUTO_ANSWER = "Use your best judgment and document the assumption."
GIVE_UP = "Benchmark: automatic escalation limit reached."
EVAL_DIR = "hidden-eval"


@dataclass
class AutoPolicy:
    """Answers every interrupt without a human.

    Approvals are approved and questions get AUTO_ANSWER. Escalations are retried with
    AUTO_ANSWER as guidance up to `max_escalations` times per run, then given up (the task is
    marked failed, or a run-level escalation aborts the run), so a stuck run always ends.
    """

    max_escalations: int = 2
    escalations: int = 0
    questions: int = 0
    carried_iterations: int = 0  # task iteration counters reset by an escalation retry
    log: list[str] = field(default_factory=list)

    def payload(self, value: dict[str, Any]) -> ResumePayload:
        allowed = {ResumeAction(a) for a in value.get("allowed_actions", [])}
        kind = value.get("kind")
        if kind == InterruptKind.ESCALATION:
            self.escalations += 1
            data = value.get("data") or {}
            retry = ResumeAction.ANSWER in allowed and self.escalations <= self.max_escalations
            self.log.append(
                f"escalation #{self.escalations} ({'retry' if retry else 'give up'}): "
                f"{str(data.get('reason', ''))[:200]}"
            )
            if retry:
                self.carried_iterations += int(data.get("iterations") or 0)
                return ResumePayload(action=ResumeAction.ANSWER, answer=AUTO_ANSWER)
            return ResumePayload(action=ResumeAction.REJECT, feedback=GIVE_UP)
        if kind == InterruptKind.QUESTION:
            self.questions += 1
            return ResumePayload(action=ResumeAction.ANSWER, answer=AUTO_ANSWER)
        if ResumeAction.APPROVE in allowed:
            return ResumePayload(action=ResumeAction.APPROVE)
        return ResumePayload(action=ResumeAction.ANSWER, answer=AUTO_ANSWER)


def export_branch(archive: bytes, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, filter="data")


class BenchmarkRunner:
    def __init__(
        self,
        deps: GraphDeps,
        *,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
        max_escalations: int = 2,
        task_timeout_s: float | None = None,
        echo: Callable[[str], None] | None = None,
    ) -> None:
        if deps.github is not None:
            raise ValueError("benchmark runs must not deliver to GitHub")
        self.deps = deps
        self.driver = RunDriver(build_graph(deps, checkpointer or InMemorySaver()), deps.events)
        self.max_escalations = max_escalations
        self.task_timeout_s = task_timeout_s
        self.echo = echo or (lambda _msg: None)

    async def run_task(self, task: BenchmarkTask) -> TaskResult:
        run_id = new_run_id()
        policy = AutoPolicy(self.max_escalations)
        self.deps.llm.usage = UsageTracker()  # tasks run one at a time: per-run token counts
        started = time.monotonic()
        status, error = "failed", None
        self.echo(f"[{task.id}] run {run_id} started")
        try:
            async with asyncio.timeout(self.task_timeout_s):
                outcome = await self._drive(run_id, task, policy)
            status, error = outcome.status.value, outcome.error
        except TimeoutError:
            status, error = "timeout", f"no result after {self.task_timeout_s:.0f}s"
            await release_run_resources(self.deps, run_id)
        except Exception as exc:  # keep benchmarking the remaining tasks
            logger.exception("benchmark task %s crashed", task.id)
            error = f"{type(exc).__name__}: {exc}"
            await release_run_resources(self.deps, run_id)
        wall = time.monotonic() - started
        for line in policy.log:
            self.echo(f"[{task.id}] {line}")

        state = await self.driver.state(run_id)
        hidden = await self.evaluate_hidden(task, run_id, state)
        result = self._result(task, run_id, state, policy, status, error, wall, hidden)
        result.coordinator_calls = await self._count_node_starts(run_id, "coordinator")
        self.echo(
            f"[{task.id}] {status}: hidden {hidden.passed}/{hidden.expected}"
            f"{'' if hidden.ran else ' (not run)'}, {wall / 60:.1f} min"
        )
        return result

    async def _drive(self, run_id: str, task: BenchmarkTask, policy: AutoPolicy) -> RunOutcome:
        outcome = await self.driver.start(run_id, task.request, f"benchmark/{task.id}")
        while outcome.kind == "interrupted":
            pending = outcome.interrupts[0]
            self.echo(f"[{task.id}] auto-answering: {pending.value.get('title')}")
            outcome = await self.driver.resume(run_id, policy.payload(pending.value), pending.id)
        return outcome

    async def evaluate_hidden(
        self, task: BenchmarkTask, run_id: str, state: dict[str, Any]
    ) -> HiddenTestResult:
        def skipped(reason: str) -> HiddenTestResult:
            return HiddenTestResult(ran=False, expected=task.expected_tests, detail=reason)

        if self.deps.sandbox is None:
            return skipped("sandbox disabled (SANDBOX_ENABLED=false): hidden tests not executed")
        workspace, branch = state.get("workspace"), state.get("integration_branch")
        if not workspace or not branch or not state.get("design"):
            return skipped("the run produced no integration branch")
        design = Design.model_validate(state["design"])
        layout = resolve_layout(design, self.deps.templates)
        entry = layout.get(task.stack.value)
        if entry is None:
            return skipped(f"the design has no {task.stack} project (stacks: {sorted(layout)})")

        eval_id = f"{run_id}-eval"
        eval_dir = Path(workspace).parent / EVAL_DIR
        archive = await GitRepo(Path(workspace)).run_bytes("archive", "--format=tar", branch)
        await asyncio.to_thread(export_branch, archive, eval_dir)
        project = eval_dir / entry.path
        await asyncio.to_thread(shutil.copytree, task.hidden_dir, project, dirs_exist_ok=True)

        target = sandbox_target(eval_id, None, eval_dir, design, layout)
        try:
            out = await self.deps.sandbox.exec(target, layout, task.test_command, cwd=entry.path)
        finally:
            await self.deps.sandbox.cleanup_run(eval_id)
        counts = parse_test_output(task.stack, out.output)
        return HiddenTestResult.scored(
            counts,
            expected=task.expected_tests,
            command=task.test_command,
            exit_code=out.exit_code,
            output=out.output,
        )

    def _result(
        self,
        task: BenchmarkTask,
        run_id: str,
        state: dict[str, Any],
        policy: AutoPolicy,
        status: str,
        error: str | None,
        wall: float,
        hidden: HiddenTestResult,
    ) -> TaskResult:
        tasks = [TaskState.model_validate(t) for t in (state.get("tasks") or {}).values()]
        qa_log = state.get("qa_log") or []
        integration = state.get("integration") or {}
        tests = integration.get("tests") or {}
        usage = self.deps.llm.usage
        total = usage.total()
        return TaskResult(
            task_id=task.id,
            stack=task.stack.value,
            difficulty=task.difficulty,
            title=task.title,
            run_id=run_id,
            status=status,
            error=error,
            wall_time_s=round(wall, 1),
            hidden=hidden,
            dev_iterations=sum(t.iterations for t in tasks) + policy.carried_iterations,
            escalations=policy.escalations,
            questions_to_human=policy.questions,
            questions_to_agents=sum(1 for q in qa_log if q.get("target") != "human"),
            planned_tasks=len((state.get("plan") or {}).get("tasks") or []),
            merged_tasks=sum(1 for t in tasks if t.status is TaskStatus.MERGED),
            failed_tasks=sum(1 for t in tasks if t.status is TaskStatus.FAILED),
            integration_tests_passed=(
                all(bool(r.get("passed")) for r in tests.values()) if tests else None
            ),
            tokens=TokenCounts(
                input=total.input_tokens,
                output=total.output_tokens,
                calls=total.calls,
                by_role={
                    role.value: {
                        "input": u.input_tokens,
                        "output": u.output_tokens,
                        "calls": u.calls,
                    }
                    for role, u in usage.by_role.items()
                },
            ),
            workspace=state.get("workspace"),
        )

    async def _count_node_starts(self, run_id: str, node: str) -> int:
        count = 0
        async for event in self.deps.events.replay(run_id):
            if event.type is EventType.NODE_STARTED and event.node == node:
                count += 1
        return count
