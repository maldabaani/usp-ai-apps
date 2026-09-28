"""Per-task subgraph: developer -> reviewer -> qa -> merge, with feedback loops.

    prepare -> developer -> reviewer --approve--> qa --pass--> merge -> END
                  ^  |          |                  |              |
                  |  |          +--changes---------+--fail--> developer
                  |  +--> ask_human --> developer                 |
                  +--- coordinator <-- iteration limit / agent errors / merge conflict
                        (retry | replan | split | escalate | resolve conflict)

The backbone scheduler dispatches up to MAX_PARALLEL_DEVS of these at once with the Send API.
Each runs in its own git worktree and branch; merges into the integration branch are
serialized with a per-repository lock and squashed to one commit per task.
"""

from __future__ import annotations

import shutil
from typing import Any, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.events.types import EventType
from app.graph.coordinator import make_task_coordinator, make_task_escalate
from app.graph.interrupts import InterruptKind, InterruptRequest, ResumeAction, request_input
from app.graph.nodes.developer import make_developer
from app.graph.nodes.human import make_ask_human
from app.graph.nodes.qa import make_qa
from app.graph.nodes.reviewer import make_reviewer
from app.graph.nodes.task_common import (
    load_task_ctx,
    task_branch,
    to_coordinator,
    worktree_path,
)
from app.graph.runtime import GraphDeps, NodeFn, instrument, reindex
from app.graph.state import TaskStatus, TaskWorkerOutput, TaskWorkerState
from app.tools.git import GitRepo, repo_lock


def make_prepare(deps: GraphDeps) -> NodeFn:
    async def prepare(state: dict[str, Any]) -> dict[str, Any]:
        ctx = load_task_ctx(deps, state)
        branch = task_branch(ctx.run_id, ctx.task.id)
        worktree = worktree_path(ctx.main_root, ctx.task.id)
        async with repo_lock(ctx.main_root):
            registered = worktree.exists() and await ctx.main_repo.is_worktree_of(
                worktree, ctx.main_root
            )
            if registered and ctx.ts.reset_branch:
                await ctx.main_repo.worktree_remove(worktree)
                registered = False
            if not registered:
                if worktree.exists():  # stale directory from an interrupted attempt
                    shutil.rmtree(worktree)
                # Replanned tasks restart from the integration branch; otherwise a re-run after
                # a crash keeps the existing branch and its commits.
                base = (
                    branch
                    if await ctx.main_repo.branch_exists(branch) and not ctx.ts.reset_branch
                    else ctx.integration_branch
                )
                await ctx.main_repo.worktree_add(worktree, branch, base)
                if ctx.ts.merge_from:
                    # PR follow-up: bring the PR base in; conflicts go to the developer.
                    task_repo = GitRepo(worktree)
                    conflicts = await task_repo.merge_in(ctx.ts.merge_from)
                    if conflicts:
                        ctx.ts.conflict_files = conflicts
                        ctx.ts.feedback = (
                            f"The pull request conflicts with its base branch. The base has been "
                            f"merged into your branch; resolve the conflicts in: "
                            f"{', '.join(conflicts)}. Keep both sides' intent, remove every "
                            "<<<<<<< ======= >>>>>>> marker and write the complete files."
                        )
                    else:
                        await task_repo.commit_all(f"{ctx.task.id}: merge the base branch")
        ctx.ts.branch = branch
        ctx.ts.worktree = str(worktree)
        ctx.ts.reset_branch = False
        ctx.ts.status = TaskStatus.IN_PROGRESS
        return ctx.update()

    return prepare


def make_merge(deps: GraphDeps) -> NodeFn:
    async def merge(state: dict[str, Any]) -> Command[str]:
        ctx = load_task_ctx(deps, state)
        async with repo_lock(ctx.main_root):
            if ctx.ts.merge_from:
                # keep the merge commit (base as a parent) so GitHub sees the conflict resolved
                result = await ctx.main_repo.fast_forward(ctx.branch, ctx.integration_branch)
            else:
                result = await ctx.main_repo.squash_merge(
                    ctx.branch, ctx.integration_branch, f"{ctx.task.id}: {ctx.task.title}"
                )
            if result.ok:
                # Index under the same lock so it always matches the integration HEAD.
                await reindex(deps, ctx.run_id, ctx.main_root, "merge")
                await ctx.main_repo.worktree_remove(ctx.worktree)
        if not result.ok:
            return await to_coordinator(
                deps,
                ctx,
                "merge",
                f"merge conflict in {', '.join(result.conflicts)}",
                kind="merge_conflict",
            )
        ctx.ts.status = TaskStatus.MERGED
        ctx.ts.commit = result.commit
        ctx.ts.worktree = None
        if deps.sandbox is not None:
            await deps.sandbox.release(ctx.run_id, ctx.task.id)
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


HOLD_STEPS = {"developer": "developer turn", "reviewer": "review", "qa": "QA run"}


def make_hold(deps: GraphDeps) -> NodeFn:
    """Paused before this task's next step; POST /pause resumes it."""

    async def hold(state: dict[str, Any]) -> Command[str]:
        task_id = state["task"]["id"]
        step = state.get("hold_next") or "developer"
        request_input(
            InterruptRequest(
                kind=InterruptKind.PAUSE,
                title=f"Paused before {task_id}'s next {HOLD_STEPS.get(step, step)}",
                allowed_actions=[ResumeAction.APPROVE],
                data={"task_id": task_id, "node": step},
            )
        )
        return Command(goto=step, update={"hold_next": None})

    return hold


def build_task_subgraph(deps: GraphDeps) -> CompiledStateGraph[Any, Any, Any, Any]:
    g = StateGraph(TaskWorkerState, output_schema=TaskWorkerOutput)
    nodes: dict[str, tuple[NodeFn, tuple[str, ...]]] = {
        "prepare": (make_prepare(deps), ()),
        "developer": (make_developer(deps), ("reviewer", "ask_human", "coordinator", "hold")),
        "hold": (make_hold(deps), ("developer", "reviewer", "qa")),
        "ask_human": (
            make_ask_human(deps, scratch_key="task_scratch", pending_key="task_pending_question"),
            ("developer",),
        ),
        "reviewer": (make_reviewer(deps), ("developer", "qa", "coordinator", "hold")),
        "qa": (make_qa(deps), ("developer", "merge", "coordinator", "hold")),
        "merge": (make_merge(deps), ("coordinator", END)),
        "coordinator": (
            make_task_coordinator(deps),
            ("developer", "reviewer", "qa", "escalate", END),
        ),
        "escalate": (make_task_escalate(deps), ("developer", "reviewer", "qa", "coordinator", END)),
    }
    for name, (fn, destinations) in nodes.items():
        g.add_node(name, cast(Any, instrument(deps, name, fn)), destinations=destinations or None)
    g.add_edge(START, "prepare")
    g.add_edge("prepare", "developer")
    return g.compile()
