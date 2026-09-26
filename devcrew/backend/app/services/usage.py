"""Run usage from the event log: tokens per role and task, model time, working time."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.events.types import Event, EventType


class UsageLine(BaseModel):
    key: str  # role name or task id ("" = not in a task)
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    model_seconds: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class RunUsage(BaseModel):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    model_seconds: float = 0.0
    elapsed_s: float = 0.0  # first event -> now (or the last event of a finished run)
    waiting_s: float = 0.0  # time spent waiting for the human (approvals, questions, pauses)
    active_s: float = 0.0  # elapsed - waiting: what time budgets count
    by_role: list[UsageLine] = Field(default_factory=list)
    by_task: list[UsageLine] = Field(default_factory=list)
    budget: dict[str, int] | None = None


def _add(lines: dict[str, UsageLine], key: str, event: Event) -> None:
    p = event.payload
    line = lines.setdefault(key, UsageLine(key=key))
    line.calls += 1
    line.input_tokens += int(p.get("input_tokens") or 0)
    line.output_tokens += int(p.get("output_tokens") or 0)
    line.model_seconds += int(p.get("duration_ms") or 0) / 1000


def summarize(events: Sequence[Event], *, finished: bool, now: datetime | None = None) -> RunUsage:
    usage = RunUsage()
    if not events:
        return usage
    roles: dict[str, UsageLine] = {}
    tasks: dict[str, UsageLine] = {}
    waiting_since: datetime | None = None
    for event in events:
        if event.type is EventType.LLM_USAGE:
            _add(roles, str(event.payload.get("role", "?")), event)
            if event.task_id:
                _add(tasks, event.task_id, event)
        elif event.type is EventType.AWAITING_INPUT:
            waiting_since = waiting_since or event.created_at
        elif event.type is EventType.NODE_STARTED and waiting_since is not None:
            usage.waiting_s += (event.created_at - waiting_since).total_seconds()
            waiting_since = None
    end = events[-1].created_at if finished else (now or datetime.now(UTC))
    if waiting_since is not None:  # still waiting
        usage.waiting_s += max(0.0, (end - waiting_since).total_seconds())
    usage.elapsed_s = max(0.0, (end - events[0].created_at).total_seconds())
    usage.active_s = max(0.0, usage.elapsed_s - usage.waiting_s)
    usage.by_role = sorted(roles.values(), key=lambda line: -line.total_tokens)
    usage.by_task = sorted(tasks.values(), key=lambda line: line.key)
    for line in roles.values():
        usage.calls += line.calls
        usage.input_tokens += line.input_tokens
        usage.output_tokens += line.output_tokens
        usage.model_seconds += line.model_seconds
    usage.total_tokens = usage.input_tokens + usage.output_tokens
    usage.model_seconds = round(usage.model_seconds, 3)
    return usage


def over_budget(usage: RunUsage, budget: dict[str, int] | None) -> list[str]:
    """Which budget limits are reached (empty: within budget or no budget)."""
    if not budget:
        return []
    reasons = []
    if budget.get("tokens") and usage.total_tokens >= budget["tokens"]:
        reasons.append(f"{usage.total_tokens:,} tokens used (budget {budget['tokens']:,})")
    if budget.get("minutes") and usage.active_s / 60 >= budget["minutes"]:
        reasons.append(
            f"{usage.active_s / 60:.1f} working minutes (budget {budget['minutes']} min)"
        )
    return reasons
