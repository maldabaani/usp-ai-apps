from __future__ import annotations

from typing import Any

from langgraph.types import Command

from devcrew.events.types import EventType
from devcrew.graph.nodes.task_common import load_task_ctx
from devcrew.graph.runtime import GraphDeps, NodeFn
from devcrew.graph.state import TaskStatus
from devcrew.llm.tokens import tail_text

NODE = "build_check"
LOG_TAIL_TOKENS = 1500


def make_build_check(deps: GraphDeps) -> NodeFn:
    """Deterministic compile/build check between Developer and Reviewer.

    A build failure (e.g. a missing import, a misplaced annotation) is unambiguous and doesn't
    need the Reviewer's LLM judgment to diagnose -- routing it straight back to the Developer
    with the raw compiler output skips a Reviewer LLM call *and* the QA LLM calls (writing tests,
    then summarizing the failure) that would otherwise be spent discovering the same thing.
    """

    async def build_check(state: dict[str, Any]) -> Command[str]:
        if await deps.steering.pause_requested(state["run_id"]):  # safe point, like reviewer/qa
            return Command(goto="hold", update={"hold_next": "build_check"})
        ctx = load_task_ctx(deps, state)
        command = ctx.project.template.build_cmd
        if deps.sandbox is None or not command.strip():
            return Command(goto="reviewer", update=ctx.update())

        await deps.emit(
            ctx.run_id,
            EventType.TOOL_CALL,
            node=NODE,
            task_id=ctx.task.id,
            tool="build_check",
            args={"command": command},
        )
        result = await deps.sandbox.exec(ctx.target, ctx.layout, command, cwd=ctx.project.path)
        logs = tail_text(result.output, LOG_TAIL_TOKENS)
        await deps.emit(
            ctx.run_id,
            EventType.TOOL_RESULT,
            node=NODE,
            task_id=ctx.task.id,
            tool="build_check",
            ok=result.ok,
            exit_code=result.exit_code,
            result=logs[-2000:],
        )
        if result.ok:
            return Command(goto="reviewer", update=ctx.update())

        ctx.ts.status = TaskStatus.IN_PROGRESS
        reason = "timed out" if result.timed_out else f"exit code {result.exit_code}"
        ctx.ts.feedback = (
            f"The project does not build ({reason}). Fix this before anything else "
            f"(no review or tests can run until it builds):\n{logs}"
        )
        return Command(
            goto="developer", update=ctx.update(task_scratch={"developer": None})
        )

    return build_check
