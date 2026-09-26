"""Phase 5: parallel waves, worktrees, merge conflicts, Coordinator decisions, ask_agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from langchain_core.messages import AIMessage

from app.db.models import RunStatus
from app.events.types import EventType
from app.graph.backbone import ready_tasks
from app.graph.interrupts import ResumePayload
from app.graph.replan import apply_changes, replan_change, split_change
from app.graph.state import Plan, PlanTask, RevisedTask, TaskState, dump
from app.tools.git import GitRepo
from tests.fakes import Call, final, tool_call, tool_results
from tests.graph_harness import APPROVE, PLAN, Harness, make_harness

RUN = "run0005aaaabbbbccccdddd"


def task(tid: str, deps: list[str] | None = None, files: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": tid,
        "title": f"Task {tid}",
        "description": f"Implement {tid}",
        "target_files": files or [f"app/{tid.lower()}.py"],
        "depends_on": deps or [],
        "stack": "python",
        "story_ids": ["US1"],
    }


PAR_PLAN = {**PLAN, "tasks": [task("A"), task("B"), task("C"), task("D", ["A", "B"])]}


def tid_of(call: Call) -> str:
    return str(call.messages[1].content).split("id: ", 1)[1].split("\n", 1)[0]


def writing_developer(files: dict[str, str] | None = None) -> Any:
    """Writes one file per task (overridable), then finishes."""

    def respond(call: Call) -> AIMessage:
        tid = tid_of(call)
        if not tool_results(call):
            path = (files or {}).get(tid, f"app/{tid.lower()}.py")
            return tool_call("write_file", path=path, content=f"# written by {tid}\n")
        return final("done")

    return respond


async def start(h: Harness, plan: dict[str, Any] = PAR_PLAN) -> Any:
    h.brain.responders["planner"] = lambda c: final(plan)
    await h.driver.start(RUN, "Build it", "me/x", False)
    await h.driver.resume(RUN, APPROVE)
    return await h.driver.resume(RUN, APPROVE)


async def log(h: Harness, ref: str) -> list[str]:
    state = await h.driver.state(RUN)
    return (await GitRepo(Path(state["workspace"])).run("log", "--format=%s", ref)).splitlines()


# ------------------------------------------------------------------------------------ waves
async def test_waves_respect_parallel_limit_and_dependencies(tmp_path: Path) -> None:
    h = make_harness(tmp_path, max_parallel_devs=2)
    h.brain.delay = 0.02
    h.brain.responders["developer"] = writing_developer()
    outcome = await start(h)
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL
    tasks = (await h.driver.state(RUN))["tasks"]
    assert {t: (tasks[t]["wave"], tasks[t]["lane"]) for t in "ABCD"} == {
        "A": (1, 0),
        "B": (1, 1),
        "C": (2, 0),
        "D": (2, 1),
    }
    dispatches = [e.payload for e in await h.events(RUN) if e.payload.get("tool") == "dispatch"]
    assert [d["tasks"] for d in dispatches] == [["A", "B"], ["C", "D"]]
    assert dispatches[0]["waiting"] == ["C"]
    assert h.brain.peak["developer"] == 2  # two developers really overlapped
    subjects = await log(h, (await h.driver.state(RUN))["integration_branch"])
    assert sorted(subjects[:4]) == ["A: Task A", "B: Task B", "C: Task C", "D: Task D"]
    assert subjects.index("D: Task D") < min(
        subjects.index("A: Task A"), subjects.index("B: Task B")
    )


async def test_each_task_has_its_own_worktree_and_branch(tmp_path: Path) -> None:
    h = make_harness(tmp_path, max_parallel_devs=3)
    seen: dict[str, str] = {}

    def developer(call: Call) -> AIMessage:
        tid = tid_of(call)
        if not tool_results(call):
            return tool_call("list_dir", path=".")
        if len(tool_results(call)) == 1:
            seen[tid] = str(tool_results(call)[0].content)
            return tool_call("write_file", path=f"app/{tid.lower()}.py", content="x = 1\n")
        return final("done")

    h.brain.responders["developer"] = developer
    await start(h)
    # C ran in parallel with A and B and never saw their files (separate worktrees).
    assert "app/a.py" not in seen["C"] and "app/b.py" not in seen["C"]
    # D started after A and B merged, from the integration branch: it sees both.
    assert "app/a.py" in seen["D"] and "app/b.py" in seen["D"]
    state = await h.driver.state(RUN)
    main = Path(state["workspace"])
    assert [p.name for p in await GitRepo(main).worktrees()] == ["repo"]  # removed after merge
    branches = (await GitRepo(main).run("branch", "--format=%(refname:short)")).split()
    assert {f"devcrew/{RUN[:8]}/task-{t}" for t in "ABCD"} <= set(branches)
    assert {t["worktree"] for t in state["tasks"].values()} == {None}


# ------------------------------------------------------------------------------ conflicts
@pytest.mark.parametrize("first_attempt_leaves_markers", [False, True])
async def test_merge_conflict_is_resolved_by_developer_then_retested(
    tmp_path: Path, first_attempt_leaves_markers: bool
) -> None:
    h = make_harness(tmp_path, max_parallel_devs=2)
    plan = {
        **PLAN,
        "tasks": [task("A", files=["app/shared.py"]), task("B", files=["app/shared.py"])],
    }
    attempts: dict[str, int] = {}

    def developer(call: Call) -> AIMessage:
        tid = tid_of(call)
        text = call.text()
        if tool_results(call):
            return final("done")
        if "resolve the conflicts" in text or ("markers" in text and "still present" in text):
            attempts[tid] = attempts.get(tid, 0) + 1
            if first_attempt_leaves_markers and attempts[tid] == 1:
                return tool_call("read_file", path="app/shared.py")
            return tool_call(
                "write_file", path="app/shared.py", content="# written by A\n# written by B\n"
            )
        return tool_call("write_file", path="app/shared.py", content=f"# written by {tid}\n")

    h.brain.responders["developer"] = developer
    outcome = await start(h, plan)
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL
    state = await h.driver.state(RUN)
    loser = next(t for t in "AB" if state["tasks"][t]["conflict_rounds"] == 1)
    assert state["tasks"][loser]["status"] == "merged"
    assert attempts[loser] == (2 if first_attempt_leaves_markers else 1)
    main = Path(state["workspace"])
    assert (main / "app" / "shared.py").read_text() == "# written by A\n# written by B\n"
    qa_runs = [c for c in h.brain.calls_for("qa") if c.fresh and f"id: {loser}" in c.text()]
    assert len(qa_runs) == 2  # QA re-ran after the resolution
    decisions = [
        e.payload.get("action") for e in await h.events(RUN) if e.payload.get("tool") == "decision"
    ]
    assert "resolve_conflict" in decisions
    assert not any("<<<<<<<" in (main / p).read_text() for p in ["app/shared.py"])


async def test_repeated_conflicts_escalate(tmp_path: Path) -> None:
    h = make_harness(tmp_path, max_parallel_devs=2, max_conflict_rounds=0)
    plan = {**PLAN, "tasks": [task("A", files=["app/s.py"]), task("B", files=["app/s.py"])]}
    h.brain.responders["developer"] = writing_developer({"A": "app/s.py", "B": "app/s.py"})
    outcome = await start(h, plan)
    value = outcome.interrupts[0].value
    assert value["kind"] == "escalation" and value["data"]["kind"] == "merge_conflict"
    outcome = await h.driver.resume(RUN, ResumePayload(action="reject", feedback="drop it"))
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL


# ---------------------------------------------------------------------------- coordinator
def rejecting_reviewer(approve_when: str | None = None, only: str | None = None) -> Any:
    def respond(call: Call) -> AIMessage:
        if (approve_when and approve_when in call.text()) or (
            only and f"id: {only}\n" not in call.text()
        ):
            return final({"decision": "approve", "issues": []})
        return final(
            {
                "decision": "changes_requested",
                "issues": [
                    {"file": "app/a.py", "severity": "blocker", "message": "wrong approach"}
                ],
            }
        )

    return respond


async def test_iteration_limit_coordinator_replans(tmp_path: Path) -> None:
    h = make_harness(tmp_path, max_dev_iterations=2)
    h.brain.responders["developer"] = writing_developer()
    h.brain.responders["reviewer"] = rejecting_reviewer(approve_when="REVISED")
    h.brain.responders["coordinator"] = lambda c: final(
        {
            "action": "replan",
            "reason": "task was ambiguous",
            "guidance": "use a dict store",
            "revised_task": {
                "title": "Task A (REVISED)",
                "description": "REVISED: dict store",
                "target_files": ["app/a.py"],
            },
        }
    )
    plan = {**PLAN, "tasks": [task("A"), task("B", ["A"])]}
    outcome = await start(h, plan)
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL
    state = await h.driver.state(RUN)
    a = state["tasks"]["A"]
    assert a["status"] == "merged" and a["coordinator_actions"] == 1 and a["iterations"] == 1
    assert state["plan"]["tasks"][0]["title"] == "Task A (REVISED)"
    assert a["wave"] == 2  # re-dispatched after the replan
    coordinator_call = h.brain.calls_for("coordinator")[0]
    assert (
        "iteration_limit" in coordinator_call.text() and "wrong approach" in coordinator_call.text()
    )
    assert "replan, split, escalate" in coordinator_call.text()


async def test_iteration_limit_coordinator_splits_and_rewires(tmp_path: Path) -> None:
    h = make_harness(tmp_path, max_dev_iterations=1, max_parallel_devs=2)
    h.brain.responders["developer"] = writing_developer()
    h.brain.responders["reviewer"] = rejecting_reviewer(only="A")
    h.brain.responders["coordinator"] = lambda c: final(
        {
            "action": "split",
            "reason": "too big",
            "subtasks": [task("A-1"), task("A-2", ["A-1"])],
        }
    )
    plan = {**PLAN, "tasks": [task("A"), task("B", ["A"])]}
    outcome = await start(h, plan)
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL
    state = await h.driver.state(RUN)
    assert state["tasks"]["A"]["status"] == "split"
    assert {t: state["tasks"][t]["status"] for t in ("A-1", "A-2", "B")} == {
        "A-1": "merged",
        "A-2": "merged",
        "B": "merged",
    }
    plan_tasks = {t["id"]: t for t in state["plan"]["tasks"]}
    assert "A" not in plan_tasks and plan_tasks["B"]["depends_on"] == ["A-1", "A-2"]
    assert plan_tasks["A-1"]["story_ids"] == ["US1"]
    b_wave = state["tasks"]["B"]["wave"]
    assert b_wave > state["tasks"]["A-2"]["wave"] > state["tasks"]["A-1"]["wave"]


async def test_invalid_split_is_corrected_or_escalated(tmp_path: Path) -> None:
    h = make_harness(tmp_path, max_dev_iterations=1)
    h.brain.responders["developer"] = writing_developer()
    h.brain.responders["reviewer"] = rejecting_reviewer()
    h.brain.responders["coordinator"] = lambda c: final(
        {"action": "split", "reason": "x", "subtasks": [task("B"), task("A-2", ["Z"])]}
    )
    plan = {**PLAN, "tasks": [task("A"), task("B", ["A"])]}
    outcome = await start(h, plan)
    value = outcome.interrupts[0].value
    assert value["kind"] == "escalation" and value["data"]["task_id"] == "A"
    calls = h.brain.calls_for("coordinator")
    assert len(calls) == 3 and "clashing: ['B']" in calls[1].text()


async def test_coordinator_retry_after_agent_error(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    broken = {"n": 0}

    def developer(call: Call) -> AIMessage:
        if "Coordinator guidance: call write_file" not in call.text() and broken["n"] < 2:
            broken["n"] += 1
            return tool_call("nonexistent", x=1)
        return cast(AIMessage, writing_developer()(call))

    h.brain.responders["developer"] = developer
    h.brain.responders["coordinator"] = lambda c: final(
        {"action": "retry", "reason": "tool misuse", "guidance": "call write_file with path"}
    )
    outcome = await start(h, {**PLAN, "tasks": [task("A")]})
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL
    assert "retry, replan, escalate" in h.brain.calls_for("coordinator")[0].text()


async def test_coordinator_action_cap_escalates_without_llm(tmp_path: Path) -> None:
    h = make_harness(tmp_path, max_dev_iterations=1, max_coordinator_actions=1)
    h.brain.responders["developer"] = writing_developer()
    h.brain.responders["reviewer"] = rejecting_reviewer()
    h.brain.responders["coordinator"] = lambda c: final(
        {
            "action": "replan",
            "reason": "r",
            "guidance": "g",
            "revised_task": {"title": "A again", "description": "d", "target_files": ["app/a.py"]},
        }
    )
    outcome = await start(h, {**PLAN, "tasks": [task("A")]})
    assert outcome.interrupts[0].value["kind"] == "escalation"
    assert len(h.brain.calls_for("coordinator")) == 1


async def test_run_coordinator_retries_planner_with_guidance(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    replies = iter([final("not json"), final("still"), final("nope")])
    h.brain.responders["planner"] = lambda c: next(replies, final(PAR_PLAN))
    h.brain.responders["coordinator"] = lambda c: final(
        {"action": "retry", "reason": "format", "guidance": "Output only the JSON object."}
    )
    outcome = await h.driver.start(RUN, "Build it", "me/x", False)
    assert outcome.status is RunStatus.AWAITING_PLAN_APPROVAL
    assert "Output only the JSON object." in h.brain.calls_for("planner")[-1].text()


# ------------------------------------------------------------------------------ ask_agent
def asking_developer(question: str, target: str = "architect") -> Any:
    def respond(call: Call) -> AIMessage:
        results = tool_results(call)
        if tid_of(call) == "T1" and not results:
            return tool_call("ask_agent", target=target, question=question)
        if tid_of(call) == "T1" and len(results) == 1:
            return tool_call(
                "write_file", path="app/a.py", content=f"# answer: {results[0].content}\n"
            )
        return cast(AIMessage, writing_developer()(call))

    return respond


def answering_architect(answer: dict[str, Any]) -> Any:
    def respond(call: Call) -> AIMessage:
        assert "Answering a teammate's question" in str(call.messages[0].content)
        assert not call.has_tools  # one-shot, not the architect node
        return final(answer)

    return respond


async def test_ask_agent_one_shot_answer(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    await h.driver.start(RUN, "Build it", "me/x", False)
    await h.driver.resume(RUN, APPROVE)  # plan approved, design pending (architect node ran)
    architect_node_calls = len(h.brain.calls_for("architect"))
    h.brain.responders["architect"] = answering_architect({"answer": "Use int ids."})
    h.brain.responders["developer"] = asking_developer("Int or UUID ids?")
    outcome = await h.driver.resume(RUN, APPROVE)
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL
    one_shot = h.brain.calls_for("architect")[architect_node_calls:]
    assert len(one_shot) == 1 and "Int or UUID ids?" in one_shot[0].text()
    assert "Design contracts" in one_shot[0].text()
    state = await h.driver.state(RUN)
    entry = next(e for e in state["qa_log"] if e["target"] == "architect")
    assert (entry["asker"], entry["task_id"], entry["answer"]) == (
        "developer",
        "T1",
        "Use int ids.",
    )
    ws = Path(state["workspace"])
    assert "Use int ids." in (ws / "app" / "a.py").read_text()
    events = [e for e in await h.events(RUN) if e.type in (EventType.QUESTION, EventType.ANSWER)]
    assert [(e.type, e.payload.get("target") or e.node) for e in events] == [
        (EventType.QUESTION, "architect"),
        (EventType.ANSWER, "architect"),
    ]


async def test_ask_agent_routes_to_human(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    await h.driver.start(RUN, "Build it", "me/x", False)
    await h.driver.resume(RUN, APPROVE)
    h.brain.responders["architect"] = answering_architect(
        {"answer": "Probably soft delete.", "needs_human": True, "reason": "product decision"}
    )
    h.brain.responders["developer"] = asking_developer("Soft or hard delete?")
    outcome = await h.driver.resume(RUN, APPROVE)
    value = outcome.interrupts[0].value
    assert value["kind"] == "question" and value["data"]["task_id"] == "T1"
    assert "architect could not settle this" in value["data"]["question"]
    assert "Soft or hard delete?" in value["data"]["question"]
    outcome = await h.driver.resume(RUN, ResumePayload(action="answer", answer="Hard delete."))
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL
    state = await h.driver.state(RUN)
    assert [(e["target"], e["answer"]) for e in state["qa_log"]] == [
        ("architect", "Probably soft delete."),
        ("human", "Hard delete."),
    ]
    assert "Hard delete." in (Path(state["workspace"]) / "app" / "a.py").read_text()


async def test_question_limit_is_shared_by_ask_agent_and_ask_human(tmp_path: Path) -> None:
    h = make_harness(tmp_path, max_questions_per_task=1)
    await h.driver.start(RUN, "Build it", "me/x", False)
    await h.driver.resume(RUN, APPROVE)
    h.brain.responders["architect"] = answering_architect({"answer": "ok"})
    replies: list[str] = []

    def developer(call: Call) -> AIMessage:
        results = tool_results(call)
        if tid_of(call) != "T1":
            return cast(AIMessage, writing_developer()(call))
        if not results:
            return tool_call("ask_agent", target="architect", question="First question?")
        if len(results) == 1:
            return tool_call("ask_human", question="Second question?")
        replies.append(str(results[-1].content))
        return (
            tool_call("write_file", path="app/x.py", content="x\n")
            if len(results) == 2
            else final("d")
        )

    h.brain.responders["developer"] = developer
    outcome = await h.driver.resume(RUN, APPROVE)
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL  # never interrupted
    assert "limit reached" in replies[0]


# --------------------------------------------------------------- parallel human questions
async def test_parallel_questions_resume_independently_and_survive_restart(tmp_path: Path) -> None:
    h = make_harness(tmp_path, max_parallel_devs=2)

    def developer(call: Call) -> AIMessage:
        tid = tid_of(call)
        results = tool_results(call)
        if tid in "AB" and not results:
            return tool_call("ask_human", question=f"Question from {tid}?")
        if tid in "AB" and len(results) == 1:
            return tool_call(
                "write_file", path=f"app/{tid.lower()}.py", content=f"# {results[0].content}\n"
            )
        return cast(AIMessage, writing_developer()(call))

    h.brain.responders["developer"] = developer
    outcome = await start(h, {**PLAN, "tasks": [task("A"), task("B"), task("C", ["A", "B"])]})
    assert outcome.status is RunStatus.NEEDS_HUMAN
    pending = {p.value["data"]["task_id"]: p.id for p in outcome.interrupts}
    assert set(pending) == {"A", "B"}
    with pytest.raises(ValueError, match="interrupt_id"):
        await h.driver.resume(RUN, ResumePayload(action="answer", answer="x"))

    outcome = await h.driver.resume(
        RUN, ResumePayload(action="answer", answer="answer A"), pending["A"]
    )
    assert [p.value["data"]["task_id"] for p in outcome.interrupts] == ["B"]

    restarted = h.rebuild()  # backend restart while B's question is pending
    waiting = await restarted.driver.pending_interrupts(RUN)
    assert [p.value["data"]["task_id"] for p in waiting] == ["B"]
    outcome = await restarted.driver.resume(
        RUN, ResumePayload(action="answer", answer="answer B"), waiting[0].id
    )
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL
    state = await restarted.driver.state(RUN)
    ws = Path(state["workspace"])
    assert (ws / "app" / "a.py").read_text() == "# answer A\n"
    assert (ws / "app" / "b.py").read_text() == "# answer B\n"
    assert state["tasks"]["C"]["status"] == "merged"


# ---------------------------------------------------------------------------- unit: plan
def test_ready_tasks_blocks_dependents_of_failed_tasks() -> None:
    plan = Plan.model_validate(PAR_PLAN)
    tasks = {t.id: dump(TaskState(id=t.id)) for t in plan.tasks}
    tasks["A"] = dump(TaskState(id="A", status="failed"))
    tasks["B"] = dump(TaskState(id="B", status="merged"))
    ready, updates = ready_tasks({"plan": dump(plan), "tasks": tasks})
    assert ready == ["C"] and updates["D"]["status"] == "blocked"


def test_apply_changes_replan_split_and_invalid() -> None:
    plan = Plan.model_validate(PAR_PLAN)
    tasks = {t.id: dump(TaskState(id=t.id, iterations=3)) for t in plan.tasks}
    revised = RevisedTask(title="A2", description="d", target_files=["app/a2.py"])
    subtasks = [PlanTask.model_validate(task("B-1")), PlanTask.model_validate(task("B-2", ["B-1"]))]
    new_plan, updates, errors = apply_changes(
        plan, tasks, [replan_change("A", revised, "g"), split_change("B", subtasks)]
    )
    assert errors == []
    assert new_plan.task("A").title == "A2" and new_plan.task("D").depends_on == ["A", "B-1", "B-2"]
    assert updates["A"]["status"] == "pending" and updates["A"]["reset_branch"] is True
    assert updates["A"]["iterations"] == 0 and updates["B"]["status"] == "split"
    assert updates["B-1"]["status"] == "pending"

    cyclic = [
        PlanTask.model_validate(task("C-1", ["C-2"])),
        PlanTask.model_validate(task("C-2", ["C-1"])),
    ]
    _, updates, errors = apply_changes(plan, tasks, [split_change("C", cyclic)])
    assert updates["C"]["status"] == "failed" and "cycle" in errors[0]
