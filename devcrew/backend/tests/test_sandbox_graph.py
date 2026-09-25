"""Graph behavior with a (fake) sandbox runner: install step, test loops, cleanup, mixed stacks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

from app.db.models import RunStatus
from tests.fakes import Call, FakeRunner, final, tool_call, tool_results
from tests.graph_harness import APPROVE, DESIGN, PLAN, Harness, default_developer, make_harness

RUN = "run0002aaaabbbbccccdddd"
PYTEST = "pytest -q"
NG_TEST = "npx ng test --watch=false --browsers=ChromeHeadless"


async def run_to_final(h: Harness) -> Any:
    await h.driver.start(RUN, "Build a TODO API", "me/todo", False)
    await h.driver.resume(RUN, APPROVE)
    return await h.driver.resume(RUN, APPROVE)


async def test_install_then_offline_tests_then_cleanup(tmp_path: Path) -> None:
    runner = FakeRunner()
    h = make_harness(tmp_path, runner=runner)
    outcome = await run_to_final(h)
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL
    tests = outcome.interrupts[0].value["data"]["integration"]["tests"]
    assert tests["python"]["ran"] is True and tests["python"]["passed"] is True

    # One network install at scaffold; manifests never changed, so no reinstall.
    assert runner.commands(network=True) == [(None, "pip install -e .", ".")]
    assert runner.commands(network=False) == [
        ("T1", PYTEST, "."),
        ("T2", PYTEST, "."),
        (None, PYTEST, "."),
    ]
    assert {image for *_, image in runner.calls} == {"python"}
    assert runner.released == ["T1", "T2"]

    outcome = await h.approve_until_done(await h.driver.resume(RUN, APPROVE), RUN)
    assert outcome.status is RunStatus.COMPLETED
    assert runner.cleaned == [RUN]


async def test_failing_tests_loop_back_with_qa_diagnosis(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.script(PYTEST, (1, "FAILED tests/test_t1.py::test_x - assert 1 == 2\n1 failed"))
    h = make_harness(tmp_path, runner=runner)
    h.brain.responders["qa_report"] = lambda c: final(
        {"failed": ["tests/test_t1.py::test_x"], "summary": "create() returns 1, expected 2"}
    )
    outcome = await run_to_final(h)
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL
    state = await h.driver.state(RUN)
    t1 = state["tasks"]["T1"]
    assert t1["iterations"] == 2 and t1["test_results"]["passed"] is True
    retry = [c for c in h.brain.calls_for("developer") if c.fresh][1]
    assert "tests/test_t1.py::test_x" in retry.text()
    assert "create() returns 1, expected 2" in retry.text()
    assert "exit code 1" in retry.text()


async def test_exit_code_decides_pass_not_the_model(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.script(PYTEST, (2, "ERROR collecting tests"), (2, "ERROR"), (2, "ERROR"))
    h = make_harness(tmp_path, runner=runner, max_dev_iterations=3)
    h.brain.responders["qa_report"] = lambda c: final({"failed": [], "summary": "all good!"})
    outcome = await run_to_final(h)
    assert outcome.interrupts[0].value["kind"] == "escalation"
    assert (await h.driver.state(RUN))["tasks"]["T1"]["test_results"]["passed"] is False


async def test_manifest_change_triggers_reinstall(tmp_path: Path) -> None:
    runner = FakeRunner()
    h = make_harness(tmp_path, runner=runner)

    def developer(call: Call) -> AIMessage:
        if "id: T2" in str(call.messages[1].content) and not tool_results(call):
            return tool_call(
                "write_file", path="pyproject.toml", content="[project]\nname='app'\n# +dep\n"
            )
        return default_developer(call)

    h.brain.responders["developer"] = developer
    await run_to_final(h)
    installs = runner.commands(network=True)
    assert installs == [(None, "pip install -e .", "."), ("T2", "pip install -e .", ".")]


async def test_run_command_tool_and_policy(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.script("pytest -q tests", (1, "1 failed"))
    h = make_harness(tmp_path, runner=runner)
    outputs: list[str] = []

    def developer(call: Call) -> AIMessage:
        results = tool_results(call)
        if "id: T1" not in str(call.messages[1].content):
            return default_developer(call)
        if not results:
            return tool_call("run_command", command="sudo rm -rf build")
        if len(results) == 1:
            outputs.append(str(results[0].content))
            return tool_call("run_command", command="pytest -q tests", cwd="app")
        if len(results) == 2:
            outputs.append(str(results[1].content))
            return tool_call("write_file", path="app/schemas/todo.py", content="X = 1\n")
        return final("done")

    h.brain.responders["developer"] = developer
    await run_to_final(h)
    assert "rejected by sandbox policy" in outputs[0] and "sudo" in outputs[0]
    assert outputs[1].startswith("exit_code=1") and "1 failed" in outputs[1]
    assert ("T1", "pytest -q tests", "app") in runner.commands(network=False)
    assert not any("sudo" in cmd for _, cmd, _ in runner.commands())
    tools = {c.role: set(c.tool_names) for c in h.brain.calls if c.has_tools}
    assert "run_command" in tools["developer"] and "run_command" in tools["qa"]
    assert "run_command" not in tools["reviewer"]


async def test_mixed_stack_layout(tmp_path: Path) -> None:
    runner = FakeRunner()
    h = make_harness(tmp_path, runner=runner)
    plan = {
        **PLAN,
        "tasks": [
            {**PLAN["tasks"][0], "target_files": ["backend/app/schemas/todo.py"]},
            {
                **PLAN["tasks"][1],
                "id": "T2",
                "stack": "angular",
                "depends_on": [],
                "title": "Todo list UI",
                "target_files": ["frontend/src/app/todo.ts"],
            },
        ],
    }
    design = {
        **DESIGN,
        "stack": "mixed",
        "components": [
            {"template_id": "python-fastapi", "path": "backend"},
            {"template_id": "angular-standalone", "path": "frontend"},
        ],
    }

    def architect(call: Call) -> AIMessage:
        return final(design)

    h.brain.responders["planner"] = lambda c: final(plan)
    h.brain.responders["architect"] = architect
    outcome = await run_to_final(h)
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL, outcome

    ws = Path((await h.driver.state(RUN))["workspace"])
    assert (ws / "backend" / "app" / "main.py").is_file()
    assert (ws / "frontend" / "src" / "main.ts").is_file()
    assert not (ws / "backend" / "template.yaml").exists()
    assert sorted(runner.commands(network=True)) == [
        (None, "npm install", "frontend"),
        (None, "pip install -e .", "backend"),
    ]
    offline = runner.commands(network=False)
    assert ("T1", PYTEST, "backend") in offline and ("T2", NG_TEST, "frontend") in offline
    assert {image for *_, image in runner.calls} == {"mixed"}
    tests = outcome.interrupts[0].value["data"]["integration"]["tests"]
    assert set(tests) == {"python", "angular"}


async def test_design_missing_task_stack_is_corrected(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    plan = {**PLAN, "tasks": [{**PLAN["tasks"][0], "stack": "angular"}]}
    replies = iter([final(DESIGN)])
    h.brain.responders["planner"] = lambda c: final(plan)
    h.brain.responders["architect"] = lambda c: next(
        replies,
        final({**DESIGN, "stack": "angular", "template_id": "angular-standalone"}),
    )
    await h.driver.start(RUN, "Build a TODO UI", "me/todo", False)
    outcome = await h.driver.resume(RUN, APPROVE)
    assert outcome.status is RunStatus.AWAITING_DESIGN_APPROVAL
    retry = h.brain.calls_for("architect")[-1]
    assert "['angular'] tasks" in retry.text()
