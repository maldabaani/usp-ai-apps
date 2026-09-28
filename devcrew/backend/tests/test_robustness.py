"""Phase 15: real-run robustness (baselines, coverage sources, install state, mount points)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.gates.checks import failing_tests
from app.graph.layout import LayoutEntry
from app.graph.state import TestResult as RunTestResult
from app.graph.wave_check import new_failures
from app.repo.detect import coverage_for_python, detect_projects, python_sources
from app.sandbox.runner import SandboxTarget
from app.sandbox.service import Sandbox
from app.tools.catalog import TemplateInfo
from tests.fakes import FakeRunner
from tests.graph_harness import APPROVE
from tests.test_existing_repo import PY_REPO, RUN, existing_harness, start, write


# ------------------------------------------------------------------------------ BL-122
def test_python_coverage_measures_the_source_packages(tmp_path: Path) -> None:
    write(tmp_path / "flat", {"shop/__init__.py": "", "tests/__init__.py": "", "docs/x.md": ""})
    assert python_sources(tmp_path / "flat") == ["shop"]
    write(tmp_path / "src", {"src/shop/__init__.py": "", "src/shop_cli/__init__.py": ""})
    assert python_sources(tmp_path / "src") == ["src/shop", "src/shop_cli"]
    write(tmp_path / "none", {"main.py": ""})
    assert python_sources(tmp_path / "none") == []
    assert "--cov=. " in coverage_for_python([])
    assert "--cov=src/shop --cov=src/shop_cli" in coverage_for_python(["src/shop", "src/shop_cli"])

    write(tmp_path / "repo", {**PY_REPO})
    [project] = detect_projects(tmp_path / "repo").projects
    assert "--cov=app " in (project.coverage_cmd or "")  # not the tests


# ------------------------------------------------------------------------------ BL-151
def test_failing_test_names_per_stack() -> None:
    assert failing_tests(
        "python", "x\nFAILED tests/test_a.py::test_b - assert 1\nERROR tests/c.py"
    ) == [
        "tests/c.py",
        "tests/test_a.py::test_b",
    ]
    java = "[ERROR]   TodoServiceTest.create:42 expected: <1>\n[ERROR] Tests run: 3, Failures: 1"
    assert failing_tests("java", java) == ["TodoServiceTest.create"]
    ng = "Chrome Headless 141.0.0.0 (Linux 0.0.0) AppComponent should create FAILED\n"
    assert failing_tests("angular", ng) == ["AppComponent should create"]


def test_only_failures_the_run_caused_count() -> None:
    state: dict[str, Any] = {
        "gate_baseline": {"tests": {"python": {"passed": False, "failing": ["t.py::old"]}}}
    }
    same = RunTestResult(ran=True, passed=False, failed=["t.py::old"])
    assert new_failures(state, {"python": same}) == ({}, ["python"])
    worse = RunTestResult(ran=True, passed=False, failed=["t.py::old", "t.py::new"])
    caused, known = new_failures(state, {"python": worse})
    assert caused["python"].failed == ["t.py::new"] and known == []
    # without a baseline (new projects) every failure is new
    assert new_failures({}, {"python": same})[0] == {"python": same}


class BaseBranchFails(FakeRunner):
    """The integration branch keeps failing the same old test the base branch fails."""

    def __init__(self) -> None:
        super().__init__()
        self.branch_runs = 0

    async def run(self, target: Any, command: str, **kwargs: Any) -> Any:
        if target.task_id is None and "pytest" in command and "pip install" not in command:
            self.branch_runs += 1
            self.calls.append((None, command, ".", False, target.image_stack))
            return self._result(exit_code=1, output="FAILED tests/test_legacy.py::test_old\n")
        return await super().run(target, command, **kwargs)


async def test_failures_the_base_branch_already_has_add_no_fix_task(tmp_path: Path) -> None:
    runner = BaseBranchFails()
    h, _, _ = existing_harness(tmp_path, runner=runner)
    await start(h, mode="quick")
    outcome = await h.driver.resume(RUN, APPROVE)
    state = await h.driver.state(RUN)
    assert state["gate_baseline"]["tests"]["python"] == {
        "passed": False,
        "failing": ["tests/test_legacy.py::test_old"],
    }
    assert "WAVEFIX1" not in state["tasks"]  # the wave check saw only the old failure
    events = await h.events(RUN)
    assert any("fail as on the base branch" in str(e.payload.get("result")) for e in events)
    assert outcome.status.value == "awaiting_final_approval"


# ------------------------------------------------------------------------------ BL-125
async def test_the_baseline_is_cached_per_base_commit(tmp_path: Path) -> None:
    runner = BaseBranchFails()
    h, _, _ = existing_harness(tmp_path, runner=runner)
    await start(h, mode="quick")
    await h.driver.resume(RUN, APPROVE)
    first = runner.branch_runs
    second = "run0011eeeeffff00001111"
    await h.driver.start(second, "Another change", "acme/shop", target="existing", mode="quick")
    await h.driver.resume(second, APPROVE)
    events = await h.events(second)
    assert any("baseline reused" in str(e.payload.get("result")) for e in events)
    # the second run skipped the base-branch test run: only its wave checks + integration ran
    assert runner.branch_runs - first == first - 1


# ------------------------------------------------------------------------------ BL-011
async def test_install_state_survives_a_restart(tmp_path: Path) -> None:
    project = tmp_path / "ws" / "run1" / "repo"
    write(project, {"pyproject.toml": "[project]\nname='x'\n"})
    tpl = TemplateInfo(
        id="py",
        stack="python",
        description="d",
        install_cmd="pip install -e .",
        build_cmd="b",
        test_cmd="pytest -q",
    )
    layout = {"python": LayoutEntry("python", ".", tpl)}
    target = SandboxTarget("run1", "T1", project, "python", {"python": "."})
    state = tmp_path / "state"
    runner = FakeRunner()
    await Sandbox(runner, state_dir=state).ensure_dependencies(target, layout)
    restarted = Sandbox(runner, state_dir=state)  # a new process: nothing in memory
    [outcome] = await restarted.ensure_dependencies(target, layout)
    assert outcome.skipped
    assert [c for _, c, _ in runner.commands(network=True)] == ["pip install -e ."]
    (project / "pyproject.toml").write_text("[project]\nname='y'\n")  # manifest changed
    [outcome] = await restarted.ensure_dependencies(target, layout)
    assert not outcome.skipped
    await restarted.cleanup_run("run1")
    assert not (state / "run1.json").exists()


# ------------------------------------------------------------------------------ BL-021
def test_node_modules_mount_points_are_created_by_the_backend(tmp_path: Path) -> None:
    from app.sandbox.docker_runner import DockerSandboxRunner

    target = SandboxTarget("run1", "T1", tmp_path, "mixed", {"angular": "frontend", "python": "."})
    DockerSandboxRunner._make_mount_points(target)
    assert (tmp_path / "frontend" / "node_modules").is_dir()


# ------------------------------------------------------------------------------ BL-100
async def test_split_parts_and_search_the_full_document() -> None:
    from app.graph.requirements import SearchRequirementsArgs, search_requirements_tool, split_parts

    text = "\n\n".join(f"para {i} " + "x" * 50 for i in range(10))
    parts = split_parts(text, 200)
    assert all(len(p) <= 200 for p in parts) and "".join(parts).count("para") == 10
    doc = "# Orders\n\nOrders have a status.\n\n# Refunds\n\nRefunds need a reason code R1-R9."
    tool = search_requirements_tool(doc)
    assert tool.handler is not None
    found = await tool.handler(SearchRequirementsArgs(query="refund reason codes"))
    assert "R1-R9" in found and "[# Refunds]" in found
    missing = await tool.handler(SearchRequirementsArgs(query="shipping"))
    assert missing.startswith("No part")


async def test_long_documents_are_condensed_for_planning(tmp_path: Path) -> None:
    from langchain_core.messages import AIMessage

    from tests.api_harness import api
    from tests.fakes import Call, final
    from tests.graph_harness import PLAN

    def planner(call: Call) -> AIMessage:
        if "requirements document for planning" in str(call.messages[0].content):
            return final("- Orders have a status\n- Refunds need a reason code")
        return final(PLAN)

    long_doc = "# Shop requirements\n\n" + "\n\n".join(
        f"## Section {i}\n\nOrders and refunds rule {i}: " + "detail " * 30 for i in range(12)
    )
    async with api(tmp_path, max_request_chars=1000, max_document_chars=20000) as a:
        a.harness.brain.responders["planner"] = planner
        resp = await a.client.post("/runs", json={"request": long_doc, "repo_target": "o/r"})
        assert resp.status_code == 201, resp.text
        run = await a.settle(resp.json()["id"])
        assert run["status"] == "awaiting_plan_approval"
        assert run["request_digest"].startswith("(Condensed from a")
        calls = a.harness.brain.calls_for("planner")
        condense_calls = [c for c in calls if "for planning" in str(c.messages[0].content)]
        assert len(condense_calls) >= 3  # the document was condensed part by part
        planning = next(c for c in calls if c not in condense_calls)
        context = str(planning.messages[1].content)
        assert "Condensed from a" in context and "Refunds need a reason code" in context
        assert "detail detail detail" not in context  # not the full text
        assert "search_requirements" in planning.tool_names
        run = await a.approve(run["id"])  # the Architect reads the digest too
        architect = str(a.harness.brain.calls_for("architect")[0].messages[1].content)
        assert "Condensed from a" in architect
        assert run["request"] == long_doc.strip()  # the run keeps the full document
