"""Restart survival with the real Postgres checkpointer (needs TEST_DATABASE_URL)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from app.db.models import RunStatus
from app.graph.backbone import build_graph
from app.graph.checkpointer import postgres_checkpointer, to_psycopg_conninfo
from app.graph.runner import RunDriver
from tests.conftest import TEST_DATABASE_URL
from tests.fakes import final
from tests.graph_harness import APPROVE, make_harness

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")


def test_conninfo_conversion() -> None:
    assert to_psycopg_conninfo("postgresql+asyncpg://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"
    with pytest.raises(ValueError):
        to_psycopg_conninfo("nonsense")


async def test_run_survives_backend_restart(tmp_path: Path) -> None:
    assert TEST_DATABASE_URL
    run_id = uuid.uuid4().hex

    # "Process 1": plan approved, then the backend dies while awaiting design approval.
    async with postgres_checkpointer(TEST_DATABASE_URL) as saver:
        h = make_harness(tmp_path, checkpointer=saver)
        await h.driver.start(run_id, "Build a TODO API", "me/todo", False)
        outcome = await h.driver.resume(run_id, APPROVE)
        assert outcome.status is RunStatus.AWAITING_DESIGN_APPROVAL

    # "Process 2": new pool, new graph, same thread id.
    async with postgres_checkpointer(TEST_DATABASE_URL) as saver:
        driver = RunDriver(build_graph(h.deps, saver), h.deps.events)
        pending = await driver.pending_interrupts(run_id)
        assert [p.value["artifact"] for p in pending] == ["design"]
        state = await driver.state(run_id)
        assert state["status"] == RunStatus.AWAITING_DESIGN_APPROVAL
        assert state["plan"]["tasks"][0]["id"] == "T1"

        outcome = await driver.resume(run_id, APPROVE)
        while outcome.kind == "interrupted":
            outcome = await driver.resume(run_id, APPROVE)
        assert outcome.status is RunStatus.COMPLETED
    assert len(h.brain.calls_for("planner")) == 1


async def test_parallel_interrupts_survive_restart(tmp_path: Path) -> None:
    """Two tasks ask the human in the same wave; answer one, restart, answer the other."""
    from app.graph.interrupts import ResumePayload
    from tests.fakes import tool_call, tool_results
    from tests.graph_harness import PLAN
    from tests.test_parallel import task, tid_of, writing_developer

    assert TEST_DATABASE_URL
    run_id = uuid.uuid4().hex

    def developer(call: Any) -> Any:
        tid, results = tid_of(call), tool_results(call)
        if tid in "AB" and not results:
            return tool_call("ask_human", question=f"Question from {tid}?")
        if tid in "AB" and len(results) == 1:
            return tool_call("write_file", path=f"app/{tid.lower()}.py", content="x = 1\n")
        return writing_developer()(call)

    plan = {**PLAN, "tasks": [task("A"), task("B"), task("C", ["A", "B"])]}
    async with postgres_checkpointer(TEST_DATABASE_URL) as saver:
        h = make_harness(tmp_path, checkpointer=saver, max_parallel_devs=2)
        h.brain.responders["developer"] = developer
        h.brain.responders["planner"] = lambda c: final(plan)
        await h.driver.start(run_id, "x", "me/x", False)
        await h.driver.resume(run_id, APPROVE)
        outcome = await h.driver.resume(run_id, APPROVE)
        pending = {p.value["data"]["task_id"]: p.id for p in outcome.interrupts}
        assert set(pending) == {"A", "B"}
        await h.driver.resume(run_id, ResumePayload(action="answer", answer="a"), pending["A"])

    async with postgres_checkpointer(TEST_DATABASE_URL) as saver:
        driver = RunDriver(build_graph(h.deps, saver), h.deps.events)
        waiting = await driver.pending_interrupts(run_id)
        assert [p.value["data"]["task_id"] for p in waiting] == ["B"]
        outcome = await driver.resume(run_id, ResumePayload(action="answer", answer="b"))
        assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL
        assert {t["status"] for t in (await driver.state(run_id))["tasks"].values()} == {"merged"}
