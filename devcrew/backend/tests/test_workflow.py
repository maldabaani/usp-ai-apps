"""Workflow graph endpoint, client config, request size limit and the Architect's assessment."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

from app.graph.interrupts import ResumePayload
from tests.api_harness import Api, api
from tests.fakes import Call, final, tool_call, tool_results
from tests.graph_harness import DESIGN, current_task_id, default_developer

REQUEST_TEXT = "Build a TODO API with CRUD"
REQUEST = {"request": REQUEST_TEXT, "repo_target": "me/todo", "create_repo": False}


async def create(a: Api, body: dict[str, Any] = REQUEST) -> str:
    resp = await a.client.post("/runs", json=body)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def workflow(a: Api, run_id: str) -> dict[str, Any]:
    resp = await a.client.get(f"/runs/{run_id}/workflow")
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


def by_id(wf: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {n["id"]: n for n in wf["nodes"]}


def statuses(wf: dict[str, Any]) -> dict[str, str]:
    return {n["id"]: n["status"] for n in wf["nodes"]}


def edges(wf: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {(e["source"], e["target"], e["kind"]) for e in wf["edges"]}


async def test_workflow_follows_the_run_from_plan_to_completion(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        run_id = await create(a)
        await a.settle(run_id)

        wf = await workflow(a, run_id)
        s = statuses(wf)
        assert wf["status"] == "awaiting_plan_approval"
        assert s["requirements"] == "done" and s["planner"] == "done"
        assert s["approve_plan"] == "waiting" and wf["attention"] == ["approve_plan"]
        assert s["architect"] == "pending" and s["delivery"] == "pending"
        nodes = by_id(wf)
        assert nodes["approve_plan"]["pending_interrupt_ids"]
        assert nodes["requirements"]["detail"] == f"{len(REQUEST_TEXT)} characters"
        assert nodes["planner"]["runs"] == 1 and nodes["planner"]["started_at"]
        # the plan exists, so the development placeholder is replaced by one node per task
        assert "development" not in nodes
        assert nodes["task:T1"]["label"] == "T1 · Todo model"
        assert nodes["task:T1"]["status"] == "pending" and nodes["task:T1"]["detail"] == "planned"
        assert {
            ("scaffold", "task:T1", "flow"),
            ("task:T1", "task:T2", "flow"),
            ("task:T2", "integration", "flow"),
            ("planner", "approve_plan", "flow"),
        } <= edges(wf)
        assert ("scaffold", "task:T2", "flow") not in edges(wf)
        active = {(e["source"], e["target"]) for e in wf["edges"] if e["active"]}
        assert active == {("planner", "approve_plan")}

        await a.approve(run_id)
        s = statuses(await workflow(a, run_id))
        assert s["approve_plan"] == "done" and s["architect"] == "done"
        assert s["approve_design"] == "waiting"

        await a.approve(run_id)
        wf = await workflow(a, run_id)
        s = statuses(wf)
        assert s["scaffold"] == "done" and s["integration"] == "done"
        assert s["task:T1"] == s["task:T2"] == "done"
        assert s["approve_final"] == "waiting"
        t1 = by_id(wf)["task:T1"]
        assert t1["detail"] == "merged after 1 iteration" and t1["runs"] == 1
        assert t1["counters"]["iterations"] == 1
        assert any("write_file" in a["text"] for a in t1["activity"])
        assert any(a["text"].startswith("merged into") for a in t1["activity"])
        assert len(t1["activity"]) <= 8

        await a.approve(run_id)
        wf = await workflow(a, run_id)
        s = statuses(wf)
        assert wf["status"] == "completed" and not wf["attention"]
        assert s["approve_final"] == "done"
        # delivery disabled in tests: finished without a PR
        assert s["delivery"] == "skipped"
        assert by_id(wf)["delivery"]["detail"] == "finished without a PR"


async def test_plan_rejection_shows_a_loop_and_revisions(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        run_id = await create(a)
        await a.settle(run_id)
        resp = await a.client.post(
            f"/runs/{run_id}/resume", json={"action": "reject", "feedback": "Add due dates"}
        )
        assert resp.status_code == 202
        await a.settle(run_id)
        wf = await workflow(a, run_id)
        planner = by_id(wf)["planner"]
        assert planner["runs"] == 2 and planner["detail"] == "revised 1 times"
        assert ("approve_plan", "planner", "loop") in edges(wf)
        loop = next(e for e in wf["edges"] if e["kind"] == "loop")
        assert loop["label"] == "rejected 1x"


async def test_task_question_marks_the_task_waiting(tmp_path: Path) -> None:
    async with api(tmp_path) as a:

        def developer(call: Call) -> AIMessage:
            if current_task_id(call) == "T1":
                answers = [m for m in tool_results(call) if m.name == "ask_human"]
                if not answers:
                    return tool_call("ask_human", question="Ints or UUIDs?")
                if len(tool_results(call)) == 1:
                    return tool_call("write_file", path="app/schemas/todo.py", content="x = 1\n")
                return final("done")
            return default_developer(call)

        a.harness.brain.responders["developer"] = developer
        run_id = await create(a)
        await a.settle(run_id)
        await a.approve(run_id)
        await a.approve(run_id)
        wf = await workflow(a, run_id)
        assert wf["status"] == "needs_human"
        s = statuses(wf)
        assert s["task:T1"] == "waiting" and s["scaffold"] == "done"
        assert s["integration"] == "pending"
        assert wf["attention"] == ["task:T1"]
        t1 = by_id(wf)["task:T1"]
        assert t1["counters"]["questions"] == 1
        assert "waiting for your answer" in t1["detail"]
        assert any("Ints or UUIDs?" in a["text"] for a in t1["activity"])


async def test_workflow_of_unknown_run_is_404(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        assert (await a.client.get("/runs/nope/workflow")).status_code == 404


async def test_failed_planner_marks_the_stage_failed(tmp_path: Path) -> None:
    async with api(tmp_path, max_coordinator_actions=0) as a:

        def planner(call: Call) -> AIMessage:
            return AIMessage(content="not json")

        a.harness.brain.responders["planner"] = planner
        run_id = await create(a)
        await a.settle(run_id)
        wf = await workflow(a, run_id)
        # the planner escalates to the human after its retries
        assert statuses(wf)["planner"] == "waiting" and wf["attention"] == ["planner"]
        pending = by_id(wf)["planner"]["pending_interrupt_ids"]
        assert len(pending) == 1
        resp = await a.client.post(
            f"/runs/{run_id}/resume", json={"action": "reject", "feedback": "stop"}
        )
        assert resp.status_code == 202
        await a.settle(run_id)
        wf = await workflow(a, run_id)
        s = statuses(wf)
        assert wf["status"] == "failed" and s["planner"] == "failed"
        assert s["architect"] == "skipped" and s["delivery"] == "skipped"
        assert s["requirements"] == "done"


# ------------------------------------------------------------------------------ requests
async def test_request_size_limit_and_client_config(tmp_path: Path) -> None:
    async with api(tmp_path, max_request_chars=200) as a:
        config = (await a.client.get("/config")).json()
        assert config == {"max_request_chars": 200, "max_dev_iterations": 3, "max_parallel_devs": 2}
        too_long = {**REQUEST, "request": "# Requirements\n\n" + "x" * 300}
        resp = await a.client.post("/runs", json=too_long)
        assert resp.status_code == 422
        assert "the limit is 200" in resp.json()["detail"]
        ok = {**REQUEST, "request": "# Requirements\n\n- CRUD for todos\n- pytest tests\n"}
        run_id = await create(a, ok)
        run = await a.settle(run_id)
        assert run["request"].startswith("# Requirements")


# ------------------------------------------------------------------------------ assessment
async def test_architect_plan_assessment_reaches_the_design_gate(tmp_path: Path) -> None:
    assessment = {
        "concerns": ["T2 has no error handling story"],
        "assumptions": ["todos are stored in memory"],
        "suggested_changes": ["split T2 into read and write endpoints"],
    }
    async with api(tmp_path) as a:

        def architect(call: Call) -> AIMessage:
            if not tool_results(call):
                return tool_call("list_templates")
            return final({**DESIGN, "plan_assessment": assessment})

        a.harness.brain.responders["architect"] = architect
        run_id = await create(a)
        await a.settle(run_id)
        run = await a.approve(run_id)
        assert run["design"]["plan_assessment"] == assessment
        [pending] = run["pending"]
        assert pending["data"]["design"]["plan_assessment"] == assessment
        # the prompt asks for it; a design without it stays valid (older runs, weaker models)
        system = str(a.harness.brain.calls_for("architect")[0].messages[0].content)
        assert "plan_assessment" in system
        state = await a.harness.driver.state(run_id)
        assert state["plan"]["tasks"][1]["id"] == "T2"  # advisory only: the plan is unchanged
        run = await a.approve(run_id)
        assert run["status"] == "awaiting_final_approval"
        await a.harness.driver.resume(run_id, ResumePayload(action="approve"))
