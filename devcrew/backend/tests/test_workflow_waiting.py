"""Why a pending task has not started: developer limit, dependencies or the current wave."""

from __future__ import annotations

from typing import Any

from app.events.types import Event, EventType
from app.services.workflow import build_workflow

PLAN: dict[str, list[dict[str, Any]]] = {
    "tasks": [
        {"id": "T1", "title": "Model", "depends_on": []},
        {"id": "T2", "title": "Endpoint", "depends_on": ["T1"]},
        {"id": "T3", "title": "Tests", "depends_on": ["T1"]},
        {"id": "T4", "title": "Docs", "depends_on": ["T2"]},
        {"id": "T5", "title": "Extra", "depends_on": []},
    ]
}


def dispatch(event_id: int, tasks: list[str], waiting: list[str]) -> Event:
    return Event(
        id=event_id,
        run_id="r1",
        type=EventType.TOOL_RESULT,
        node="schedule",
        payload={"tool": "dispatch", "ok": True, "tasks": tasks, "waiting": waiting},
    )


def details(tasks: dict[str, dict[str, Any]], events: list[Event]) -> dict[str, str | None]:
    wf = build_workflow(
        run_id="r1",
        status="executing",
        request="Bookmarks API",
        created_at=None,
        state={"plan": PLAN, "tasks": tasks},
        events=events,
        pending=[],
        pr_url=None,
        max_dev_iterations=3,
    )
    return {n.task_id: n.detail for n in wf.nodes if n.task_id}


def test_pending_tasks_say_why_they_wait() -> None:
    tasks: dict[str, dict[str, Any]] = {
        "T1": {"status": "merged", "iterations": 1},
        "T2": {"status": "in_progress", "iterations": 1},
        "T3": {"status": "pending"},
        "T4": {"status": "pending"},
        "T5": {"status": "pending"},
    }
    d = details(tasks, [dispatch(1, ["T1"], []), dispatch(2, ["T2"], ["T3"])])
    assert d["T3"] == "ready · waiting for a free developer (MAX_PARALLEL_DEVS=1)"
    assert d["T4"] == "waiting for T2"
    # ready but not dispatched: it starts with the next wave
    assert d["T5"] == "ready · starts when the current wave finishes"


def test_before_development_tasks_are_planned() -> None:
    tasks: dict[str, dict[str, Any]] = {t["id"]: {"status": "pending"} for t in PLAN["tasks"]}
    assert set(details(tasks, []).values()) == {"planned"}


def build(status: str, tasks: dict[str, dict[str, Any]], events: list[Event]) -> dict[str, Any]:
    wf = build_workflow(
        run_id="r1",
        status=status,
        request="Bookmarks API",
        created_at=None,
        state={"plan": PLAN, "tasks": tasks},
        events=events,
        pending=[],
        pr_url=None,
        max_dev_iterations=3,
    )
    return {n.id: n for n in wf.nodes}


def test_a_cancelled_run_shows_stopped_and_unstarted_tasks() -> None:
    tasks: dict[str, dict[str, Any]] = {
        "T1": {"status": "merged", "iterations": 2},
        "T2": {"status": "in_progress", "iterations": 1},
        "T3": {"status": "pending"},
        "T4": {"status": "pending"},
        "T5": {"status": "pending"},
    }
    nodes = build("cancelled", tasks, [dispatch(1, ["T1"], []), dispatch(2, ["T2"], ["T3"])])
    assert (nodes["task:T2"].status, nodes["task:T2"].detail) == (
        "skipped",
        "stopped: run cancelled",
    )
    assert (nodes["task:T3"].status, nodes["task:T3"].detail) == (
        "skipped",
        "not started: run cancelled",
    )
    failed = build("failed", tasks, [])
    t2 = failed["task:T2"]
    assert (t2.status, t2.detail) == ("failed", "stopped: run stopped")


def node_event(event_id: int, kind: EventType, node: str) -> Event:
    return Event(id=event_id, run_id="r1", type=kind, node=node)


def test_approvals_count_decisions_not_restarts() -> None:
    # an approval node starts, interrupts, and starts again when the human answers
    events = [
        node_event(1, EventType.NODE_STARTED, "approve_plan"),
        node_event(2, EventType.NODE_STARTED, "approve_plan"),
        node_event(3, EventType.NODE_FINISHED, "approve_plan"),
    ]
    nodes = build("designing", {}, events)
    assert nodes["approve_plan"].runs == 1
