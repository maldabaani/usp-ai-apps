"""Steering a running run (Phase 13): the human's chat messages, applied at safe points.

Safe points:
- Planner / Architect start: run-level messages become notes (the plan or design takes them in).
- The scheduler, before every wave: the Coordinator turns each run-level message into a note
  for all agents, a new task, a cancelled (not yet started) task, or an answer.
- A task's developer / reviewer turn: messages addressed to that task are part of its context.

Which run-level messages were applied is kept in the graph state (`steer_applied`), so a node
that re-runs after a crash applies each message exactly once; the message rows only show the
human what happened.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal, Self

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import Command
from pydantic import BaseModel, Field, ValidationError, ValidationInfo, model_validator

from app.db.models import RunMessage
from app.events.types import EventType
from app.graph.runtime import GraphDeps
from app.graph.state import Plan, PlanTask, TaskState, TaskStatus, dump, get_plan
from app.llm.models_config import Role
from app.llm.structured import StructuredOutputError, generate_structured

PENDING, DELIVERED, APPLIED, ANSWERED, EXPIRED = (
    "pending",
    "delivered",
    "applied",
    "answered",
    "expired",
)


class SteerItem(BaseModel):
    id: int
    action: Literal["note", "add_task", "cancel_task", "answer"]
    reply: str
    note: str = ""
    task_title: str = ""
    task_description: str = ""
    stack: str = ""
    target_files: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    cancel_task_id: str = ""


class Steer(BaseModel):
    items: list[SteerItem]

    @model_validator(mode="after")
    def _consistent(self, info: ValidationInfo) -> Self:
        ctx = info.context or {}
        ids: set[int] | None = ctx.get("ids")
        if ids is not None and {i.id for i in self.items} != ids:
            raise ValueError(f"return exactly one item per message id: {sorted(ids)}")
        for i in self.items:
            if not i.reply.strip():
                raise ValueError(f"message {i.id}: reply is required")
            if i.action == "note" and not i.note.strip():
                raise ValueError(f"message {i.id}: a note needs `note`")
            if i.action == "add_task":
                if not i.task_title.strip() or not i.task_description.strip():
                    raise ValueError(f"message {i.id}: add_task needs a title and description")
                if ctx.get("stacks") and i.stack not in ctx["stacks"]:
                    raise ValueError(
                        f"message {i.id}: stack must be one of {sorted(ctx['stacks'])}"
                    )
                unknown = set(i.depends_on) - set(ctx.get("task_ids") or set())
                if unknown:
                    raise ValueError(f"message {i.id}: unknown depends_on {sorted(unknown)}")
            if i.action == "cancel_task" and i.cancel_task_id not in (ctx.get("pending") or set()):
                raise ValueError(
                    f"message {i.id}: only pending tasks can be cancelled: "
                    f"{sorted(ctx.get('pending') or [])}"
                )
        return self


async def emit_message(deps: GraphDeps, run_id: str, m: RunMessage, **values: Any) -> None:
    await deps.emit(
        run_id,
        EventType.MESSAGE,
        node="steer",
        task_id=m.task_id,
        message_id=m.id,
        text=m.text,
        **values,
    )


def _task_final(state: dict[str, Any], task_id: str) -> bool:
    ts = (state.get("tasks") or {}).get(task_id)
    return ts is None or TaskStatus(ts.get("status", "pending")).is_final


async def open_run_messages(deps: GraphDeps, state: dict[str, Any]) -> list[RunMessage]:
    """Run-level messages not applied yet. A message to a task that is already finished (or
    unknown) counts as run-level, unless its developer already received it."""
    applied = set(state.get("steer_applied") or [])
    out = []
    for m in await deps.steering.messages(state["run_id"]):
        if m.id in applied or m.status == EXPIRED:
            continue
        if m.task_id is None or (m.status == PENDING and _task_final(state, m.task_id)):
            out.append(m)
    return out


def notes_text(notes: Sequence[dict[str, Any]]) -> list[str]:
    return [str(n["text"]) for n in notes]


async def absorb_notes(
    deps: GraphDeps, state: dict[str, Any], agent: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Planner / Architect: every open run-level message becomes a note."""
    notes = list(state.get("human_notes") or [])
    messages = await open_run_messages(deps, state)
    if not messages:
        return notes, {}
    reply = f"Given to the {agent} as a note."
    for m in messages:
        notes.append({"id": m.id, "text": m.text})
        await emit_message(deps, state["run_id"], m, status=APPLIED, action="note", reply=reply)
    await deps.steering.update_messages(
        [m.id for m in messages], status=APPLIED, action="note", reply=reply
    )
    applied = [*(state.get("steer_applied") or []), *(m.id for m in messages)]
    return notes, {"human_notes": notes, "steer_applied": applied}


def with_notes(
    deps: GraphDeps, agent: str, node: Callable[[dict[str, Any]], Awaitable[Command[str]]]
) -> Callable[[dict[str, Any]], Awaitable[Command[str]]]:
    """Planner / Architect: take in open run messages as notes before the agent runs."""

    async def wrapped(state: dict[str, Any]) -> Command[str]:
        notes, update = await absorb_notes(deps, state, agent)
        command = await node({**state, "human_notes": notes})
        if not update:
            return command
        merged = {**update, **(command.update or {})}
        return Command(goto=command.goto, update=merged)

    return wrapped


def steer_context(state: dict[str, Any], messages: Sequence[RunMessage]) -> str:
    plan = get_plan(state)
    tasks = state.get("tasks") or {}
    lines = [
        f"Feature request: {str(state.get('request', '')).strip()[:2000]}",
        f"Plan summary: {plan.summary}",
        "Stacks: " + ", ".join(sorted({t.stack for t in plan.tasks})),
        "",
        "Tasks (id · status · title · depends on):",
    ]
    for t in plan.tasks:
        status = (tasks.get(t.id) or {}).get("status", "pending")
        lines.append(f"- {t.id} · {status} · {t.title} · {', '.join(t.depends_on) or '-'}")
    notes = notes_text(state.get("human_notes") or [])
    if notes:
        lines += ["", "Notes the team already has:", *(f"- {n}" for n in notes)]
    lines += ["", "Messages from the human:"]
    for m in messages:
        about = f" (about task {m.task_id}, which is finished)" if m.task_id else ""
        lines.append(f"- id={m.id}{about}: {m.text.strip()}")
    return "\n".join(lines)


async def apply_at_schedule(
    deps: GraphDeps, state: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The scheduler's safe point. Returns the state with the changes and the update to merge."""
    messages = await open_run_messages(deps, state)
    if not messages:
        return state, {}
    run_id = state["run_id"]
    plan = get_plan(state)
    tasks = dict(state.get("tasks") or {})
    pending = {t for t, s in tasks.items() if s.get("status") == TaskStatus.PENDING.value}
    context = {
        "ids": {m.id for m in messages},
        "pending": pending,
        "stacks": {t.stack for t in plan.tasks},
        "task_ids": {t.id for t in plan.tasks},
    }
    try:
        result = await generate_structured(
            deps.llm,
            Role.COORDINATOR,
            [
                SystemMessage(content=deps.prompts.get("steer", Steer)),
                HumanMessage(content=steer_context(state, messages)),
            ],
            Steer,
            context=context,
        )
        decisions = {i.id: i for i in result.value.items}
    except StructuredOutputError as exc:
        await deps.emit(
            run_id, EventType.ERROR, node="steer", message=f"could not sort messages: {exc}"
        )
        decisions = {}

    notes = list(state.get("human_notes") or [])
    task_updates: dict[str, dict[str, Any]] = {}
    new_tasks: list[PlanTask] = []
    for m in messages:
        d = decisions.get(m.id) or SteerItem(
            id=m.id, action="note", note=m.text, reply="Noted; the team will follow it."
        )
        action, reply = d.action, d.reply
        if d.action == "add_task":
            try:
                task = PlanTask(
                    id=f"H{m.id}",
                    title=d.task_title[:120],
                    description=d.task_description,
                    target_files=d.target_files,
                    depends_on=d.depends_on,
                    stack=d.stack,  # validated against the plan stacks
                )
                Plan.model_validate(
                    {
                        **dump(plan),
                        "tasks": [*map(dump, plan.tasks), *map(dump, new_tasks), dump(task)],
                    }
                )
            except ValidationError as exc:
                action, reply = (
                    "note",
                    f"Could not add the task ({exc.errors()[0]['msg']}); kept as a note.",
                )
                notes.append({"id": m.id, "text": m.text})
            else:
                new_tasks.append(task)
                task_updates[task.id] = dump(TaskState(id=task.id))
        elif d.action == "cancel_task":
            ts = TaskState.model_validate(tasks[d.cancel_task_id])
            ts.status = TaskStatus.CANCELLED
            ts.error = f"cancelled on request: {m.text[:200]}"
            task_updates[ts.id] = dump(ts)
        elif d.action == "note":
            notes.append({"id": m.id, "text": d.note})
        status = ANSWERED if action == "answer" else APPLIED
        await deps.steering.update_messages([m.id], status=status, action=action, reply=reply)
        await emit_message(deps, run_id, m, status=status, action=action, reply=reply)

    update: dict[str, Any] = {
        "human_notes": notes,
        "steer_applied": [*(state.get("steer_applied") or []), *(m.id for m in messages)],
    }
    if new_tasks:
        update["plan"] = dump(plan.model_copy(update={"tasks": [*plan.tasks, *new_tasks]}))
    if task_updates:
        update["tasks"] = task_updates
    new_state = {
        **state,
        **update,
        "tasks": {**tasks, **task_updates},
    }
    return new_state, update


async def task_notes(
    deps: GraphDeps, state: dict[str, Any], task_id: str, *, deliver: bool
) -> list[str]:
    """Run notes plus the messages addressed to this task. `deliver` marks new ones delivered
    (the developer's turn is the safe point for task messages)."""
    run_id = state["run_id"]
    mine = [
        m
        for m in await deps.steering.messages(run_id)
        if m.task_id == task_id and m.status in (PENDING, DELIVERED)
    ]
    if deliver:
        fresh = [m for m in mine if m.status == PENDING]
        reply = f"Given to the developer of {task_id}."
        for m in fresh:
            await emit_message(deps, run_id, m, status=DELIVERED, action="note", reply=reply)
        await deps.steering.update_messages(
            [m.id for m in fresh], status=DELIVERED, action="note", reply=reply
        )
    return notes_text(state.get("human_notes") or []) + [
        f"(for this task) {m.text.strip()}" for m in mine
    ]


async def expire_messages(deps: GraphDeps, run_id: str) -> None:
    """End of the run: messages that never reached a safe point."""
    stale = [m.id for m in await deps.steering.messages(run_id) if m.status == PENDING]
    await deps.steering.update_messages(
        stale, status=EXPIRED, reply="The run finished before this message could be applied."
    )
