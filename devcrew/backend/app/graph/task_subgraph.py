"""Per-task subgraph: developer -> reviewer -> qa -> merge, with feedback loops.

    prepare -> developer -> reviewer --approve--> qa --pass--> merge -> END
                  ^  |          |                  |
                  |  |          +--changes---------+--fail--> developer
                  |  +--> ask_human --> developer
                  +--- coordinator <-- (iteration limit / agent errors / merge conflict)

It is dispatched by the backbone scheduler with the Send API. Phase 2 runs one task at a time
in the main workspace; Phase 5 runs up to MAX_PARALLEL_DEVS tasks in separate git worktrees.
"""

from __future__ import annotations

from typing import Any, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.events.types import EventType
from app.graph.coordinator import make_task_coordinator
from app.graph.nodes.developer import make_developer
from app.graph.nodes.human import make_ask_human
from app.graph.nodes.qa import make_qa
from app.graph.nodes.reviewer import make_reviewer
from app.graph.nodes.task_common import load_task_ctx, task_branch, to_coordinator
from app.graph.runtime import GraphDeps, NodeFn, instrument
from app.graph.state import TaskStatus, TaskWorkerOutput, TaskWorkerState


def make_prepare(deps: GraphDeps) -> NodeFn:
    async def prepare(state: dict[str, Any]) -> dict[str, Any]:
        ctx = load_task_ctx(state)
        branch = task_branch(ctx.run_id, ctx.task.id)
        # Idempotent: a re-run (e.g. after a crash) keeps the existing branch and its commits.
        if await ctx.repo.branch_exists(branch):
            await ctx.repo.checkout(branch)
        else:
            await ctx.repo.checkout(branch, create_from=ctx.integration_branch)
        ctx.ts.branch = branch
        ctx.ts.status = TaskStatus.IN_PROGRESS
        return ctx.update()

    return prepare


def make_merge(deps: GraphDeps) -> NodeFn:
    async def merge(state: dict[str, Any]) -> Command[str]:
        ctx = load_task_ctx(state)
        result = await ctx.repo.squash_merge(
            ctx.branch, ctx.integration_branch, f"{ctx.task.id}: {ctx.task.title}"
        )
        if not result.ok:
            return await to_coordinator(
                deps, ctx, "merge", f"merge conflict in {', '.join(result.conflicts)}"
            )
        ctx.ts.status = TaskStatus.MERGED
        ctx.ts.commit = result.commit
        await deps.emit(
            ctx.run_id,
            EventType.MERGE,
            node="merge",
            task_id=ctx.task.id,
            branch=ctx.branch,
            into=ctx.integration_branch,
            commit=result.commit,
        )
        return Command(goto=END, update=ctx.update())

    return merge


def build_task_subgraph(deps: GraphDeps) -> CompiledStateGraph[Any, Any, Any, Any]:
    g = StateGraph(TaskWorkerState, output_schema=TaskWorkerOutput)
    nodes: dict[str, tuple[NodeFn, tuple[str, ...]]] = {
        "prepare": (make_prepare(deps), ()),
        "developer": (make_developer(deps), ("reviewer", "ask_human", "coordinator")),
        "ask_human": (
            make_ask_human(deps, scratch_key="task_scratch", pending_key="task_pending_question"),
            ("developer",),
        ),
        "reviewer": (make_reviewer(deps), ("developer", "qa", "coordinator")),
        "qa": (make_qa(deps), ("developer", "merge", "coordinator")),
        "merge": (make_merge(deps), ("coordinator", END)),
        "coordinator": (make_task_coordinator(deps), ("developer", END)),
    }
    for name, (fn, destinations) in nodes.items():
        g.add_node(name, cast(Any, instrument(deps, name, fn)), destinations=destinations or None)
    g.add_edge(START, "prepare")
    g.add_edge("prepare", "developer")
    return g.compile()
