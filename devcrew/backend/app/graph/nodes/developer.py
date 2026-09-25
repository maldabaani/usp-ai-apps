from __future__ import annotations

from typing import Any

from langgraph.types import Command

from app.graph.context_builder import budget_for, developer_context
from app.graph.nodes.task_common import TaskCtx, load_task_ctx, to_coordinator
from app.graph.runtime import (
    GraphDeps,
    NodeFn,
    agent_turn,
    pending_question,
    qa_entries,
    questions_asked,
    save_transcript,
)
from app.graph.state import TaskStatus, dump
from app.llm.models_config import Role
from app.tools.base import ToolError, ToolSpec
from app.tools.catalog import read_rules_tool
from app.tools.human import ask_human_tool
from app.tools.sandbox import run_command_tool
from app.tools.workspace import list_dir_tool, read_file_tool, write_file_tool

NODE = "developer"


def developer_tools(deps: GraphDeps, ctx: TaskCtx, asked: int) -> list[ToolSpec]:
    tools = [
        read_file_tool(ctx.workspace),
        write_file_tool(ctx.workspace),
        list_dir_tool(ctx.workspace),
        read_rules_tool(deps.rules),
        ask_human_tool(limit_reached=asked >= deps.settings.max_questions_per_task),
    ]
    if deps.sandbox is not None:
        tools.append(
            run_command_tool(deps.sandbox, ctx.target, ctx.layout, default_cwd=ctx.project.path)
        )
    return tools


def make_developer(deps: GraphDeps) -> NodeFn:
    async def developer(state: dict[str, Any]) -> Command[str]:
        ctx = load_task_ctx(deps, state)
        max_iter = deps.settings.max_dev_iterations
        if ctx.ts.iterations >= max_iter:
            return await to_coordinator(
                deps,
                ctx,
                NODE,
                f"reached MAX_DEV_ITERATIONS ({max_iter}) without passing review and tests",
            )
        await ctx.repo.checkout(ctx.branch)
        qa_log = state.get("qa_log", [])
        asked = questions_asked(qa_log, NODE, ctx.task.id)
        system = deps.prompts.get(NODE)

        def context() -> str:
            try:
                rules = deps.rules.read(ctx.task.stack)
            except ToolError:
                rules = ""
            return developer_context(
                ctx.task,
                ctx.plan,
                ctx.design,
                ctx.ts,
                rules,
                "\n".join(ctx.workspace.list(".", depth=4)),
                budget_for(deps.llm.spec(Role.DEVELOPER).prompt_budget, system),
                qa=qa_entries(qa_log, task_id=ctx.task.id),
            )

        outcome = await agent_turn(
            deps,
            role=Role.DEVELOPER,
            run_id=ctx.run_id,
            node=NODE,
            task_id=ctx.task.id,
            system=system,
            build_context=context,
            tools=developer_tools(deps, ctx, asked),
            saved=state.get("task_scratch", {}).get(NODE),
        )
        if outcome.kind == "ask_human":
            question = await pending_question(deps, ctx.run_id, NODE, ctx.task.id, outcome)
            return Command(
                goto="ask_human",
                update={
                    "task_scratch": {NODE: save_transcript(outcome.messages)},
                    "task_pending_question": dump(question),
                },
            )
        if outcome.kind == "error":
            return await to_coordinator(deps, ctx, NODE, outcome.error)

        ctx.ts.iterations += 1
        await ctx.repo.commit_all(f"{ctx.task.id}: {ctx.task.title} (attempt {ctx.ts.iterations})")
        ctx.ts.status = TaskStatus.IN_REVIEW
        ctx.ts.feedback = None
        return Command(goto="reviewer", update=ctx.update(task_scratch={NODE: None}))

    return developer
