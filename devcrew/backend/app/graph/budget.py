"""Run budgets (Phase 14): tokens and working minutes, checked before every wave."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END
from langgraph.types import Command

from app.db.models import RunStatus
from app.events.types import EventType
from app.graph.interrupts import InterruptKind, InterruptRequest, ResumeAction, request_input
from app.graph.runtime import GraphDeps, NodeFn, release_run_resources
from app.graph.steering import expire_messages
from app.services.usage import RunUsage, over_budget, summarize


def base_budget(deps: GraphDeps, state: dict[str, Any]) -> dict[str, int]:
    own = state.get("budget")
    if own is not None:
        return {"tokens": int(own.get("tokens") or 0), "minutes": int(own.get("minutes") or 0)}
    return {
        "tokens": deps.settings.run_token_budget,
        "minutes": deps.settings.run_time_budget_min,
    }


def current_limit(deps: GraphDeps, state: dict[str, Any]) -> dict[str, int]:
    return dict(state.get("budget_limit") or base_budget(deps, state))


async def run_usage(deps: GraphDeps, run_id: str) -> RunUsage:
    events = [e async for e in deps.events.replay(run_id)]
    return summarize(events, finished=False)


async def budget_reasons(deps: GraphDeps, state: dict[str, Any]) -> list[str]:
    limit = current_limit(deps, state)
    if not limit.get("tokens") and not limit.get("minutes"):
        return []
    return over_budget(await run_usage(deps, state["run_id"]), limit)


def make_budget(deps: GraphDeps) -> NodeFn:
    """The run reached its budget: continue (the budget grows by its base again) or stop."""

    async def budget(state: dict[str, Any]) -> Command[str]:
        run_id = state["run_id"]
        usage = await run_usage(deps, run_id)
        limit = current_limit(deps, state)
        reasons = over_budget(usage, limit)
        payload = request_input(
            InterruptRequest(
                kind=InterruptKind.BUDGET,
                title="Budget reached",
                allowed_actions=[ResumeAction.APPROVE, ResumeAction.REJECT],
                data={
                    "reasons": reasons,
                    "limit": limit,
                    "base": base_budget(deps, state),
                    "used_tokens": usage.total_tokens,
                    "active_minutes": round(usage.active_s / 60, 1),
                },
            )
        )
        if payload.action is ResumeAction.APPROVE:
            base = base_budget(deps, state)
            usage = await run_usage(deps, run_id)
            raised = {
                "tokens": usage.total_tokens + base["tokens"] if base["tokens"] else 0,
                "minutes": int(usage.active_s / 60) + base["minutes"] if base["minutes"] else 0,
            }
            await deps.emit(
                run_id,
                EventType.TOOL_RESULT,
                node="budget",
                tool="budget",
                ok=True,
                result=f"continued; new limit {raised}",
            )
            return Command(
                goto="schedule",
                update={"budget_limit": raised, "status": RunStatus.EXECUTING.value},
            )
        reason = "stopped at the budget: " + ("; ".join(reasons) or "limit reached")
        await deps.emit(run_id, EventType.ERROR, node="budget", message=reason)
        await release_run_resources(deps, run_id)
        await expire_messages(deps, run_id)
        await deps.steering.set_pause(run_id, False)
        return Command(goto=END, update={"status": RunStatus.CANCELLED.value, "errors": [reason]})

    return budget
