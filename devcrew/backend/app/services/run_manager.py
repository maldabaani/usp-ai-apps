"""Runs the graph in the background for the API.

- One background drive per run at a time (start / resume / continue); the API returns
  immediately and progress arrives as events (SSE).
- Cancel stops the drive, marks the run cancelled and releases its containers, volumes,
  worktrees and Chroma collection.
- On startup `recover()` continues every run that was mid-flight when the backend stopped
  (from its last checkpoint). Runs waiting for human input simply keep waiting.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.db.models import Run, RunStatus
from app.db.repository import RunStore
from app.events.bus import EventBus
from app.events.types import EventType
from app.graph.interrupts import ResumeAction, ResumePayload
from app.graph.runner import PendingInterrupt, RunDriver, RunOutcome
from app.graph.runtime import GraphDeps, release_run_resources
from app.graph.steering import expire_messages
from app.tools.git import GitRepo, repo_lock

logger = logging.getLogger(__name__)

# Statuses in which the graph is (or should be) executing, i.e. not waiting for a human.
ACTIVE_STATUSES = {
    RunStatus.PENDING,
    RunStatus.PREPARING,
    RunStatus.PLANNING,
    RunStatus.DESIGNING,
    RunStatus.SCAFFOLDING,
    RunStatus.EXECUTING,
    RunStatus.INTEGRATING,
    RunStatus.CHECKING,
    RunStatus.DELIVERING,
}


class RunNotFoundError(LookupError):
    pass


class RunConflictError(RuntimeError):
    """The request does not fit the run's current state (HTTP 409)."""


class InvalidResumeError(ValueError):
    """The resume payload does not fit the pending interrupt (HTTP 422)."""


class RunManager:
    def __init__(
        self, driver: RunDriver, runs: RunStore, events: EventBus, deps: GraphDeps
    ) -> None:
        self.driver = driver
        self.runs = runs
        self.events = events
        self.deps = deps
        self._tasks: dict[str, asyncio.Task[RunOutcome | None]] = {}

    # ------------------------------------------------------------------------------ queries
    def is_busy(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        return task is not None and not task.done()

    async def get(self, run_id: str) -> Run:
        run = await self.runs.get(run_id)
        if run is None:
            raise RunNotFoundError(run_id)
        return run

    async def state(self, run_id: str) -> dict[str, Any]:
        return await self.driver.state(run_id)

    async def pending(self, run_id: str) -> list[PendingInterrupt]:
        return await self.driver.pending_interrupts(run_id)

    async def wait(self, run_id: str) -> RunOutcome | None:
        """Wait for the current background drive (tests and scripts)."""
        task = self._tasks.get(run_id)
        return await task if task is not None else None

    # ------------------------------------------------------------------------------ commands
    async def start(
        self,
        *,
        request: str,
        repo_target: str,
        create_repo: bool,
        target: str = "new",
        mode: str = "full",
        issue: dict[str, Any] | None = None,
        budget: dict[str, int] | None = None,
        models: dict[str, str] | None = None,
    ) -> Run:
        run = await self.runs.create(
            request=request, repo_target=repo_target, create_repo=create_repo
        )
        self._spawn(
            run.id,
            lambda: self.driver.start(
                run.id,
                request,
                repo_target,
                create_repo,
                target=target,
                mode=mode,
                issue=issue,
                budget=budget,
                models=models,
            ),
        )
        return run

    async def resume(
        self, run_id: str, payload: ResumePayload, interrupt_id: str | None = None
    ) -> PendingInterrupt:
        run = await self.get(run_id)
        if RunStatus(run.status).is_terminal:
            raise RunConflictError(f"run is {run.status}")
        if self.is_busy(run_id):
            raise RunConflictError("run is busy; wait for it to ask for input")
        pending = await self.pending(run_id)
        if not pending:
            raise RunConflictError("run is not waiting for input")
        if interrupt_id is None:
            if len(pending) > 1:
                raise InvalidResumeError("several questions are pending; pass interrupt_id")
            target = pending[0]
        else:
            match = [p for p in pending if p.id == interrupt_id]
            if not match:
                raise InvalidResumeError(f"no pending interrupt {interrupt_id}")
            target = match[0]
        allowed = target.value.get("allowed_actions", [])
        if payload.action.value not in allowed:
            raise InvalidResumeError(
                f"action '{payload.action}' not allowed here; allowed: {', '.join(allowed)}"
            )
        self._spawn(run_id, lambda: self.driver.resume(run_id, payload, target.id))
        return target

    async def cancel(self, run_id: str) -> None:
        run = await self.get(run_id)
        if RunStatus(run.status).is_terminal:
            raise RunConflictError(f"run is already {run.status}")
        task = self._tasks.pop(run_id, None)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self.runs.update(run_id, status=RunStatus.CANCELLED)
        await expire_messages(self.deps, run_id)
        await self.deps.steering.set_pause(run_id, False)
        await self.events.publish(
            run_id, EventType.STATUS, payload={"status": RunStatus.CANCELLED.value, "error": None}
        )
        await self._release(run_id)

    async def resume_paused(self, run_id: str) -> int:
        """Resume every pending pause (the run between waves, or tasks between iterations)."""
        if self.is_busy(run_id):
            return 0
        paused = [p for p in await self.pending(run_id) if p.value.get("kind") == "pause"]
        if not paused:
            return 0
        go = ResumePayload(action=ResumeAction.APPROVE)
        self._spawn(run_id, lambda: self.driver.resume_many(run_id, {p.id: go for p in paused}))
        return len(paused)

    async def retry(self, run_id: str) -> Run:
        """Continue a failed run from its last checkpoint: the step that failed runs again."""
        run = await self.get(run_id)
        if RunStatus(run.status) is not RunStatus.FAILED:
            raise RunConflictError(f"only failed runs can be retried (this one is {run.status})")
        if self.is_busy(run_id):
            raise RunConflictError("run is busy")
        state = await self.driver.state(run_id)
        if not state.get("run_id"):
            raise RunConflictError("the run has no checkpoint to continue from")
        status = RunStatus(state.get("status") or RunStatus.EXECUTING)
        if status.is_terminal:
            status = RunStatus.EXECUTING
        await self.runs.update(run_id, status=status, clear_error=True)
        await self.events.publish(
            run_id, EventType.STATUS, payload={"status": status.value, "error": None}
        )
        self.driver.forget_status(run_id)
        self._spawn(run_id, functools.partial(self.driver.continue_run, run_id))
        return await self.get(run_id)

    async def recover(self) -> list[str]:
        """Continue runs that were executing when the backend stopped."""
        resumed = []
        for run in await self.runs.list(limit=1000):
            if RunStatus(run.status) in ACTIVE_STATUSES and not self.is_busy(run.id):
                pending = await self.pending(run.id)
                if pending:  # it is actually waiting for a human: nothing to do
                    continue
                logger.info("recovering run %s (status %s)", run.id, run.status)
                self._spawn(run.id, functools.partial(self.driver.continue_run, run.id))
                resumed.append(run.id)
        return resumed

    async def shutdown(self) -> None:
        """Stop background drives WITHOUT changing run status: they resume on next start."""
        tasks = [t for t in self._tasks.values() if not t.done()]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()

    # ------------------------------------------------------------------------------ internals
    def _spawn(self, run_id: str, drive: Callable[[], Awaitable[RunOutcome]]) -> None:
        if self.is_busy(run_id):
            raise RunConflictError("run is busy")

        async def runner() -> RunOutcome | None:
            try:
                outcome = await drive()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # RunDriver already reports graph errors; this is a guard
                logger.exception("drive for run %s crashed", run_id)
                await self.runs.update(run_id, status=RunStatus.FAILED, error=str(exc))
                return None
            await self._sync(run_id, outcome)
            return outcome

        self._tasks[run_id] = asyncio.create_task(runner(), name=f"run-{run_id}")

    async def _sync(self, run_id: str, outcome: RunOutcome) -> None:
        """Copy results that live in graph state into the runs table."""
        try:
            state = await self.driver.state(run_id)
        except Exception:  # state unavailable (e.g. never started)
            return
        pr_url = state.get("pr_url")
        errors = state.get("errors") or []
        failed = outcome.status in (RunStatus.FAILED, RunStatus.CANCELLED)
        if pr_url or (failed and (errors or outcome.error)):
            # the exception that stopped the run beats an older, handled error in the state
            error = (outcome.error or errors[-1]) if failed else None
            await self.runs.update(run_id, pr_url=pr_url, error=error)

    async def _release(self, run_id: str) -> None:
        await release_run_resources(self.deps, run_id)
        try:
            state = await self.driver.state(run_id)
        except Exception:
            return
        workspace = state.get("workspace")
        if not workspace:
            return
        main = Path(workspace)
        repo = GitRepo(main)
        async with repo_lock(main):
            for tree in (await repo.worktrees())[1:]:
                await repo.worktree_remove(tree)
