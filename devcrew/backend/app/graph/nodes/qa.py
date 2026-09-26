from __future__ import annotations

from functools import partial
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import Command

from app.events.types import EventType
from app.gates.checks import secrets_gate
from app.gates.runner import scan_secrets
from app.graph.context_builder import budget_for, qa_context, qa_report_context
from app.graph.nodes.task_common import (
    TEST_GLOBS,
    TaskCtx,
    load_task_ctx,
    task_query,
    to_coordinator,
)
from app.graph.runtime import GraphDeps, NodeFn, retrieve
from app.graph.state import QAReport, TaskStatus, TestResult
from app.llm.agent import run_agent
from app.llm.models_config import Role
from app.llm.structured import StructuredOutputError, generate_structured
from app.llm.tokens import tail_text
from app.tools.base import ToolError, ToolSpec
from app.tools.sandbox import run_command_tool
from app.tools.search import search_codebase_tool
from app.tools.workspace import is_test_path, read_file_tool, write_file_tool

NODE = "qa"
LOG_TAIL_TOKENS = 1500
NOT_RUN_NOTE = "Sandbox runner not configured: tests were written but not executed."


async def run_tests(deps: GraphDeps, ctx: TaskCtx, command: str) -> TestResult:
    """Run the stack's test command in the sandbox and let QA summarize failures."""
    await deps.emit(
        ctx.run_id,
        EventType.TOOL_CALL,
        node=NODE,
        task_id=ctx.task.id,
        tool="run_tests",
        args={"command": command},
    )
    if deps.sandbox is None:
        await deps.emit(
            ctx.run_id,
            EventType.TOOL_RESULT,
            node=NODE,
            task_id=ctx.task.id,
            tool="run_tests",
            ok=False,
            skipped=True,
            result=NOT_RUN_NOTE,
        )
        return TestResult(ran=False, passed=True, logs_excerpt=NOT_RUN_NOTE, command=command)

    result = await deps.sandbox.exec(ctx.target, ctx.layout, command, cwd=ctx.project.path)
    logs = tail_text(result.output, LOG_TAIL_TOKENS)
    await deps.emit(
        ctx.run_id,
        EventType.TOOL_RESULT,
        node=NODE,
        task_id=ctx.task.id,
        tool="run_tests",
        ok=result.ok,
        exit_code=result.exit_code,
        result=logs[-2000:],
    )
    if result.ok:
        return TestResult(ran=True, passed=True, logs_excerpt=logs[-2000:], command=command)

    system = deps.prompts.get("qa_report", QAReport)
    context = qa_report_context(
        ctx.task, command, logs, budget_for(deps.llm.spec(Role.QA).prompt_budget, system)
    )
    try:
        report = (
            await generate_structured(
                deps.llm,
                Role.QA,
                [SystemMessage(content=system), HumanMessage(content=context)],
                QAReport,
            )
        ).value
    except StructuredOutputError:
        report = QAReport(failed=[], summary="See the test output below.")
    reason = "timed out" if result.timed_out else f"exit code {result.exit_code}"
    return TestResult(
        ran=True,
        passed=False,
        failed=report.failed,
        logs_excerpt=f"{report.summary}\n\n({reason})\n{logs}",
        command=command,
    )


async def secret_findings(deps: GraphDeps, ctx: TaskCtx, changed: list[str]) -> list[str]:
    """gitleaks on the task worktree; findings in files this task changed (redacted)."""
    if not deps.settings.gates_enabled or deps.sandbox is None:
        return []
    scan = await scan_secrets(deps.sandbox, ctx.target, changed)
    if scan.errors:
        await deps.emit(
            ctx.run_id,
            EventType.TOOL_RESULT,
            node=NODE,
            task_id=ctx.task.id,
            tool="secret_scan",
            ok=True,
            skipped=True,
            result="; ".join(scan.errors),
        )
        return []
    gate = secrets_gate(scan.findings, changed)
    await deps.emit(
        ctx.run_id,
        EventType.TOOL_RESULT,
        node=NODE,
        task_id=ctx.task.id,
        tool="secret_scan",
        ok=gate.status == "passed",
        result=gate.summary,
    )
    return gate.details if gate.status == "failed" else []


def qa_tools(deps: GraphDeps, ctx: TaskCtx) -> list[ToolSpec]:
    tools = [
        read_file_tool(ctx.workspace),
        write_file_tool(
            ctx.workspace,
            partial(lambda stack, p: is_test_path(p, [stack]), ctx.task.stack),
            note=f"QA may only write {ctx.task.stack} test files.",
        ),
    ]
    if deps.sandbox is not None:
        tools.append(
            run_command_tool(deps.sandbox, ctx.target, ctx.layout, default_cwd=ctx.project.path)
        )
    if deps.rag is not None:
        tools.append(search_codebase_tool(deps.rag, ctx.run_id))
    return tools


def make_qa(deps: GraphDeps) -> NodeFn:
    async def qa(state: dict[str, Any]) -> Command[str]:
        ctx = load_task_ctx(deps, state)
        command = ctx.project.template.test_cmd
        changed = await ctx.repo.changed_files(ctx.integration_branch, ctx.branch)
        try:
            rules = deps.rules.read(ctx.task.stack)
        except ToolError:
            rules = ""
        system = deps.prompts.get(NODE)
        context = qa_context(
            ctx.task,
            ctx.plan,
            ctx.design,
            changed,
            command,
            rules,
            budget_for(deps.llm.spec(Role.QA).prompt_budget, system),
            existing_tests=await retrieve(
                deps,
                ctx.run_id,
                f"tests for {task_query(ctx.task)}",
                path_filter=TEST_GLOBS.get(ctx.task.stack),
            ),
        )
        outcome = await run_agent(
            deps.llm,
            Role.QA,
            [SystemMessage(content=system), HumanMessage(content=context)],
            qa_tools(deps, ctx),
            max_steps=deps.settings.max_agent_steps,
            on_tool_event=deps.tool_hook(ctx.run_id, NODE, ctx.task.id),
        )
        if outcome.kind != "final":
            return await to_coordinator(deps, ctx, NODE, f"QA failed: {outcome.error}")
        await ctx.repo.commit_all(f"{ctx.task.id}: tests")

        ctx.ts.test_results = await run_tests(deps, ctx, command)
        if ctx.ts.test_results.passed:
            leaks = await secret_findings(deps, ctx, changed)
            if not leaks:
                return Command(goto="merge", update=ctx.update())
            # Secrets block the merge like a failing test (and can never be allowed through).
            ctx.ts.status = TaskStatus.IN_PROGRESS
            ctx.ts.feedback = (
                "The secret scan found credentials in files you changed. Remove them and read "
                "them from environment variables or configuration instead:\n"
                + "\n".join(f"- {d}" for d in leaks)
            )
            return Command(goto="developer", update=ctx.update())
        ctx.ts.status = TaskStatus.IN_PROGRESS
        failed = ", ".join(ctx.ts.test_results.failed) or "see log"
        ctx.ts.feedback = f"Tests failed ({failed}).\n{ctx.ts.test_results.logs_excerpt}"
        return Command(goto="developer", update=ctx.update())

    return qa
