from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import Command

from devcrew.graph.context_builder import budget_for, other_tasks_files, reviewer_context
from devcrew.graph.nodes.task_common import TaskCtx, load_task_ctx, task_query, to_coordinator
from devcrew.graph.runtime import GraphDeps, NodeFn, retrieve
from devcrew.graph.state import Plan, PlanTask, ReviewIssue, ReviewResult, TaskStatus
from devcrew.graph.steering import task_notes
from devcrew.llm.agent import run_agent
from devcrew.llm.models_config import Role
from devcrew.llm.structured import StructuredOutputError, generate_structured
from devcrew.tools.base import ToolError, ToolSpec
from devcrew.tools.git import git_diff_tool
from devcrew.tools.search import search_codebase_tool
from devcrew.tools.workspace import read_file_tool

NODE = "reviewer"


def merge_duplicate_issues(review: ReviewResult) -> ReviewResult:
    """One issue per (file, severity, rule, message): a real review repeated the same complaint
    for four lines, and eight near-identical rows buried the one thing to fix."""
    merged: dict[tuple[str, str, str | None, str], ReviewIssue] = {}
    extra_lines: dict[tuple[str, str, str | None, str], list[int]] = {}
    for issue in review.issues:
        key = (issue.file, issue.severity, issue.rule_ref, issue.message.strip())
        if key not in merged:
            merged[key] = issue
        elif issue.line is not None:
            extra_lines.setdefault(key, []).append(issue.line)
    issues = []
    for merged_key, issue in merged.items():
        more = sorted(set(extra_lines.get(merged_key, [])) - {issue.line})
        if more:
            also = ", ".join(str(n) for n in more)
            issue = issue.model_copy(update={"message": f"{issue.message} (also lines {also})"})
        issues.append(issue)
    return review.model_copy(update={"issues": issues})


def render_feedback(review: ReviewResult, rule_texts: Mapping[str, str] | None = None) -> str:
    """The developer sees what each cited rule actually says, so a misread rule is visible."""
    rule_texts = rule_texts or {}
    lines = [f"Reviewer requested changes: {review.summary}".strip()]
    for issue in review.issues:
        where = f"{issue.file}:{issue.line}" if issue.line else issue.file
        rule = f" [{issue.rule_ref}]" if issue.rule_ref else ""
        lines.append(f"- ({issue.severity}){rule} {where}: {issue.message}")
    cited = sorted({i.rule_ref for i in review.issues if i.rule_ref and i.rule_ref in rule_texts})
    if cited:
        lines.append(
            "\nThe cited rules say (the rule text wins if a review message contradicts it):"
        )
        lines += [f"- {rid}: {rule_texts[rid]}" for rid in cited]
    return "\n".join(lines)


def changed_files(diff: str) -> set[str]:
    return {line[6:].strip() for line in diff.splitlines() if line.startswith("+++ b/")}


def drop_out_of_scope(review: ReviewResult, task: PlanTask, plan: Plan, diff: str) -> ReviewResult:
    """Blocking issues about another task's files become `info`: small models ask one task for the
    whole story (a real run asked the model task for the router, the service and every test).
    An issue is out of scope when its file or message names a file that another task of the plan
    delivers, and that this task neither targets nor changed."""
    changed = changed_files(diff)
    others = {f: tid for f, tid in other_tasks_files(plan, task).items() if f not in changed}
    if not others:
        return review
    issues: list[ReviewIssue] = []
    for issue in review.issues:
        text = f"{issue.file} {issue.message}"
        owner = next((tid for f, tid in others.items() if f in text), None)
        if owner is not None and issue.severity in ("blocker", "major"):
            issue = issue.model_copy(
                update={"severity": "info", "message": f"[delivered by {owner}] {issue.message}"}
            )
        issues.append(issue)
    blocking = any(i.severity in ("blocker", "major") for i in issues)
    decision = review.decision if blocking else "approve"
    return review.model_copy(update={"issues": issues, "decision": decision})


def reviewer_tools(deps: GraphDeps, ctx: TaskCtx) -> list[ToolSpec]:
    """Read-only by construction: no write_file, no run_command.

    No read_rules tool: the task's own stack rules are already unconditionally folded into the
    system prompt (for Claude Cloud cache-ability -- see client.py's _with_cache_control()), so
    the tool could only ever return text the model already has. Observed live: the Reviewer
    called it anyway and got back the identical text already in its own system prompt.
    """
    tools = [
        read_file_tool(ctx.workspace, ctx.project.path),
        git_diff_tool(ctx.repo, ctx.integration_branch, ctx.branch),
    ]
    if deps.rag is not None:
        tools.append(search_codebase_tool(deps.rag, ctx.run_id))
    return tools


def make_reviewer(deps: GraphDeps) -> NodeFn:
    async def reviewer(state: dict[str, Any]) -> Command[str]:
        if await deps.steering.pause_requested(state["run_id"]):  # safe point (Phase 15)
            return Command(goto="hold", update={"hold_next": "reviewer"})
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
            if rules:
                # Folded into the system prompt so a Claude Cloud run can cache this prefix
                # instead of repaying for the same rules on every re-review of this task.
                system = f"{system}\n\n## {ctx.task.stack} rules\n{rules}"
            context = reviewer_context(
                ctx.task,
                ctx.plan,
                ctx.design,
                diff,
                budget_for(deps.llm.spec(Role.REVIEWER).prompt_budget, system),
                related_code=await retrieve(deps, ctx.run_id, task_query(ctx.task)),
                notes=await task_notes(deps, state, ctx.task.id, deliver=False),
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
            review = merge_duplicate_issues(
                drop_out_of_scope(result.value, ctx.task, ctx.plan, diff)
            )

        ctx.ts.review = review
        if review.decision == "approve":
            ctx.ts.status = TaskStatus.TESTING
            return Command(goto="qa", update=ctx.update())
        ctx.ts.status = TaskStatus.IN_PROGRESS
        ctx.ts.feedback = render_feedback(review, deps.rules.rule_texts([ctx.task.stack]))
        return Command(goto="developer", update=ctx.update())

    return reviewer
