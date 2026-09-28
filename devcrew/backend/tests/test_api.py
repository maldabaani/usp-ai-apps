"""HTTP API: runs lifecycle, resume/cancel, files/diff, SSE replay, restart recovery."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from app.api.sse import event_stream
from app.db.repository import InMemoryRunStore
from app.events.bus import EventBus
from app.events.store import InMemoryEventStore
from app.events.types import EventType
from tests.api_harness import Api, api, in_memory_container, wait_for_status
from tests.fakes import FakeChroma, FakeRunner, final, tool_call, tool_results
from tests.graph_harness import PLAN, make_harness

REQUEST = {"request": "Build a TODO API with CRUD", "repo_target": "me/todo", "create_repo": True}


async def create(a: Api, body: dict[str, Any] = REQUEST) -> str:
    resp = await a.client.post("/runs", json=body)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def parse_sse(text: str) -> list[dict[str, Any]]:
    events = []
    for block in text.split("\n\n"):
        data = [line[6:] for line in block.splitlines() if line.startswith("data: ")]
        if data:
            events.append(json.loads("\n".join(data)))
    return events


# ------------------------------------------------------------------------------ lifecycle
async def test_full_run_through_the_api(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        run_id = await create(a)
        run = await a.settle(run_id)
        assert run["status"] == "awaiting_plan_approval" and run["create_repo"] is True
        [pending] = run["pending"]
        assert pending["kind"] == "approval" and pending["artifact"] == "plan"
        assert pending["allowed_actions"] == ["approve", "reject", "edit"]
        assert pending["data"]["plan"]["tasks"][0]["id"] == "T1"

        run = await a.approve(run_id)
        assert run["status"] == "awaiting_design_approval" and run["plan"]["summary"]
        run = await a.approve(run_id)
        assert run["status"] == "awaiting_final_approval"
        assert {t["status"] for t in run["tasks"].values()} == {"merged"}
        assert run["integration"]["merged"] == ["T1", "T2"]
        assert run["integration_branch"].startswith(f"devcrew/{run_id}-")

        listed = (await a.client.get("/runs")).json()
        assert [r["id"] for r in listed] == [run_id] and listed[0]["busy"] is False

        run = await a.approve(run_id)
        assert run["status"] == "completed" and run["pending"] == []


async def test_create_run_validation(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        for body in (
            {**REQUEST, "request": "short"},
            {**REQUEST, "repo_target": "not a repo"},
            {**REQUEST, "repo_target": "owner/repo/extra"},
        ):
            assert (await a.client.post("/runs", json=body)).status_code == 422
        assert (await a.client.get("/runs/nope")).status_code == 404


async def test_resume_errors(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        assert (
            await a.client.post("/runs/nope/resume", json={"action": "approve"})
        ).status_code == 404
        run_id = await create(a)
        await a.settle(run_id)
        bad = await a.client.post(
            f"/runs/{run_id}/resume", json={"action": "answer", "answer": "x"}
        )
        assert bad.status_code == 422 and "not allowed" in bad.text
        no_feedback = await a.client.post(f"/runs/{run_id}/resume", json={"action": "reject"})
        assert no_feedback.status_code == 422 and "feedback" in no_feedback.text
        wrong_id = await a.client.post(
            f"/runs/{run_id}/resume", json={"action": "approve", "interrupt_id": "nope"}
        )
        assert wrong_id.status_code == 422

        a.harness.brain.delay = 0.05  # keep the next drive busy for a moment
        assert (
            await a.client.post(f"/runs/{run_id}/resume", json={"action": "approve"})
        ).status_code == 202
        busy = await a.client.post(f"/runs/{run_id}/resume", json={"action": "approve"})
        assert busy.status_code == 409 and "busy" in busy.text
        run = await a.settle(run_id)
        assert run["status"] == "awaiting_design_approval"


async def test_reject_and_edit_via_api(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        run_id = await create(a)
        await a.settle(run_id)
        resp = await a.client.post(
            f"/runs/{run_id}/resume", json={"action": "reject", "feedback": "Add due dates"}
        )
        assert resp.status_code == 202
        run = await a.settle(run_id)
        assert run["status"] == "awaiting_plan_approval"
        assert "Add due dates" in a.harness.brain.calls_for("planner")[-1].text()
        edited = {**PLAN, "summary": "edited by human"}
        resp = await a.client.post(
            f"/runs/{run_id}/resume", json={"action": "edit", "artifact": edited}
        )
        run = await a.settle(run_id)
        assert (
            run["status"] == "awaiting_design_approval"
            and run["plan"]["summary"] == "edited by human"
        )


async def test_parallel_questions_need_interrupt_id(tmp_path: Path) -> None:
    async with api(tmp_path, max_parallel_devs=2) as a:
        brain = a.harness.brain
        tasks = [{**PLAN["tasks"][0], "id": tid, "depends_on": []} for tid in ("A", "B")]
        brain.responders["planner"] = lambda c: final({**PLAN, "tasks": tasks})

        def developer(call: Any) -> Any:
            tid = str(call.messages[1].content).split("id: ", 1)[1].split("\n", 1)[0]
            results = tool_results(call)
            if not results:
                return tool_call("ask_human", question=f"Question {tid}?")
            if len(results) == 1:
                return tool_call("write_file", path=f"app/{tid}.py", content="x = 1\n")
            return final("done")

        brain.responders["developer"] = developer
        run_id = await create(a)
        await a.settle(run_id)
        await a.approve(run_id)
        run = await a.approve(run_id)
        assert run["status"] == "needs_human" and len(run["pending"]) == 2
        resp = await a.client.post(
            f"/runs/{run_id}/resume", json={"action": "answer", "answer": "x"}
        )
        assert resp.status_code == 422 and "interrupt_id" in resp.text
        for p in run["pending"]:
            resp = await a.client.post(
                f"/runs/{run_id}/resume",
                json={"action": "answer", "answer": "ok", "interrupt_id": p["interrupt_id"]},
            )
            assert resp.status_code == 202 and resp.json()["interrupt_id"] == p["interrupt_id"]
            await a.settle(run_id)
        run = await a.settle(run_id)
        assert run["status"] == "awaiting_final_approval"


# --------------------------------------------------------------------------------- cancel
async def test_cancel_waiting_run_releases_resources(tmp_path: Path) -> None:
    runner, chroma = FakeRunner(), FakeChroma()
    async with api(tmp_path, runner=runner, chroma=chroma) as a:
        run_id = await create(a)
        await a.settle(run_id)
        await a.approve(run_id)
        a.harness.brain.responders["reviewer"] = lambda c: final(
            {
                "decision": "changes_requested",
                "issues": [{"file": "x", "severity": "blocker", "message": "no"}],
            }
        )
        run = await a.approve(run_id)  # tasks run, T1 escalates at the iteration limit
        assert run["status"] == "needs_human"
        assert f"run_{run_id}" in chroma.collections

        resp = await a.client.post(f"/runs/{run_id}/cancel")
        assert resp.status_code == 200 and resp.json()["status"] == "cancelled"
        assert runner.cleaned == [run_id] and f"run_{run_id}" not in chroma.collections
        ws = Path(run["tasks"]["T1"]["worktree"]).parent.parent / "repo"
        from app.tools.git import GitRepo

        assert [p.name for p in await GitRepo(ws).worktrees()] == ["repo"]
        assert (await a.client.post(f"/runs/{run_id}/cancel")).status_code == 409
        assert (
            await a.client.post(f"/runs/{run_id}/resume", json={"action": "approve"})
        ).status_code == 409
        events = [e.type for e in await a.harness.events(run_id)]
        assert events[-1] is EventType.STATUS


async def test_cancel_busy_run_stops_the_drive(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        a.harness.brain.delay = 0.2
        run_id = await create(a)
        await asyncio.sleep(0.05)
        assert a.container.manager.is_busy(run_id)
        resp = await a.client.post(f"/runs/{run_id}/cancel")
        assert resp.status_code == 200 and resp.json()["status"] == "cancelled"
        assert not a.container.manager.is_busy(run_id)
        await asyncio.sleep(0.3)
        run = await a.container.runs.get(run_id)
        assert run is not None and run.status == "cancelled"  # nothing overwrote it


# ------------------------------------------------------------------------ files and diffs
async def test_files_and_diffs(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        run_id = await create(a)
        assert (await a.client.get(f"/runs/{run_id}/files")).status_code in (404, 200)
        await a.settle(run_id)
        assert (await a.client.get(f"/runs/{run_id}/files")).status_code == 404  # no workspace yet
        await a.approve(run_id)
        await a.approve(run_id)

        files = (await a.client.get(f"/runs/{run_id}/files")).json()
        paths = {f["path"] for f in files["files"]}
        assert {"app/main.py", "app/schemas/todo.py", "docs/design.md"} <= paths
        main_only = {
            f["path"]
            for f in (await a.client.get(f"/runs/{run_id}/files", params={"ref": "main"})).json()[
                "files"
            ]
        }
        assert "app/schemas/todo.py" not in main_only

        content = (await a.client.get(f"/runs/{run_id}/files/app/schemas/todo.py")).json()
        assert content["content"].startswith("# T1") and not content["binary"]
        t1 = (
            await a.client.get(
                f"/runs/{run_id}/files/app/schemas/todo.py", params={"ref": "task:T1"}
            )
        ).json()
        assert t1["content"] == content["content"]
        for bad in ("../etc/passwd", ".git/config"):
            assert (await a.client.get(f"/runs/{run_id}/files/{bad}")).status_code in (400, 404)
        assert (
            await a.client.get(f"/runs/{run_id}/files", params={"ref": "HEAD~1"})
        ).status_code == 400
        assert (await a.client.get(f"/runs/{run_id}/files/nope.py")).status_code == 404

        diff = (await a.client.get(f"/runs/{run_id}/diff", params={"task_id": "T1"})).json()
        assert "+++ b/app/schemas/todo.py" in diff["diff"] and "app/routers" not in diff["diff"]
        assert "tests/test_t1.py" in diff["diff"]
        whole = (await a.client.get(f"/runs/{run_id}/diff")).json()
        assert "app/routers/todos.py" in whole["diff"] and whole["base"] == "main"
        assert (
            await a.client.get(f"/runs/{run_id}/diff", params={"task_id": "Z"})
        ).status_code == 404


# -------------------------------------------------------------------------------------- SSE
async def test_sse_replay_and_last_event_id(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        run_id = await create(a)
        await a.settle(run_id)
        await a.client.post(f"/runs/{run_id}/cancel")  # terminal: the stream replays and closes

        full = parse_sse((await a.client.get(f"/runs/{run_id}/events")).text)
        assert full[0]["type"] == "status" and full[-1]["payload"]["status"] == "cancelled"
        ids = [e["id"] for e in full]
        assert ids == sorted(set(ids))
        middle = ids[len(ids) // 2]
        resumed = parse_sse(
            (
                await a.client.get(f"/runs/{run_id}/events", headers={"Last-Event-ID": str(middle)})
            ).text
        )
        assert [e["id"] for e in resumed] == [i for i in ids if i > middle]
        by_query = parse_sse(
            (await a.client.get(f"/runs/{run_id}/events", params={"last_event_id": middle})).text
        )
        assert by_query == resumed
        bad = await a.client.get(f"/runs/{run_id}/events", headers={"Last-Event-ID": "x"})
        assert bad.status_code == 400


async def test_event_stream_live_keepalive_and_terminal_close() -> None:
    bus = EventBus(InMemoryEventStore())
    finished = {"done": False}

    async def terminal() -> bool:
        return finished["done"]

    await bus.publish("r", EventType.NODE_STARTED, node="planner")
    stream = event_stream(bus, "r", None, run_is_terminal=terminal, keepalive_s=0.05)
    chunks = [await anext(stream), await anext(stream)]
    assert chunks[0].startswith("retry:") and "event: node_started" in chunks[1]
    assert await anext(stream) == ": keepalive\n\n"
    await bus.publish("r", EventType.AWAITING_INPUT, payload={"kind": "approval"})
    assert "event: awaiting_input" in await anext(stream)
    finished["done"] = True
    await bus.publish("r", EventType.STATUS, payload={"status": "completed"})
    assert '"completed"' in await anext(stream)
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert bus.subscriber_count("r") == 0


# ---------------------------------------------------------------------- restart recovery
async def test_backend_restart_resumes_active_runs(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    runs = InMemoryRunStore()
    first = in_memory_container(h, runs)
    async with api(tmp_path, container=first, harness=h) as a:
        run_id = await create(a)
        await a.settle(run_id)
        await a.approve(run_id)
        a.harness.brain.delay = 0.05
        await a.client.post(f"/runs/{run_id}/resume", json={"action": "approve"})
        await wait_for_status(a, run_id, "executing")
    # The first backend is gone (drive cancelled mid-flight); status is still "executing".
    run = await runs.get(run_id)
    assert run is not None and run.status == "executing"

    h.brain.delay = 0
    second = in_memory_container(h, runs)  # same checkpointer, run store and event log
    async with api(tmp_path, container=second, harness=h) as b:
        assert second.manager.is_busy(run_id)  # recovered by the lifespan
        run_detail = await b.settle(run_id)
        assert run_detail["status"] == "awaiting_final_approval"
        assert {t["status"] for t in run_detail["tasks"].values()} == {"merged"}


async def test_sse_live_over_real_server_with_reconnect(tmp_path: Path) -> None:
    """Real uvicorn + streaming client: live events, disconnect, reconnect with Last-Event-ID."""
    import httpx
    import uvicorn

    async with api(tmp_path) as a:
        server = uvicorn.Server(
            uvicorn.Config(a.app, host="127.0.0.1", port=0, log_level="error", lifespan="off")
        )
        serve = asyncio.create_task(server.serve())
        async with asyncio.timeout(5):
            while not server.started:  # noqa: ASYNC110 - uvicorn exposes only a flag
                await asyncio.sleep(0.01)
        base = f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
        try:
            async with httpx.AsyncClient(base_url=base, timeout=10) as http:
                run_id = (await http.post("/runs", json=REQUEST)).json()["id"]

                async def read_until(
                    resp: httpx.Response, kind: str, got: list[dict[str, Any]]
                ) -> None:
                    buffer = ""
                    async for chunk in resp.aiter_text():
                        buffer += chunk
                        while "\n\n" in buffer:
                            block, buffer = buffer.split("\n\n", 1)
                            got.extend(parse_sse(block + "\n\n"))
                            if got and got[-1]["type"] == kind:
                                return

                live: list[dict[str, Any]] = []
                async with http.stream("GET", f"/runs/{run_id}/events") as resp:
                    assert resp.headers["content-type"].startswith("text/event-stream")
                    async with asyncio.timeout(5):
                        await read_until(resp, "awaiting_input", live)  # plan approval
                assert live[-1]["payload"]["artifact"] == "plan"
                last_seen = live[-1]["id"]

                # While disconnected: approve the plan -> design approval events are published.
                await http.post(f"/runs/{run_id}/resume", json={"action": "approve"})
                await a.container.manager.wait(run_id)

                missed: list[dict[str, Any]] = []
                async with http.stream(
                    "GET", f"/runs/{run_id}/events", headers={"Last-Event-ID": str(last_seen)}
                ) as resp:
                    async with asyncio.timeout(5):
                        await read_until(resp, "awaiting_input", missed)
                assert missed[0]["id"] > last_seen
                assert {e["id"] for e in missed}.isdisjoint({e["id"] for e in live})
                assert missed[-1]["payload"]["artifact"] == "design"
                assert "architect" in {e["node"] for e in missed}
        finally:
            server.should_exit = True
            await serve
