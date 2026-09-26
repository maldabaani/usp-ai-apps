"""Benchmark: task files, output parsing, auto policy and headless runs (models mocked)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from app.benchmark.results import (
    BenchmarkReport,
    ConfigResult,
    ConfigSummary,
    HiddenTestResult,
    SuiteCounts,
    TaskResult,
    parse_test_output,
    write_report,
)
from app.benchmark.runner import AUTO_ANSWER, AutoPolicy, BenchmarkRunner
from app.benchmark.tasks import (
    BenchmarkTask,
    BenchStack,
    TaskFileError,
    count_test_cases,
    load_tasks,
)
from app.config import DEVCREW_DIR
from app.graph.interrupts import ResumeAction
from app.sandbox.policy import check_command
from tests.fake_github import FakeGitHub
from tests.fakes import Call, FakeRunner, final, tool_call, tool_results
from tests.graph_harness import current_task_id, default_developer, make_harness

BENCH = DEVCREW_DIR / "benchmarks"


# ------------------------------------------------------------------------------ task files
def test_shipped_tasks_cover_every_stack_with_four_levels() -> None:
    tasks = load_tasks(BENCH / "tasks", BENCH / "hidden_tests")
    assert len(tasks) == 12
    for stack in BenchStack:
        mine = [t for t in tasks if t.stack is stack]
        assert sorted(t.difficulty for t in mine) == [1, 2, 3, 4]
        assert all(t.expected_tests >= 5 for t in mine)
    assert {t.id for t in tasks} >= {"py-todo-crud", "java-shop-orders", "ng-shopping-cart"}


def test_hidden_suites_are_counted_per_stack() -> None:
    assert count_test_cases(BenchStack.PYTHON, BENCH / "hidden_tests" / "py-todo-crud") == 9
    assert count_test_cases(BenchStack.JAVA, BENCH / "hidden_tests" / "java-todo-crud") == 9
    assert count_test_cases(BenchStack.ANGULAR, BENCH / "hidden_tests" / "ng-counter") == 6


def test_filters_and_unknown_ids() -> None:
    python = load_tasks(BENCH / "tasks", BENCH / "hidden_tests", stacks=["python"])
    assert {t.stack for t in python} == {BenchStack.PYTHON} and len(python) == 4
    one = load_tasks(BENCH / "tasks", BENCH / "hidden_tests", ids=["ng-counter"])
    assert [t.id for t in one] == ["ng-counter"]
    with pytest.raises(TaskFileError, match="unknown task"):
        load_tasks(BENCH / "tasks", BENCH / "hidden_tests", ids=["nope"])


def test_task_without_hidden_tests_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "x.yaml").write_text(
        "stack: python\ntasks:\n  - {id: t1, difficulty: 1, title: t, request: r}\n"
    )
    with pytest.raises(TaskFileError, match="no hidden tests"):
        load_tasks(tmp_path / "tasks", tmp_path / "hidden")
    (tmp_path / "hidden" / "t1").mkdir(parents=True)
    (tmp_path / "hidden" / "t1" / "test_x.py").write_text("X = 1\n")
    with pytest.raises(TaskFileError, match="no python test cases"):
        load_tasks(tmp_path / "tasks", tmp_path / "hidden")


@pytest.mark.parametrize("stack", list(BenchStack))
def test_hidden_test_commands_pass_the_sandbox_policy(stack: BenchStack) -> None:
    task = next(t for t in load_tasks(BENCH / "tasks", BENCH / "hidden_tests") if t.stack is stack)
    check_command(task.test_command)


# ------------------------------------------------------------------------------ parsing
@pytest.mark.parametrize(
    ("stack", "output", "expected"),
    [
        (BenchStack.PYTHON, "....\n9 passed in 0.14s\n", SuiteCounts(passed=9, reported=True)),
        (
            BenchStack.PYTHON,
            "FAILED t.py::a\n2 failed, 5 passed, 1 error in 0.5s\n",
            SuiteCounts(passed=5, failed=3, reported=True),
        ),
        (BenchStack.PYTHON, "ERROR collecting\nImportError: app.main\n", SuiteCounts()),
        (
            BenchStack.JAVA,
            "[INFO] Tests run: 9, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 5.5 s -- in X\n"
            "[INFO] Results:\n[ERROR] Tests run: 9, Failures: 2, Errors: 1, Skipped: 1\n",
            SuiteCounts(passed=5, failed=3, reported=True),
        ),
        (BenchStack.JAVA, "[ERROR] COMPILATION ERROR : cannot find symbol\n", SuiteCounts()),
        (
            BenchStack.ANGULAR,
            "\x1b[1A\x1b[2KChrome Headless: Executed 6 of 7\x1b[32m SUCCESS\x1b[39m (0 secs)\n"
            "Chrome Headless: Executed 7 of 7\x1b[31m (2 FAILED)\x1b[39m (0.2 secs / 0.1 secs)\n",
            SuiteCounts(passed=5, failed=2, reported=True),
        ),
        (BenchStack.ANGULAR, "ERROR [karma-server]: Error: Found 1 load error\n", SuiteCounts()),
    ],
)
def test_parse_test_output(stack: BenchStack, output: str, expected: SuiteCounts) -> None:
    assert parse_test_output(stack, output) == expected


def test_scoring_counts_tests_that_never_ran_as_failures() -> None:
    none_ran = HiddenTestResult.scored(
        SuiteCounts(), expected=8, command="c", exit_code=1, output="boom"
    )
    assert (none_ran.passed, none_ran.failed, none_ran.pass_rate) == (0, 8, 0.0)
    partial = HiddenTestResult.scored(
        SuiteCounts(passed=6, failed=0, reported=True),
        expected=8,
        command="c",
        exit_code=0,
        output="",
    )
    assert partial.failed == 2 and partial.pass_rate == 0.75


# ------------------------------------------------------------------------------ auto policy
def interrupt(kind: str, allowed: list[str], **data: Any) -> dict[str, Any]:
    return {"kind": kind, "allowed_actions": allowed, "data": data, "title": kind}


def test_auto_policy_approves_answers_and_bounds_escalations() -> None:
    policy = AutoPolicy(max_escalations=1)
    assert policy.payload(interrupt("approval", ["approve", "reject", "edit"])).action is (
        ResumeAction.APPROVE
    )
    answer = policy.payload(interrupt("question", ["answer"]))
    assert answer.action is ResumeAction.ANSWER and answer.answer == AUTO_ANSWER
    escalation = ["approve", "answer", "reject"]
    retry = policy.payload(interrupt("escalation", escalation, iterations=3, reason="limit"))
    assert retry.action is ResumeAction.ANSWER and retry.answer == AUTO_ANSWER
    give_up = policy.payload(interrupt("escalation", escalation, iterations=3, reason="limit"))
    assert give_up.action is ResumeAction.REJECT
    # delivery failures cannot be answered: finish without a PR instead of retrying forever
    assert policy.payload(interrupt("escalation", ["approve", "reject"])).action is (
        ResumeAction.REJECT
    )
    assert (policy.escalations, policy.questions, policy.carried_iterations) == (3, 1, 3)


# ------------------------------------------------------------------------------ headless runs
def fake_task(tmp_path: Path, tests: int = 3) -> BenchmarkTask:
    hidden = tmp_path / "hidden" / "bench-todo" / "tests_hidden"
    hidden.mkdir(parents=True)
    (hidden / "test_hidden.py").write_text(
        "".join(f"def test_{i}() -> None:\n    assert True\n\n\n" for i in range(tests))
    )
    return BenchmarkTask(
        id="bench-todo",
        stack=BenchStack.PYTHON,
        difficulty=1,
        title="TODO",
        request="Build a FastAPI TODO API with CRUD and pytest tests",
        hidden_dir=hidden.parent,
        expected_tests=tests,
    )


async def test_headless_run_scores_hidden_tests_in_the_sandbox(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.script(
        "tests_hidden",
        (1, "FAILED tests_hidden/test_hidden.py::test_2\n1 failed, 2 passed in 0.1s"),
    )
    h = make_harness(tmp_path, runner=runner)
    lines: list[str] = []
    bench = BenchmarkRunner(h.deps, echo=lines.append)
    result = await bench.run_task(fake_task(tmp_path))

    assert result.status == "completed" and result.error is None
    assert (result.hidden.passed, result.hidden.failed, result.hidden.expected) == (2, 1, 3)
    assert result.hidden.pass_rate == pytest.approx(0.6667, abs=1e-4)
    assert (result.planned_tasks, result.merged_tasks, result.dev_iterations) == (2, 2, 2)
    assert (result.escalations, result.questions_to_human) == (0, 0)
    assert result.integration_tests_passed is True
    assert result.tokens.input > 0 and result.tokens.calls > 0
    assert {"planner", "developer", "reviewer"} <= set(result.tokens.by_role)

    # The hidden suite ran in the sandbox, from the stack's project dir, in a separate eval
    # target that is cleaned up afterwards (the run's own resources were released at `done`).
    hidden_calls = [c for c in runner.calls if "tests_hidden" in c[1]]
    assert len(hidden_calls) == 1 and hidden_calls[0][2] == "."
    eval_id = f"{result.run_id}-eval"
    assert runner.cleaned == [result.run_id, eval_id]
    eval_dir = Path(result.workspace or "").parent / "hidden-eval"
    assert (eval_dir / "app" / "schemas" / "todo.py").is_file()  # exported integration branch
    assert (eval_dir / "tests_hidden" / "test_hidden.py").is_file()
    assert not (eval_dir / ".git").exists()
    assert any("completed: hidden 2/3" in line for line in lines)


async def test_questions_are_auto_answered_and_counted(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.script("tests_hidden", (0, "3 passed in 0.1s"))
    h = make_harness(tmp_path, runner=runner)

    def developer(call: Call) -> AIMessage:
        if current_task_id(call) == "T1":
            answers = [m for m in tool_results(call) if m.name == "ask_human"]
            if not answers:
                return tool_call("ask_human", question="Ints or UUIDs?")
            if len(tool_results(call)) == 1:
                assert answers[0].content == AUTO_ANSWER
                return tool_call("write_file", path="app/schemas/todo.py", content="id: int\n")
            return final("done")
        return default_developer(call)

    h.brain.responders["developer"] = developer
    result = await BenchmarkRunner(h.deps).run_task(fake_task(tmp_path))
    assert result.status == "completed"
    assert result.questions_to_human == 1 and result.questions_to_agents == 0
    assert result.hidden.pass_rate == 1.0


async def test_escalations_are_retried_then_given_up(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.script("tests_hidden", (1, "3 failed in 0.1s"))
    h = make_harness(tmp_path, runner=runner, max_dev_iterations=1)
    issue = {"file": "app/schemas/todo.py", "line": 1, "severity": "major", "message": "wrong"}
    reject = {"decision": "changes_requested", "summary": "no", "issues": [issue]}

    def reviewer(call: Call) -> AIMessage:
        prompt = str(call.messages[1].content)
        return final(
            reject if "id: T1" in prompt else {"decision": "approve", "summary": "ok", "issues": []}
        )

    h.brain.responders["reviewer"] = reviewer
    result = await BenchmarkRunner(h.deps, max_escalations=1).run_task(fake_task(tmp_path))

    assert result.status == "completed"  # the run finishes; T1 failed, T2 blocked
    assert result.escalations == 2
    assert result.failed_tasks == 1 and result.merged_tasks == 0
    # 1 iteration before the retry (counter reset by the escalation) + 1 after it
    assert result.dev_iterations == 2
    assert result.coordinator_calls >= 2
    assert result.hidden.ran and result.hidden.pass_rate == 0.0


async def test_failed_run_is_reported_without_hidden_tests(tmp_path: Path) -> None:
    runner = FakeRunner()
    h = make_harness(tmp_path, runner=runner, max_coordinator_actions=0)

    def planner(call: Call) -> AIMessage:
        raise RuntimeError("ollama exploded")

    h.brain.responders["planner"] = planner
    result = await BenchmarkRunner(h.deps).run_task(fake_task(tmp_path))
    assert result.status == "failed"
    assert result.error and "ollama exploded" in result.error
    assert not result.hidden.ran and result.hidden.expected == 3
    assert "no integration branch" in (result.hidden.detail or "")


async def test_sandbox_disabled_skips_hidden_tests(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    result = await BenchmarkRunner(h.deps).run_task(fake_task(tmp_path))
    assert result.status == "completed"
    assert not result.hidden.ran and "sandbox disabled" in (result.hidden.detail or "")


async def test_benchmark_never_delivers_to_github(tmp_path: Path) -> None:
    h = make_harness(tmp_path, github=FakeGitHub(tmp_path).delivery())
    with pytest.raises(ValueError, match="GitHub"):
        BenchmarkRunner(h.deps)


# ------------------------------------------------------------------------------ reports
def task_result(task_id: str, stack: str, rate: float, passed: int) -> TaskResult:
    return TaskResult(
        task_id=task_id,
        stack=stack,
        difficulty=1,
        title="t",
        run_id="r",
        status="completed",
        wall_time_s=120.0,
        hidden=HiddenTestResult(
            ran=True, passed=passed, failed=4 - passed, expected=4, pass_rate=rate
        ),
        dev_iterations=3,
        escalations=1,
        questions_to_human=1,
        questions_to_agents=2,
    )


def test_reports_are_written_as_json_and_markdown(tmp_path: Path) -> None:
    report = BenchmarkReport(
        started_at=datetime(2026, 9, 25, 12, 0, tzinfo=UTC), settings={"max_parallel_devs": 2}
    )
    for label, rate in (("qwen-14b", 0.75), ("qwen-7b", 0.25)):
        results = [task_result("py-todo-crud", "python", rate, int(rate * 4))]
        report.configs.append(
            ConfigResult(
                label=label,
                models_config=f"{label}.yaml",
                models={"developer": label},
                tasks=results,
                summary=ConfigSummary.of(results),
            )
        )
    json_path, md_path = write_report(report, tmp_path / "results")
    assert json_path.name == "20260925T120000Z.json"
    data = json.loads(json_path.read_text())
    assert data["configs"][0]["summary"]["mean_pass_rate"] == 0.75
    assert data["configs"][1]["summary"]["pass_rate_by_stack"] == {"python": 0.25}
    md = md_path.read_text()
    assert "| py-todo-crud | python | 1 | completed | 3/4 (75%) | 3 | 1 | 1/2 | 2.0 min |" in md
    assert "## Comparison" in md and "| qwen-7b | 1/1 | 25% | 1/4 |" in md
