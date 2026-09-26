"""Drives a run's graph: start, resume after an interrupt, continue after a crash.

Mirrors status changes to the runs table and the event bus, and publishes an
`awaiting_input` event whenever the graph interrupts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Interrupt, StateSnapshot

from app.db.models import RunStatus
from app.db.repository import RunStore
from app.events.bus import EventBus
from app.events.types import EventType
from app.graph.backbone import initial_state
from app.graph.interrupts import InterruptKind, ResumePayload

logger = logging.getLogger(__name__)

RECURSION_LIMIT = 1000

AWAITING_STATUS = {
    "plan": RunStatus.AWAITING_PLAN_APPROVAL,
    "design": RunStatus.AWAITING_DESIGN_APPROVAL,
    "final": RunStatus.AWAITING_FINAL_APPROVAL,
}


@dataclass
class PendingInterrupt:
    id: str
    value: dict[str, Any]


@dataclass
class RunOutcome:
    kind: Literal["interrupted", "finished", "failed"]
    status: RunStatus
    interrupts: list[PendingInterrupt] = field(default_factory=list)
    error: str | None = None


def status_for_interrupt(value: dict[str, Any]) -> RunStatus:
    if value.get("kind") == InterruptKind.APPROVAL:
        return AWAITING_STATUS.get(str(value.get("artifact")), RunStatus.NEEDS_HUMAN)
    return RunStatus.NEEDS_HUMAN


class RunDriver:
    def __init__(
        self,
        graph: CompiledStateGraph[Any, Any, Any, Any],
        events: EventBus,
        runs: RunStore | None = None,
    ) -> None:
        self.graph = graph
        self.events = events
        self.runs = runs
        self._last_status: dict[str, tuple[RunStatus, str | None]] = {}

    @staticmethod
    def config(run_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": run_id}, "recursion_limit": RECURSION_LIMIT}

    async def start(
        self,
        run_id: str,
        request: str,
        repo_target: str,
        create_repo: bool = False,
        *,
        target: str = "new",
        mode: str = "full",
    ) -> RunOutcome:
        state = initial_state(run_id, request, repo_target, create_repo, target=target, mode=mode)
        await self._set_status(run_id, RunStatus(state["status"]))
        return await self._drive(run_id, state)

    async def resume(
        self, run_id: str, payload: ResumePayload, interrupt_id: str | None = None
    ) -> RunOutcome:
        pending = await self.pending_interrupts(run_id)
        if not pending:
            raise ValueError(f"run {run_id} is not waiting for input")
        if interrupt_id is None:
            if len(pending) > 1:
                raise ValueError("several interrupts are pending; pass interrupt_id")
            interrupt_id = pending[0].id
        elif interrupt_id not in {p.id for p in pending}:
            raise ValueError(f"unknown interrupt {interrupt_id}")
        command: Command[Any] = Command(resume={interrupt_id: payload.model_dump(mode="json")})
        return await self._drive(run_id, command)

    async def continue_run(self, run_id: str) -> RunOutcome:
        """Re-enter a run from its last checkpoint (e.g. after a backend restart)."""
        return await self._drive(run_id, None)

    async def state(self, run_id: str) -> dict[str, Any]:
        """Current run state, with live task states from in-flight task subgraphs merged in."""
        snapshot = await self.graph.aget_state(self.config(run_id), subgraphs=True)
        values = dict(snapshot.values)
        tasks = dict(values.get("tasks") or {})
        for pending in snapshot.tasks:
            sub = pending.state
            if isinstance(sub, StateSnapshot):
                tasks.update(sub.values.get("tasks") or {})
        values["tasks"] = tasks
        return values

    async def pending_interrupts(self, run_id: str) -> list[PendingInterrupt]:
        """Interrupts still waiting for input.

        With parallel tasks, a task that was already resumed and finished keeps its (answered)
        interrupt in `snapshot.interrupts` until the whole wave completes. Such a task carries its
        output as `result`; a task that is still waiting has no result (None, or {} when it
        re-interrupted after a resume). Every interrupting node here finishes with a non-empty
        update, so an empty result reliably means "still pending".
        """
        snapshot = await self.graph.aget_state(self.config(run_id))
        return [
            PendingInterrupt(i.id, dict(i.value))
            for task in snapshot.tasks
            if not task.result
            for i in task.interrupts
        ]

    async def _drive(self, run_id: str, graph_input: Any) -> RunOutcome:
        interrupts: list[Interrupt] = []
        try:
            async for chunk in self.graph.astream(
                graph_input, self.config(run_id), stream_mode="updates"
            ):
                for node, update in chunk.items():
                    if node == "__interrupt__":
                        interrupts.extend(update)
                    elif isinstance(update, dict) and "status" in update:
                        await self._set_status(run_id, RunStatus(update["status"]))
        except Exception as exc:
            logger.exception("run %s failed", run_id)
            message = f"{type(exc).__name__}: {exc}"
            await self.events.publish(
                run_id, EventType.ERROR, node="runner", payload={"message": message[:2000]}
            )
            await self._set_status(run_id, RunStatus.FAILED, error=message)
            return RunOutcome("failed", RunStatus.FAILED, error=message)

        if interrupts:
            pending = [PendingInterrupt(i.id, dict(i.value)) for i in interrupts]
            status = status_for_interrupt(pending[0].value)
            await self._set_status(run_id, status)
            for p in pending:
                await self.events.publish(
                    run_id,
                    EventType.AWAITING_INPUT,
                    payload={"interrupt_id": p.id, **p.value},
                )
            return RunOutcome("interrupted", status, pending)

        final = await self.state(run_id)
        status = RunStatus(final.get("status", RunStatus.COMPLETED))
        await self._set_status(run_id, status)
        return RunOutcome("finished", status)

    async def _set_status(
        self, run_id: str, status: RunStatus, *, error: str | None = None
    ) -> None:
        if self._last_status.get(run_id) == (status, error):
            return
        self._last_status[run_id] = (status, error)
        if self.runs is not None:
            await self.runs.update(run_id, status=status, error=error)
        await self.events.publish(
            run_id, EventType.STATUS, payload={"status": status.value, "error": error}
        )
