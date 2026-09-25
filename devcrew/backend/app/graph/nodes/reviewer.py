from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import Command

from app.graph.context_builder import budget_for, reviewer_context
from app.graph.nodes.task_common import TaskCtx, load_task_ctx, task_query, to_coordinator
from app.graph.runtime import GraphDeps, NodeFn, retrieve
from app.graph.state import ReviewIssue, ReviewResult, TaskStatus
from app.llm.agent import run_agent
from app.llm.models_config import Role
from app.llm.structured import StructuredOutputError, generate_structured
from app.tools.base import ToolError, ToolSpec
from app.tools.catalog import read_rules_tool
from app.tools.git import git_diff_tool
from app.tools.search import search_codebase_tool
from app.tools.workspace import read_file_tool

NODE = "reviewer"


def render_feedback(review: ReviewResult) -> str:
    lines = [f"Reviewer requested changes: {review.summary}".strip()]
    for issue in review.issues:
        where = f"{issue.file}:{issue.line}" if issue.line else issue.file
        rule = f" [{issue.rule_ref}]" if issue.rule_ref else ""
        lines.append(f"- ({issue.severity}){rule} {where}: {issue.message}")
    return "\n".join(lines)


def reviewer_tools(deps: GraphDeps, ctx: TaskCtx) -> list[ToolSpec]:
    """Read-only by construction: no write_file, no run_command."""
    tools = [
        read_file_tool(ctx.workspace),
        git_diff_tool(ctx.repo, ctx.integration_branch, ctx.branch),
        read_rules_tool(deps.rules),
    ]
    if deps.rag is not None:
        tools.append(search_codebase_tool(deps.rag, ctx.run_id))
    return tools


def make_reviewer(deps: GraphDeps) -> NodeFn:
    async def reviewer(state: dict[str, Any]) -> Command[str]:
        ctx = load_task_ctx(deps, state)
        diff = await ctx.repo.diff(ctx.integration_branch, ctx.branch)

        if not diff.strip():
            review = ReviewResult(
                decision="changes_requested",
                summary="The task branch has no changes.",
                issues=[
                    ReviewIssue(
                        file=(ctx.task.target_files or ["."])[0],
                        severity="blocker",
                        message="Nothing was implemented. Write the task's files with write_file.",
                    )
                ],
            )
        else:
            try:
                rules = deps.rules.read(ctx.task.stack)
            except ToolError:
                rules = ""
            system = deps.prompts.get(NODE, ReviewResult)
            context = reviewer_context(
                ctx.task,
                ctx.plan,
                ctx.design,
                rules,
                diff,
                budget_for(deps.llm.spec(Role.REVIEWER).prompt_budget, system),
                related_code=await retrieve(deps, ctx.run_id, task_query(ctx.task)),
            )
            outcome = await run_agent(
                deps.llm,
                Role.REVIEWER,
                [SystemMessage(content=system), HumanMessage(content=context)],
                reviewer_tools(deps, ctx),
                max_steps=deps.settings.max_agent_steps,
                on_tool_event=deps.tool_hook(ctx.run_id, NODE, ctx.task.id),
            )
            if outcome.kind != "final":
                return await to_coordinator(deps, ctx, NODE, f"reviewer failed: {outcome.error}")
            try:
                result = await generate_structured(
                    deps.llm,
                    Role.REVIEWER,
                    outcome.messages[:-1],
                    ReviewResult,
                    context={"rule_ids": deps.rules.rule_ids([ctx.task.stack])},
                    first_response=outcome.final_text,
                )
            except StructuredOutputError as exc:
                return await to_coordinator(deps, ctx, NODE, f"reviewer output invalid: {exc}")
            review = result.value

        ctx.ts.review = review
        if review.decision == "approve":
            ctx.ts.status = TaskStatus.TESTING
            return Command(goto="qa", update=ctx.update())
        ctx.ts.status = TaskStatus.IN_PROGRESS
        ctx.ts.feedback = render_feedback(review)
        return Command(goto="developer", update=ctx.update())

    return reviewer
