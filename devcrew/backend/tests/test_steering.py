"""Phase 13: steering running work (chat messages applied at safe points, pause / resume)."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

from app.db.models import RunStatus
from app.services.run_manager import ACTIVE_STATUSES
from tests.api_harness import Api, api
from tests.fakes import Call, final
from tests.graph_harness import ESCALATE, current_task_id

Decide = Callable[[int, str], dict[str, Any]]
MESSAGE_LINE = re.compile(r"- id=(\d+)(?: \(about task \S+, which is finished\))?: (.*)")


def steering_coordinator(decide: Decide) -> Callable[[Call], AIMessage]:
    def respond(call: Call) -> AIMessage:
        context = str(call.messages[1].content)
        if "Messages from the human:" not in context:
            return final(ESCALATE)
        items = [
            {"id": int(i), **decide(int(i), text)} for i, text in MESSAGE_LINE.findall(context)
        ]
        return final({"items": items})

    return respond


def decide(_: int, text: str) -> dict[str, Any]:
    if text.startswith("Also add"):
        return {
            "action": "add_task",
            "reply": "Added a task for the health endpoint.",
            "task_title": "Health endpoint",
            "task_description": "GET /health returns ok",
            "stack": "python",
            "target_files": ["app/health.py"],
            "depends_on": ["T1"],
        }
    if text.startswith("Drop"):
        return {"action": "cancel_task", "reply": "Cancelled T2.", "cancel_task_id": "T2"}
    if text.endswith("?"):
        return {"action": "answer", "reply": "Two tasks are planned."}
    return {"action": "note", "note": text, "reply": "Noted for all developers."}


async def create(a: Api) -> str:
    resp = await a.client.post(
        "/runs", json={"request": "Build a TODO API with CRUD", "repo_target": "o/r"}
    )
    assert resp.status_code == 201, resp.text
    run_id = str(resp.json()["id"])
    await a.settle(run_id)
    return run_id


async def say(a: Api, run_id: str, text: str, task_id: str | None = None) -> dict[str, Any]:
    resp = await a.client.post(f"/runs/{run_id}/messages", json={"text": text, "task_id": task_id})
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


async def messages(a: Api, run_id: str) -> dict[str, dict[str, Any]]:
    rows = (await a.client.get(f"/runs/{run_id}/messages")).json()
    return {r["text"]: r for r in rows}


def developer_prompts(a: Api, task_id: str) -> list[str]:
    return [
        str(c.messages[1].content)
        for c in a.harness.brain.calls_for("developer")
        if c.fresh and current_task_id(c) == task_id
    ]


# ------------------------------------------------------------------------------ messages
async def test_messages_before_design_become_notes_for_the_architect(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        run_id = await create(a)  # awaiting plan approval
        await say(a, run_id, "Use snake_case for every field name")
        assert (await messages(a, run_id))["Use snake_case for every field name"]["status"] == (
            "pending"
        )
        run = await a.approve(run_id)  # -> architect (safe point) -> design approval
        assert run["status"] == "awaiting_design_approval"
        architect = str(a.harness.brain.calls_for("architect")[0].messages[1].content)
        assert "Notes from the human (follow them)" in architect
        assert "Use snake_case for every field name" in architect
        m = (await messages(a, run_id))["Use snake_case for every field name"]
        assert m["status"] == "applied" and m["reply"] == "Given to the Architect as a note."
        assert run["human_notes"] == [{"id": m["id"], "text": m["text"]}]

        run = await a.approve(run_id)  # the note reaches every developer too
        assert run["status"] == "awaiting_final_approval"
        for tid in ("T1", "T2"):
            assert "Use snake_case for every field name" in developer_prompts(a, tid)[0]
        # applied once: the scheduler did not ask the Coordinator about it again
        assert not a.harness.brain.calls_for("coordinator")


async def test_the_coordinator_applies_messages_before_the_next_wave(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        a.harness.brain.responders["coordinator"] = steering_coordinator(decide)
        run_id = await create(a)
        await a.approve(run_id)  # awaiting design approval: the next safe point is the scheduler
        await say(a, run_id, "Prefer small functions")
        await say(a, run_id, "Also add a health endpoint")
        await say(a, run_id, "Drop the router task")
        await say(a, run_id, "How many tasks are left?")
        run = await a.approve(run_id)
        assert run["status"] == "awaiting_final_approval"

        rows = await messages(a, run_id)
        assert rows["Prefer small functions"]["action"] == "note"
        assert rows["Also add a health endpoint"]["action"] == "add_task"
        assert rows["Drop the router task"]["reply"] == "Cancelled T2."
        assert rows["How many tasks are left?"]["status"] == "answered"
        assert rows["How many tasks are left?"]["reply"] == "Two tasks are planned."

        health = f"H{rows['Also add a health endpoint']['id']}"
        assert [t["id"] for t in run["plan"]["tasks"]] == ["T1", "T2", health]
        assert run["tasks"][health]["status"] == "merged"
        assert run["tasks"]["T2"]["status"] == "cancelled"
        assert run["integration"]["cancelled"] == ["T2"]
        assert "Prefer small functions" in developer_prompts(a, health)[0]
        # one Coordinator call for the four messages, none afterwards
        assert len(a.harness.brain.calls_for("coordinator")) == 1

        wf = (await a.client.get(f"/runs/{run_id}/workflow")).json()
        t2 = next(n for n in wf["nodes"] if n["id"] == "task:T2")
        assert t2["status"] == "skipped" and t2["detail"] == "cancelled on your request"


async def test_unusable_coordinator_output_keeps_messages_as_notes(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        a.harness.brain.responders["coordinator"] = lambda c: final("not json at all")
        run_id = await create(a)
        await a.approve(run_id)
        await say(a, run_id, "Log every request")
        run = await a.approve(run_id)
        assert run["status"] == "awaiting_final_approval"
        row = (await messages(a, run_id))["Log every request"]
        assert row["status"] == "applied" and row["action"] == "note"
        assert "Log every request" in developer_prompts(a, "T1")[0]


async def test_task_messages_reach_that_task_only(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        run_id = await create(a)
        await a.approve(run_id)
        await say(a, run_id, "Return 404 for unknown ids", task_id="T2")
        run = await a.approve(run_id)
        assert run["status"] == "awaiting_final_approval"
        assert "(for this task) Return 404 for unknown ids" in developer_prompts(a, "T2")[0]
        assert "Return 404" not in developer_prompts(a, "T1")[0]
        reviewer = [
            str(c.messages[1].content)
            for c in a.harness.brain.calls_for("reviewer")
            if "Return 404" in str(c.messages[1].content)
        ]
        assert reviewer  # the reviewer checks it too
        row = (await messages(a, run_id))["Return 404 for unknown ids"]
        assert row["status"] == "delivered" and row["reply"] == "Given to the developer of T2."


async def test_message_validation_and_expiry(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        run_id = await create(a)
        bad = await a.client.post(f"/runs/{run_id}/messages", json={"text": "x", "task_id": "T9"})
        assert bad.status_code == 422
        empty = await a.client.post(f"/runs/{run_id}/messages", json={"text": "   "})
        assert empty.status_code == 422
        await a.approve(run_id)
        await a.approve(run_id)  # awaiting final approval: no safe point before the end
        await say(a, run_id, "Too late for this one")
        await a.client.post(f"/runs/{run_id}/pause", json={"paused": True})
        run = await a.approve(run_id)
        assert run["status"] == "completed" and run["pause_requested"] is False
        row = (await messages(a, run_id))["Too late for this one"]
        assert row["status"] == "expired" and "finished before" in row["reply"]
        late = await a.client.post(f"/runs/{run_id}/messages", json={"text": "hello"})
        assert late.status_code == 409


# ------------------------------------------------------------------------------ pause
async def test_pause_between_waves_and_resume(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        run_id = await create(a)
        await a.approve(run_id)
        resp = await a.client.post(f"/runs/{run_id}/pause", json={"paused": True})
        assert resp.json() == {"status": "awaiting_design_approval", "pause_requested": True}
        run = await a.approve(run_id)  # scaffold -> scheduler: pauses before the first wave
        assert run["status"] == "paused" and run["pause_requested"] is True
        assert [p["kind"] for p in run["pending"]] == ["pause"]
        assert all(t["status"] == "pending" for t in run["tasks"].values())
        wf = (await a.client.get(f"/runs/{run_id}/workflow")).json()
        assert wf["attention"] == []  # the header shows the pause, not a node

        # a message sent while paused is applied when the run continues
        await say(a, run_id, "Keep functions short")
        resp = await a.client.post(f"/runs/{run_id}/pause", json={"paused": False})
        assert resp.status_code == 200 and resp.json()["pause_requested"] is False
        run = await a.settle(run_id)
        assert run["status"] == "awaiting_final_approval"
        assert "Keep functions short" in developer_prompts(a, "T1")[0]


async def test_pause_request_can_be_withdrawn_before_the_safe_point(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        run_id = await create(a)
        await a.approve(run_id)
        await a.client.post(f"/runs/{run_id}/pause", json={"paused": True})
        await a.client.post(f"/runs/{run_id}/pause", json={"paused": False})
        run = await a.approve(run_id)
        assert run["status"] == "awaiting_final_approval" and run["pause_requested"] is False


async def test_paused_run_can_be_resumed_from_its_interrupt_and_cancelled(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        run_id = await create(a)
        await a.approve(run_id)
        await a.client.post(f"/runs/{run_id}/pause", json={"paused": True})
        await a.approve(run_id)
        run = await a.approve(run_id)  # "Resume" on the pause interrupt itself
        assert run["status"] == "awaiting_final_approval" and run["pause_requested"] is False

    async with api(tmp_path / "b") as b:
        run_id = await create(b)
        await b.approve(run_id)
        await b.client.post(f"/runs/{run_id}/pause", json={"paused": True})
        await b.approve(run_id)
        assert (await b.client.post(f"/runs/{run_id}/cancel")).status_code == 200
        gone = await b.client.post(f"/runs/{run_id}/pause", json={"paused": False})
        assert gone.status_code == 409


def test_restart_recovers_preparing_and_checking_runs() -> None:
    assert {RunStatus.PREPARING, RunStatus.CHECKING} <= ACTIVE_STATUSES
    assert RunStatus.PAUSED not in ACTIVE_STATUSES  # it waits on an interrupt


# ------------------------------------------------------------------------------ Phase 14
async def test_pause_between_a_tasks_developer_iterations(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        store = a.harness.deps.steering
        reviews = {"n": 0}
        run: dict[str, Any] = {}

        def reviewer(call: Call) -> AIMessage:
            reviews["n"] += 1
            if reviews["n"] == 1:  # the human clicks Pause while T1 is being reviewed
                store._paused.add(run["id"])  # type: ignore[attr-defined]
                issue = {
                    "file": "app/schemas/todo.py",
                    "line": 1,
                    "severity": "major",
                    "message": "rename x",
                    "rule_ref": None,
                }
                return final(
                    {"decision": "changes_requested", "summary": "rename", "issues": [issue]}
                )
            return final({"decision": "approve", "summary": "ok", "issues": []})

        a.harness.brain.responders["reviewer"] = reviewer
        run_id = run["id"] = await create(a)
        await a.approve(run_id)
        detail = await a.approve(run_id)
        assert detail["status"] == "paused"
        [pending] = detail["pending"]
        assert pending["kind"] == "pause" and pending["data"]["task_id"] == "T1"
        assert detail["tasks"]["T1"]["status"] == "in_progress"
        wf = (await a.client.get(f"/runs/{run_id}/workflow")).json()
        t1 = next(n for n in wf["nodes"] if n["id"] == "task:T1")
        assert t1["status"] == "waiting" and t1["detail"].startswith("paused")
        assert wf["attention"] == []

        resp = await a.client.post(f"/runs/{run_id}/pause", json={"paused": False})
        assert resp.status_code == 200
        detail = await a.settle(run_id)
        assert detail["status"] == "awaiting_final_approval"
        assert detail["tasks"]["T1"]["iterations"] == 2


async def test_waiting_messages_can_be_edited_or_withdrawn(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        run_id = await create(a)
        first = await say(a, run_id, "Use camelCase")
        second = await say(a, run_id, "Add a README")
        edited = await a.client.patch(
            f"/runs/{run_id}/messages/{first['id']}", json={"text": "Use snake_case"}
        )
        assert edited.status_code == 200 and edited.json()["text"] == "Use snake_case"
        gone = await a.client.delete(f"/runs/{run_id}/messages/{second['id']}")
        assert gone.json()["status"] == "withdrawn"
        await a.approve(run_id)  # the Architect takes in the open messages
        architect = str(a.harness.brain.calls_for("architect")[0].messages[1].content)
        assert "Use snake_case" in architect
        assert "camelCase" not in architect and "README" not in architect
        late = await a.client.patch(
            f"/runs/{run_id}/messages/{first['id']}", json={"text": "too late"}
        )
        assert late.status_code == 409
        missing = await a.client.delete(f"/runs/{run_id}/messages/999")
        assert missing.status_code == 404


def merge_notes(call: Call) -> AIMessage:
    context = str(call.messages[1].content)
    if "Notes to merge" in context:
        return final({"notes": ["Use snake_case everywhere; log every request."]})
    return final(ESCALATE)


async def test_too_many_notes_are_merged_by_the_coordinator(tmp_path: Path) -> None:
    async with api(tmp_path, max_notes_chars=200) as a:
        a.harness.brain.responders["coordinator"] = merge_notes
        run_id = await create(a)
        await say(a, run_id, "Use snake_case for every field name. " * 3)
        await say(a, run_id, "Log every request with its duration. " * 3)
        run = await a.approve(run_id)
        assert run["human_notes"] == [
            {"id": None, "text": "Use snake_case everywhere; log every request.", "merged": True}
        ]
        architect = str(a.harness.brain.calls_for("architect")[0].messages[1].content)
        assert "Use snake_case everywhere; log every request." in architect


async def test_notes_keep_the_newest_when_merging_fails(tmp_path: Path) -> None:
    async with api(tmp_path, max_notes_chars=200) as a:
        run_id = await create(a)  # the default Coordinator answers nonsense for this
        await say(a, run_id, "A" * 150)
        await say(a, run_id, "B" * 150)
        run = await a.approve(run_id)
        assert [n["text"] for n in run["human_notes"]] == ["B" * 150]


async def test_messages_during_final_approval_join_the_rejection(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        run_id = await create(a)
        await a.approve(run_id)
        await a.approve(run_id)  # awaiting final approval
        await say(a, run_id, "Also return 404 for unknown ids")
        resp = await a.client.post(
            f"/runs/{run_id}/resume", json={"action": "reject", "feedback": "Add docstrings"}
        )
        assert resp.status_code == 202
        run = await a.settle(run_id)
        assert run["status"] == "awaiting_final_approval"
        task = next(t for t in run["plan"]["tasks"] if t["id"].startswith("F"))
        assert "Add docstrings" in task["description"]
        assert "Also return 404 for unknown ids" in task["description"]
        row = (await messages(a, run_id))["Also return 404 for unknown ids"]
        assert row["status"] == "applied" and "final-approval feedback" in row["reply"]
