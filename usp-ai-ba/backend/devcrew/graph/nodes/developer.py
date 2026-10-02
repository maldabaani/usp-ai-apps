from __future__ import annotations

from typing import Any

from langgraph.types import Command

from devcrew.graph.context_builder import budget_for, developer_context
from devcrew.graph.nodes.task_common import TaskCtx, load_task_ctx, task_query, to_coordinator
from devcrew.graph.runtime import (
    GraphDeps,
    NodeFn,
    agent_turn,
    pending_question,
    qa_entries,
    questions_asked,
    retrieve,
    save_transcript,
)
from devcrew.graph.state import TaskStatus, dump
from devcrew.graph.steering import task_notes
from devcrew.llm.models_config import Role
from devcrew.tools.agents import QuestionBudget, ask_agent_tool
from devcrew.tools.base import ToolError, ToolSpec
from devcrew.tools.catalog import read_rules_tool
from devcrew.tools.human import ask_human_tool
from devcrew.tools.sandbox import run_command_tool
from devcrew.tools.search import search_codebase_tool
from devcrew.tools.storyforge_rag import retrieve_storyforge_context_tool
from devcrew.tools.workspace import list_dir_tool, read_file_tool, write_file_tool

NODE = "developer"


def developer_tools(
    deps: GraphDeps, ctx: TaskCtx, budget: QuestionBudget, *, bridged: bool
) -> list[ToolSpec]:
    tools = [
        read_file_tool(ctx.workspace, ctx.project.path),
        write_file_tool(ctx.workspace, project_path=ctx.project.path),
        list_dir_tool(ctx.workspace, ctx.project.path),
        read_rules_tool(deps.rules),
        ask_human_tool(limit_reached=lambda: budget.exhausted),
    ]
    if not bridged:
        # A bridged run's Plan/Design are deterministic translations of StoryForge's own
        # already-approved epic (pipeline/devcrew_bridge.py), not DevCrew's Planner/Architect
        # reasoning about anything -- Design.design_doc is literally each task's own description
        # concatenated back together, and Design.key_decisions says outright "Design authored by
        # StoryForge's own assessment pipeline, not DevCrew's Architect". Asking "architect" or
        # "planner" here spends a real LLM call to have a role with no actual judgment to add
        # paraphrase content the Developer already has in its own context (developer_context()
        # already includes the design contracts, key decisions, acceptance criteria and other
        # tasks) -- observed live: an ask_agent(architect, "what is LineItem for?") on a bridged
        # run, answerable by read_file alone. Non-bridged runs keep the tool: there a real
        # Planner/Architect ran and may hold detail the task description doesn't carry verbatim.
        tools.append(
            ask_agent_tool(
                deps,
                run_id=ctx.run_id,
                asker=NODE,
                task=ctx.task,
                plan=ctx.plan,
                design=ctx.design,
                budget=budget,
            )
        )
    if deps.sandbox is not None:
        tools.append(
            run_command_tool(deps.sandbox, ctx.target, ctx.layout, default_cwd=ctx.project.path)
        )
    if deps.rag is not None:
        tools.append(search_codebase_tool(deps.rag, ctx.run_id))
    if deps.storyforge_retrieval is not None:
        tools.append(retrieve_storyforge_context_tool(deps.storyforge_retrieval))
    return tools


def make_developer(deps: GraphDeps) -> NodeFn:
    async def developer(state: dict[str, Any]) -> Command[str]:
        # Safe point (Phase 14): pause between a task's developer iterations.
        if await deps.steering.pause_requested(state["run_id"]):
            return Command(goto="hold", update={"hold_next": "developer"})
        ctx = load_task_ctx(deps, state)
        max_iter = deps.settings.max_dev_iterations
        if ctx.ts.iterations >= max_iter:
            return await to_coordinator(
                deps,
                ctx,
                NODE,
                f"reached MAX_DEV_ITERATIONS ({max_iter}) without passing review and tests",
                kind="iteration_limit",
            )
        qa_log = state.get("qa_log", [])
        budget = QuestionBudget(
            asked=questions_asked(qa_log, NODE, ctx.task.id),
            limit=deps.settings.max_questions_per_task,
        )
        system = deps.prompts.get(NODE)
        try:
            rules = deps.rules.read(ctx.task.stack)
        except ToolError:
            rules = ""
        if rules:
            # Folded into the system prompt, not a context section: stack rules are static
            # across every attempt of this task (and every other task of this stack), so a
            # Claude Cloud run can cache this whole prefix instead of paying full price for the
            # same rules text on every single retry (see client.py's _with_cache_control()).
            system = f"{system}\n\n## {ctx.task.stack} rules\n{rules}"

        async def context() -> str:
            related = await retrieve(deps, ctx.run_id, task_query(ctx.task))
            notes = await task_notes(deps, state, ctx.task.id, deliver=True)
            # Empty on a first attempt (nothing committed yet); on a retry this is the real diff
            # against the integration branch, so it reflects every file written across every
            # earlier attempt on this branch -- not a hand-tracked list that could drift from it.
            touched = await ctx.repo.changed_files(ctx.integration_branch, ctx.branch)
            return developer_context(
                ctx.task,
                ctx.plan,
                ctx.design,
                ctx.ts,
                "\n".join(ctx.workspace.list(".", depth=4)),
                budget_for(deps.llm.spec(Role.DEVELOPER).prompt_budget, system),
                qa=qa_entries(qa_log, task_id=ctx.task.id),
                related_code=related,
                notes=notes,
                touched_files=touched,
            )

        outcome = await agent_turn(
            deps,
            role=Role.DEVELOPER,
            run_id=ctx.run_id,
            node=NODE,
            task_id=ctx.task.id,
            system=system,
            build_context=context,
            tools=developer_tools(deps, ctx, budget, bridged=state.get("storyforge_epic") is not None),
            saved=state.get("task_scratch", {}).get(NODE),
        )
        asked_agents = [dump(e) for e in budget.log]  # ask_agent Q&A of this turn -> qa_log
        if outcome.kind == "ask_human":
            question = await pending_question(deps, ctx.run_id, NODE, ctx.task.id, outcome)
            return Command(
                goto="ask_human",
                update={
                    "task_scratch": {NODE: save_transcript(outcome.messages)},
                    "task_pending_question": dump(question),
                    "qa_log": asked_agents,
                },
            )
        if outcome.kind == "error":
            return await to_coordinator(
                deps, ctx, NODE, outcome.error, extra={"qa_log": asked_agents}
            )

        ctx.ts.iterations += 1
        if ctx.ts.conflict_files:
            left = ctx.repo.files_with_markers(ctx.ts.conflict_files)
            if left:
                # Never commit conflict markers: send the developer back (bounded by iterations).
                ctx.ts.feedback = (
                    "Merge conflict markers (<<<<<<< ======= >>>>>>>) are still present in: "
                    + ", ".join(left)
                    + ". Edit these files to keep both sides' intent, remove "
                    "every marker, and write the complete files."
                )
                return Command(
                    goto="developer",
                    update=ctx.update(task_scratch={NODE: None}, qa_log=asked_agents),
                )
            ctx.ts.conflict_files = []
        await ctx.repo.commit_all(f"{ctx.task.id}: {ctx.task.title} (attempt {ctx.ts.iterations})")
        ctx.ts.status = TaskStatus.IN_REVIEW
        ctx.ts.feedback = None
        return Command(
            goto="build_check", update=ctx.update(task_scratch={NODE: None}, qa_log=asked_agents)
        )

    return developer
