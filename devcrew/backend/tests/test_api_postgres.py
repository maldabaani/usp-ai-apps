"""API over real Postgres (runs table, event log, LangGraph checkpointer) across a restart."""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.container import Container
from app.db.repository import RunRepository
from app.db.session import create_sessionmaker
from app.events.bus import EventBus
from app.events.store import PostgresEventStore
from app.graph.checkpointer import postgres_checkpointer
from tests.api_harness import Api, api, wait_for_status
from tests.conftest import TEST_DATABASE_URL
from tests.graph_harness import Harness, make_harness
from tests.test_api import REQUEST, parse_sse

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")


@asynccontextmanager
async def backend(h: Harness, engine: AsyncEngine, tmp_path: Path) -> AsyncIterator[Api]:
    """One 'backend process': fresh pool/checkpointer/bus over the same database."""
    assert TEST_DATABASE_URL
    sm = create_sessionmaker(engine)
    deps = dataclasses.replace(h.deps, events=EventBus(PostgresEventStore(sm)))
    async with postgres_checkpointer(TEST_DATABASE_URL) as saver:
        container = Container.assemble(
            deps.settings, deps=deps, runs=RunRepository(sm), checkpointer=saver, engine=engine
        )
        async with api(tmp_path, container=container, harness=h) as a:
            yield a


async def test_restart_keeps_waiting_and_active_runs(
    tmp_path: Path, pg_engine: AsyncEngine
) -> None:
    h = make_harness(tmp_path)
    async with backend(h, pg_engine, tmp_path) as a:
        waiting = (await a.client.post("/runs", json=REQUEST)).json()["id"]
        await a.settle(waiting)  # awaiting plan approval
        active = (await a.client.post("/runs", json=REQUEST)).json()["id"]
        await a.settle(active)
        await a.approve(active)
        h.brain.delay = 0.05
        await a.client.post(f"/runs/{active}/resume", json={"action": "approve"})
        await wait_for_status(a, active, "executing")
    # --- backend process gone -----------------------------------------------------------------
    h.brain.delay = 0
    async with backend(h, pg_engine, tmp_path) as b:
        run = await b.settle(active)  # recovered from its checkpoint by the lifespan
        assert run["status"] == "awaiting_final_approval"
        detail = (await b.client.get(f"/runs/{waiting}")).json()
        assert detail["status"] == "awaiting_plan_approval"
        assert detail["pending"][0]["artifact"] == "plan"
        run = await b.approve(waiting)
        assert run["status"] == "awaiting_design_approval"

        await b.client.post(f"/runs/{waiting}/cancel")
        events = parse_sse((await b.client.get(f"/runs/{waiting}/events")).text)
        # Events from both backend processes are replayed from Postgres, in order, no gaps.
        assert [e["id"] for e in events] == sorted({e["id"] for e in events})
        nodes = [e["node"] for e in events if e["type"] == "node_started"]
        assert nodes.count("planner") == 1 and "architect" in nodes
        assert events[-1]["payload"]["status"] == "cancelled"
        listed = {r["id"]: r["status"] for r in (await b.client.get("/runs")).json()}
        assert listed == {waiting: "cancelled", active: "awaiting_final_approval"}


async def test_watch_store_on_postgres(pg_engine: AsyncEngine) -> None:
    from sqlalchemy.exc import IntegrityError

    from app.db.models import Run
    from app.db.watch import WatchRepository

    sm = create_sessionmaker(pg_engine)
    store = WatchRepository(sm)
    row = await store.add_repo("acme/shop", poll_interval_s=120, extra_reviewers=["carol"])
    assert row.id and row.enabled and row.extra_reviewers == ["carol"]
    updated = await store.update_repo(row.id, enabled=False)
    assert updated is not None and updated.enabled is False
    assert [r.repo for r in await store.list_repos()] == ["acme/shop"]

    async with sm() as session, session.begin():
        session.add(Run(id="r" * 32, request="x", repo_target="acme/shop", status="pending"))
    ir = await store.add_issue_run(
        repo="acme/shop", issue_number=3, trigger="label:1", run_id="r" * 32
    )
    await store.update_issue_run(ir.id, comment_id=77, comment_text="hi")
    found = await store.issue_run_for("r" * 32)
    assert found is not None and found.comment_id == 77
    with pytest.raises(IntegrityError):  # one run per label event
        await store.add_issue_run(
            repo="acme/shop", issue_number=3, trigger="label:1", run_id="r" * 32
        )
    assert await store.delete_repo(row.id) and not await store.delete_repo(row.id)


async def test_steering_store_on_postgres(pg_engine: AsyncEngine) -> None:
    from app.db.models import Run
    from app.db.steering import SteeringRepository

    sm = create_sessionmaker(pg_engine)
    store = SteeringRepository(sm)
    async with sm() as session, session.begin():
        session.add(Run(id="s" * 32, request="x", repo_target="o/r", status="executing"))
    assert await store.pause_requested("s" * 32) is False
    await store.set_pause("s" * 32, True)
    assert await store.pause_requested("s" * 32) is True
    first = await store.add_message("s" * 32, "use snake_case", None)
    second = await store.add_message("s" * 32, "404 please", "T2")
    await store.update_messages([first.id], status="applied", action="note", reply="ok")
    rows = await store.messages("s" * 32)
    assert [(r.text, r.status, r.task_id) for r in rows] == [
        ("use snake_case", "applied", None),
        ("404 please", "pending", "T2"),
    ]
    assert second.id > first.id
    await store.update_messages([])  # no-op
