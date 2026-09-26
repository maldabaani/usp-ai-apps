"""Phase 11: quality gates (parsers, rules, graph flow with automatic fix and final decision)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.db.models import RunStatus
from app.gates.checks import (
    GateReport,
    SecretFinding,
    Vulnerability,
    coverage_gate,
    dependencies_gate,
    parse_coverage,
    parse_gitleaks,
    parse_osv,
    secrets_gate,
)
from app.github.pr_body import pr_body
from app.graph.interrupts import ResumePayload
from app.graph.runner import RunOutcome
from tests.fake_github import FakeGitHub
from tests.fakes import FakeRunner
from tests.graph_harness import APPROVE, Harness, make_harness
from tests.test_existing_repo import PY_REPO, seeded_repo

RUN = "run0011gatesbbbbccccdddd"

LEAK = json.dumps(
    [{"RuleID": "github-pat", "File": "app/schemas/todo.py", "StartLine": 3, "Secret": "REDACTED"}]
)
OSV_VULN = json.dumps(
    {
        "results": [
            {
                "source": {"path": "/tmp/devcrew-requirements.txt", "type": "lockfile"},
                "packages": [
                    {
                        "package": {"name": "jinja2", "version": "2.4.1", "ecosystem": "PyPI"},
                        "vulnerabilities": [
                            {"id": "GHSA-462w-v97r-4m45"},
                            {"id": "PYSEC-2019-217"},
                        ],
                    },
                    {"package": {"name": "fastapi", "version": "0.115.0", "ecosystem": "PyPI"}},
                ],
            }
        ]
    }
)


# ------------------------------------------------------------------------------ parsers
def test_parse_coverage_per_stack() -> None:
    pytest_out = (
        "....\nName  Stmts  Miss  Cover\n---\napp/x.py  10  2  80%\nTOTAL   120  18  85%\n4 passed"
    )
    assert parse_coverage("python", pytest_out) == 85.0
    karma = (
        "=== Coverage summary ===\nStatements   : 90.5% ( 19/21 )\n"
        "\x1b[32mLines        : 87.25% ( 18/20 )\x1b[39m"
    )
    assert parse_coverage("angular", karma) == 87.25
    assert parse_coverage("java", "[INFO] done\nCOVERAGE_LINES 64.30\n") == 64.3
    assert parse_coverage("python", "4 passed") is None


def test_parse_gitleaks_and_osv() -> None:
    noisy = "9:12AM INF scanned 160 bytes\n" + LEAK
    assert parse_gitleaks(noisy) == [
        SecretFinding(file="app/schemas/todo.py", line=3, rule="github-pat")
    ]
    assert parse_gitleaks("[]") == []
    with pytest.raises(ValueError):
        parse_gitleaks("gitleaks: crashed")
    [vuln] = parse_osv("Scanned 12 packages\n" + OSV_VULN)
    assert (vuln.package, vuln.version, vuln.ids) == (
        "jinja2",
        "2.4.1",
        ["GHSA-462w-v97r-4m45", "PYSEC-2019-217"],
    )
    assert parse_osv("No package sources found") == []


# ------------------------------------------------------------------------------ rules
def test_coverage_rules() -> None:
    new = coverage_gate({"python": 65.0, "angular": 90.0}, baseline=None, minimum=70, tolerance=0.5)
    assert new.status == "failed" and "65.0% is below the 70% minimum" in new.details[1]
    existing = coverage_gate({"python": 41.8}, baseline={"python": 42.0}, minimum=70, tolerance=0.5)
    assert existing.status == "passed"  # an old repo at 42% may stay there
    drop = coverage_gate({"python": 40.0}, baseline={"python": 42.0}, minimum=70, tolerance=0.5)
    assert drop.status == "failed" and "down from 42.0%" in drop.details[0]
    assert (
        coverage_gate({"python": None}, baseline=None, minimum=70, tolerance=0).status == "skipped"
    )
    assert coverage_gate({}, baseline=None, minimum=70, tolerance=0).status == "skipped"


def test_secret_and_dependency_rules() -> None:
    findings = [
        SecretFinding(file="app/new.py", line=1, rule="aws"),
        SecretFinding(file="legacy/old.py", line=9, rule="generic"),
    ]
    gate = secrets_gate(findings, ["./app/new.py"])
    assert gate.status == "failed" and gate.allowable is False
    assert gate.details == [
        "app/new.py:1 (aws)",
        "1 finding(s) in files this run did not change (not blocking)",
    ]
    assert secrets_gate(findings, ["README.md"]).status == "passed"

    old = Vulnerability(package="a", version="1", ecosystem="PyPI", ids=["X-1"])
    new = Vulnerability(package="b", version="2", ecosystem="PyPI", ids=["X-2"])
    gate = dependencies_gate([old, new], known=[old.key])
    assert gate.status == "failed" and gate.details[0].startswith("b 2 (PyPI)")
    assert dependencies_gate([old], known=[old.key]).status == "passed"
    assert dependencies_gate([], errors=["osv-scanner failed"]).status == "error"


# ------------------------------------------------------------------------------ graph flow
def gated(tmp_path: Path, runner: FakeRunner, **settings: Any) -> Harness:
    return make_harness(tmp_path, runner=runner, gates_enabled=True, **settings)


async def run_to_final(h: Harness) -> RunOutcome:
    await h.driver.start(RUN, "Build a TODO API", "me/todo", False)
    await h.driver.resume(RUN, APPROVE)
    return await h.driver.resume(RUN, APPROVE)


async def test_secret_in_a_task_goes_back_to_the_developer(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.script("gitleaks", (0, LEAK), (0, "[]"), (0, "[]"), (0, "[]"))  # T1 twice, T2, final
    h = gated(tmp_path, runner)
    outcome = await run_to_final(h)
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL
    state = await h.driver.state(RUN)
    assert state["tasks"]["T1"]["iterations"] == 2 and state["tasks"]["T2"]["iterations"] == 1
    t1_dev = [c for c in h.brain.calls_for("developer") if "id: T1" in str(c.messages[1].content)]
    assert "secret scan found credentials" in str(t1_dev[-1].messages[1].content)
    assert "app/schemas/todo.py:3 (github-pat)" in str(t1_dev[-1].messages[1].content)
    assert {r["name"]: r["status"] for r in state["gates"]["results"]}["secrets"] == "passed"


async def test_failed_gate_gets_an_automatic_fix_task(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.script("osv-scanner", (1, OSV_VULN))  # first integration: vulnerable dependency
    runner.script("gitleaks", *[(0, "[]")] * 10)
    h = gated(tmp_path, runner)
    outcome = await run_to_final(h)
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL
    state = await h.driver.state(RUN)
    assert state["gate_fix_rounds"] == 1
    fix = next(t for t in state["plan"]["tasks"] if t["id"] == "GATEFIX1")
    assert (
        "jinja2 2.4.1" in fix["description"] and "upgrade to a fixed version" in fix["description"]
    )
    assert state["tasks"]["GATEFIX1"]["status"] == "merged"
    gates = {r["name"]: r["status"] for r in state["gates"]["results"]}
    assert gates["dependencies"] == "passed"  # fixed in the automatic round
    assert outcome.interrupts[0].value["allowed_actions"] == ["approve", "reject"]
    events = await h.events(RUN)
    assert any(
        e.payload.get("tool") == "gate_dependencies" and not e.payload.get("ok") for e in events
    )


async def test_unfixed_gate_is_decided_by_the_human(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.script("osv-scanner", (1, OSV_VULN), (1, OSV_VULN))  # still vulnerable after the fix
    runner.script("gitleaks", *[(0, "[]")] * 10)
    h = gated(tmp_path, runner)
    outcome = await run_to_final(h)
    value = outcome.interrupts[0].value
    assert value["title"] == "Approve the final result (quality gates failed)"
    assert value["allowed_actions"] == ["approve", "reject"]
    state = await h.driver.state(RUN)
    body = pr_body({**state, "gates": state["gates"]})
    assert "## Quality gates" in body and "Accepted by the reviewer despite failing gates" in body
    assert "jinja2 2.4.1" in body
    outcome = await h.driver.resume(RUN, APPROVE)  # allowed: noted in the PR
    assert outcome.status is RunStatus.COMPLETED


async def test_secrets_can_never_be_approved(tmp_path: Path) -> None:
    runner = FakeRunner()
    # tasks are clean, but the integrated tree has a secret in a changed file (twice)
    runner.script("gitleaks", (0, "[]"), (0, "[]"), (0, LEAK), (0, "[]"), (0, LEAK))
    h = gated(tmp_path, runner)
    outcome = await run_to_final(h)
    value = outcome.interrupts[0].value
    assert value["allowed_actions"] == ["reject"]
    assert value["title"].startswith("Secrets found")
    state = await h.driver.state(RUN)
    report = GateReport.model_validate(state["gates"])
    assert [g.name for g in report.blocking] == ["secrets"]
    bad = await h.driver.resume(RUN, APPROVE)  # refused by the gate: re-asks with an error
    assert "not allowed" in (bad.interrupts[0].value["error"] or "")


async def test_existing_repo_coverage_is_compared_with_the_base_branch(tmp_path: Path) -> None:
    gh = FakeGitHub(tmp_path / "remotes")
    seeded_repo(gh, "acme", "shop", PY_REPO)
    runner = FakeRunner()
    runner.script(
        "--cov", (0, "TOTAL 100 20 80%"), (0, "TOTAL 100 30 70%"), (0, "TOTAL 100 19 81%")
    )
    runner.script("gitleaks", *[(0, "[]")] * 10)
    runner.script("osv-scanner", (1, OSV_VULN), (1, OSV_VULN), (1, OSV_VULN))  # known on base
    h = make_harness(tmp_path, runner=runner, github=gh.delivery(), gates_enabled=True)
    await h.driver.start(RUN, "Add discounts", "acme/shop", target="existing", mode="quick")
    outcome = await h.driver.resume(RUN, APPROVE)
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL
    state = await h.driver.state(RUN)
    assert state["gate_baseline"]["coverage"] == {"python": 80.0}
    assert len(state["gate_baseline"]["vulnerabilities"]) == 1
    assert state["gate_fix_rounds"] == 1  # 70% < 80% - 0.5 -> one automatic fix round
    gates = {r["name"]: r for r in state["gates"]["results"]}
    assert gates["coverage"]["status"] == "passed"
    assert gates["coverage"]["details"] == ["python: 81.0% (base branch 80.0%)"]
    assert gates["dependencies"]["status"] == "passed"  # the vulnerability was already there
    assert (
        "1 known vulnerability(ies) already on the base branch" in gates["dependencies"]["details"]
    )


async def test_gates_disabled_are_reported_as_skipped(tmp_path: Path) -> None:
    h = make_harness(tmp_path, runner=FakeRunner())
    outcome = await run_to_final(h)
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL
    state = await h.driver.state(RUN)
    assert {r["status"] for r in state["gates"]["results"]} == {"skipped"}
    assert h.deps.settings.gates_enabled is False
    await h.driver.resume(RUN, ResumePayload(action="approve"))
