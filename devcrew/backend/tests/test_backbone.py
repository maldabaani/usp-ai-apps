from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

from app.db.models import RunStatus
from app.events.types import EventType
from app.graph.interrupts import ResumePayload
from tests.fakes import Call, final, tool_call, tool_results
from tests.graph_harness import APPROVE, PLAN, Harness, default_developer, make_harness

RUN = "run0001aaaabbbbccccdddd"


def git(ws: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=ws, capture_output=True, text=True, check=True).stdout


async def start(h: Harness) -> None:
    outcome = await h.driver.start(RUN, "Build a TODO API", "me/todo", False)
    assert outcome.kind == "interrupted"


async def test_happy_path_sequential(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    outcome = await h.driver.start(RUN, "Build a TODO API", "me/todo", False)
    assert outcome.status is RunStatus.AWAITING_PLAN_APPROVAL
    assert outcome.interrupts[0].value["artifact"] == "plan"
    assert outcome.interrupts[0].value["data"]["layers"] == [["T1"], ["T2"]]

    outcome = await h.driver.resume(RUN, APPROVE)
    assert outcome.status is RunStatus.AWAITING_DESIGN_APPROVAL
    outcome = await h.driver.resume(RUN, APPROVE)
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL
    summary = outcome.interrupts[0].value["data"]["integration"]
    assert summary["merged"] == ["T1", "T2"]
    assert summary["tests"]["python"]["ran"] is False  # no sandbox until Phase 3

    outcome = await h.driver.resume(RUN, APPROVE)
    assert outcome.kind == "finished" and outcome.status is RunStatus.COMPLETED

    state = await h.driver.state(RUN)
    assert {t["status"] for t in state["tasks"].values()} == {"merged"}
    ws = Path(state["workspace"])
    branch = state["integration_branch"]
    assert branch.startswith(f"devcrew/{RUN}-build-a-todo-api")
    # One squashed commit per task, in dependency order, on top of scaffold + design doc.
    log = git(ws, "log", "--format=%s", branch).splitlines()
    assert log == [
        "T2: Todo router",
        "T1: Todo model",
        "Add design document",
        "Scaffold from template python-fastapi",
    ]
    assert git(ws, "log", "--format=%s", "main").splitlines() == [
        "Scaffold from template python-fastapi"
    ]
    files = git(ws, "ls-tree", "-r", "--name-only", branch).split()
    assert {"app/main.py", "app/routers/todos.py", "tests/test_t1.py", "docs/design.md"} <= set(
        files
    )
    assert "template.yaml" not in files

    events = await h.events(RUN)
    types = [e.type for e in events]
    assert types.count(EventType.MERGE) == 2
    assert EventType.TOOL_CALL in types and EventType.AWAITING_INPUT in types
    started = [e.node for e in events if e.type is EventType.NODE_STARTED]
    assert started.index("planner") < started.index("architect") < started.index("scaffold")
    statuses = [e.payload["status"] for e in events if e.type is EventType.STATUS]
    assert statuses[-1] == "completed" and "executing" in statuses


async def test_reject_plan_feeds_back_to_planner(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    await start(h)
    outcome = await h.driver.resume(
        RUN,
        ResumePayload(action="reject", feedback="Add a priority field"),
    )
    assert outcome.status is RunStatus.AWAITING_PLAN_APPROVAL
    second = h.brain.calls_for("planner")[-1]
    assert "Add a priority field" in second.text() and "Previous plan" in second.text()


async def test_edit_plan_is_validated(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    await start(h)
    bad = {**PLAN, "tasks": [{**PLAN["tasks"][0], "depends_on": ["T9"]}]}
    outcome = await h.driver.resume(
        RUN,
        ResumePayload(action="edit", artifact=bad),
    )
    assert outcome.status is RunStatus.AWAITING_PLAN_APPROVAL
    assert "unknown tasks" in outcome.interrupts[0].value["error"]

    good = {**PLAN, "tasks": [PLAN["tasks"][0]]}
    outcome = await h.driver.resume(
        RUN,
        ResumePayload(action="edit", artifact=good),
    )
    assert outcome.status is RunStatus.AWAITING_DESIGN_APPROVAL
    assert [t["id"] for t in (await h.driver.state(RUN))["plan"]["tasks"]] == ["T1"]


async def test_disallowed_action_reinterrupts(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    await start(h)
    outcome = await h.driver.resume(
        RUN,
        ResumePayload(action="answer", answer="hello"),
    )
    assert outcome.status is RunStatus.AWAITING_PLAN_APPROVAL
    assert "not allowed" in outcome.interrupts[0].value["error"]


async def test_planner_asks_human_and_resumes_conversation(tmp_path: Path) -> None:
    h = make_harness(tmp_path)

    def planner(call: Call) -> AIMessage:
        answers = tool_results(call)
        if not answers:
            return tool_call("ask_human", question="Should todos have due dates?")
        assert answers[-1].content == "Yes, optional"
        return final(PLAN)

    h.brain.responders["planner"] = planner
    outcome = await h.driver.start(RUN, "Build a TODO API", "me/todo", False)
    assert outcome.status is RunStatus.NEEDS_HUMAN
    value = outcome.interrupts[0].value
    assert value["kind"] == "question" and value["data"]["question"].startswith("Should")

    outcome = await h.driver.resume(
        RUN,
        ResumePayload(action="answer", answer="Yes, optional"),
    )
    assert outcome.status is RunStatus.AWAITING_PLAN_APPROVAL
    state = await h.driver.state(RUN)
    assert state["qa_log"][0]["answer"] == "Yes, optional"
    assert state["scratch"] == {}
    types = [e.type for e in await h.events(RUN)]
    assert EventType.QUESTION in types and EventType.ANSWER in types


async def test_question_limit_stops_asking(tmp_path: Path) -> None:
    h = make_harness(tmp_path, max_questions_per_task=0)

    def planner(call: Call) -> AIMessage:
        answers = tool_results(call)
        if not answers:
            return tool_call("ask_human", question="Anything else to know?")
        assert "limit reached" in str(answers[-1].content)
        return final(PLAN)

    h.brain.responders["planner"] = planner
    outcome = await h.driver.start(RUN, "Build a TODO API", "me/todo", False)
    assert outcome.status is RunStatus.AWAITING_PLAN_APPROVAL


async def test_review_rejection_loops_back_to_developer(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    reviews: Iterator[dict[str, Any]] = iter(
        [
            {
                "decision": "changes_requested",
                "summary": "fix",
                "issues": [
                    {
                        "file": "app/schemas/todo.py",
                        "line": 1,
                        "severity": "major",
                        "message": "Missing type hints",
                        "rule_ref": "PY-001",
                    }
                ],
            },
        ]
    )
    h.brain.responders["reviewer"] = lambda c: final(
        next(reviews, {"decision": "approve", "summary": "ok", "issues": []})
    )
    await start(h)
    await h.driver.resume(RUN, APPROVE)
    outcome = await h.driver.resume(RUN, APPROVE)
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL
    state = await h.driver.state(RUN)
    assert state["tasks"]["T1"]["iterations"] == 2
    assert state["tasks"]["T2"]["iterations"] == 1
    retry = [c for c in h.brain.calls_for("developer") if c.fresh][1]
    assert "[PY-001]" in retry.text() and "Missing type hints" in retry.text()


async def test_unknown_rule_ref_is_corrected_by_validator(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    replies: Iterator[dict[str, Any]] = iter(
        [
            {
                "decision": "changes_requested",
                "issues": [
                    {"file": "a.py", "severity": "major", "message": "m", "rule_ref": "PY-999"}
                ],
            },
            {"decision": "approve", "issues": []},
        ]
    )
    h.brain.responders["reviewer"] = lambda c: final(next(replies, {"decision": "approve"}))
    await start(h)
    await h.driver.resume(RUN, APPROVE)
    await h.driver.resume(RUN, APPROVE)
    retry = h.brain.calls_for("reviewer")[1]
    assert "PY-999" in retry.text() and "unknown rule_ref" in retry.text()


async def test_iteration_limit_escalates_and_blocks_dependents(tmp_path: Path) -> None:
    h = make_harness(tmp_path, max_dev_iterations=2)
    h.brain.responders["reviewer"] = lambda c: final(
        {
            "decision": "changes_requested",
            "issues": [{"file": "app/schemas/todo.py", "severity": "blocker", "message": "wrong"}],
        }
    )
    await start(h)
    await h.driver.resume(RUN, APPROVE)
    outcome = await h.driver.resume(RUN, APPROVE)
    assert outcome.status is RunStatus.NEEDS_HUMAN
    value = outcome.interrupts[0].value
    assert value["kind"] == "escalation" and value["data"]["task_id"] == "T1"
    assert "MAX_DEV_ITERATIONS (2)" in value["data"]["reason"]
    assert len([c for c in h.brain.calls_for("developer") if c.fresh]) == 2
    assert (await h.driver.state(RUN))["tasks"]["T1"]["status"] == "needs_human"

    outcome = await h.driver.resume(
        RUN,
        ResumePayload(action="reject", feedback="give up"),
    )
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL
    summary = outcome.interrupts[0].value["data"]["integration"]
    assert summary["failed"] == ["T1"] and summary["blocked"] == ["T2"]


async def test_escalation_answer_retries_with_guidance(tmp_path: Path) -> None:
    h = make_harness(tmp_path, max_dev_iterations=1)
    reviews: Iterator[dict[str, Any]] = iter(
        [
            {
                "decision": "changes_requested",
                "issues": [{"file": "x.py", "severity": "blocker", "message": "wrong"}],
            }
        ]
    )
    h.brain.responders["reviewer"] = lambda c: final(next(reviews, {"decision": "approve"}))
    await start(h)
    await h.driver.resume(RUN, APPROVE)
    outcome = await h.driver.resume(RUN, APPROVE)
    assert outcome.interrupts[0].value["kind"] == "escalation"
    outcome = await h.driver.resume(
        RUN,
        ResumePayload(action="answer", answer="Use a dataclass"),
    )
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL
    last_t1 = [c for c in h.brain.calls_for("developer") if c.fresh][1]
    assert "Human guidance: Use a dataclass" in last_t1.text()
    state = await h.driver.state(RUN)
    assert state["qa_log"][-1]["asker"] == "coordinator"


async def test_malformed_tool_calls_route_to_coordinator(tmp_path: Path) -> None:
    h = make_harness(tmp_path)

    def developer(call: Call) -> AIMessage:
        return AIMessage(
            content="",
            invalid_tool_calls=[
                {
                    "name": "write_file",
                    "args": "{bad",
                    "id": "x",
                    "error": "bad JSON",
                    "type": "invalid_tool_call",
                }
            ],
        )

    h.brain.responders["developer"] = developer
    await start(h)
    await h.driver.resume(RUN, APPROVE)
    outcome = await h.driver.resume(RUN, APPROVE)
    assert outcome.interrupts[0].value["kind"] == "escalation"
    assert "malformed tool call" in outcome.interrupts[0].value["data"]["reason"]
    assert len(h.brain.calls_for("developer")) == 2  # original + one corrective retry
    errors = [e for e in await h.events(RUN) if e.type is EventType.ERROR]
    assert len(errors) == 1 and errors[0].node == "developer" and errors[0].task_id == "T1"
    assert "malformed" in errors[0].payload["message"]


async def test_final_rejection_adds_followup_task(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    await start(h)
    await h.driver.resume(RUN, APPROVE)
    await h.driver.resume(RUN, APPROVE)
    outcome = await h.driver.resume(
        RUN,
        ResumePayload(action="reject", feedback="Add a README"),
    )
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL
    state = await h.driver.state(RUN)
    assert state["tasks"]["FIX1"]["status"] == "merged"
    fix_call = next(c for c in h.brain.calls_for("developer") if "FIX1" in c.text())
    assert "Add a README" in fix_call.text()


async def test_restart_resumes_from_checkpoint(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    await start(h)
    await h.driver.resume(RUN, APPROVE)  # now awaiting design approval

    restarted = h.rebuild()
    pending = await restarted.driver.pending_interrupts(RUN)
    assert pending[0].value["artifact"] == "design"
    outcome = await restarted.approve_until_done(await restarted.driver.resume(RUN, APPROVE), RUN)
    assert outcome.status is RunStatus.COMPLETED
    assert len(h.brain.calls_for("planner")) == 1  # nothing before the checkpoint re-ran


async def test_structured_output_failure_escalates(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    h.brain.responders["planner"] = lambda c: final("I cannot produce JSON, sorry.")
    outcome = await h.driver.start(RUN, "Build a TODO API", "me/todo", False)
    assert outcome.status is RunStatus.NEEDS_HUMAN
    value = outcome.interrupts[0].value
    assert value["kind"] == "escalation" and "Plan" in value["data"]["reason"]
    assert len(h.brain.calls_for("planner")) == 3  # final answer + 2 validator retries

    h.brain.responders["planner"] = lambda c: final(PLAN)
    outcome = await h.driver.resume(
        RUN,
        ResumePayload(action="answer", answer="Keep it small"),
    )
    assert outcome.status is RunStatus.AWAITING_PLAN_APPROVAL
    assert "Keep it small" in h.brain.calls_for("planner")[-1].text()

    h.brain.responders["planner"] = lambda c: final("still no")
    await h.driver.resume(RUN, ResumePayload(action="reject", feedback="redo"))
    outcome = await h.driver.resume(
        RUN,
        ResumePayload(action="reject", feedback="abort"),
    )
    assert outcome.kind == "finished" and outcome.status is RunStatus.FAILED


async def test_each_role_gets_only_its_tools(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    qa_attempts: list[str] = []

    def qa(call: Call) -> AIMessage:
        results = tool_results(call)
        if not results:
            return tool_call("write_file", path="app/main.py", content="hacked")
        qa_attempts.append(str(results[-1].content))
        if len(results) == 1:
            return tool_call("write_file", path="tests/test_ok.py", content="def test(): pass\n")
        return final("done")

    h.brain.responders["qa"] = qa
    await start(h)
    await h.driver.resume(RUN, APPROVE)
    await h.driver.resume(RUN, APPROVE)
    tools = {c.role: set(c.tool_names) for c in h.brain.calls if c.has_tools}
    assert tools == {
        "planner": {"ask_human"},
        "architect": {"list_templates", "read_rules", "ask_human"},
        "developer": {"read_file", "write_file", "list_dir", "read_rules", "ask_human"},
        "reviewer": {"read_file", "git_diff", "read_rules"},
        "qa": {"read_file", "write_file"},
    }
    assert "not allowed" in qa_attempts[0]
    state = await h.driver.state(RUN)
    assert (Path(state["workspace"]) / "app" / "main.py").read_text() == "app = None\n"
    # Structured calls never bind tools and use Ollama JSON-schema format.
    structured = [c for c in h.brain.calls if not c.has_tools]
    assert all(isinstance(c.kwargs.get("format"), dict) for c in structured)


async def test_scaffold_with_empty_template_still_creates_main(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    (h.deps.settings.templates_dir / "python" / "app" / "main.py").unlink()
    (h.deps.settings.templates_dir / "python" / "app").rmdir()
    await start(h)
    outcome = await h.approve_until_done(await h.driver.resume(RUN, APPROVE), RUN)
    assert outcome.status is RunStatus.COMPLETED
    ws = Path((await h.driver.state(RUN))["workspace"])
    assert git(ws, "log", "--format=%s", "main").split("\n")[0] == (
        "Scaffold from template python-fastapi"
    )


async def test_developer_question_inside_task_subgraph(tmp_path: Path) -> None:
    h = make_harness(tmp_path)

    def developer(call: Call) -> AIMessage:
        if "id: T1" in str(call.messages[1].content):
            answers = [m for m in tool_results(call) if m.name == "ask_human"]
            if not answers:
                return tool_call("ask_human", question="Should ids be UUIDs or ints?")
            if len(tool_results(call)) == 1:
                assert answers[0].content == "ints"
                return tool_call("write_file", path="app/schemas/todo.py", content="id: int\n")
            return final("done")
        return default_developer(call)

    h.brain.responders["developer"] = developer
    await start(h)
    await h.driver.resume(RUN, APPROVE)
    outcome = await h.driver.resume(RUN, APPROVE)
    assert outcome.status is RunStatus.NEEDS_HUMAN
    value = outcome.interrupts[0].value
    assert value["kind"] == "question" and value["data"]["task_id"] == "T1"
    assert value["data"]["role"] == "developer"
    live = await h.driver.state(RUN)
    assert live["tasks"]["T1"]["status"] == "in_progress"

    outcome = await h.driver.resume(RUN, ResumePayload(action="answer", answer="ints"))
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL
    state = await h.driver.state(RUN)
    assert [(e["task_id"], e["answer"]) for e in state["qa_log"]] == [("T1", "ints")]
    ws = Path(state["workspace"])
    assert git(ws, "show", f"{state['integration_branch']}:app/schemas/todo.py") == "id: int\n"
