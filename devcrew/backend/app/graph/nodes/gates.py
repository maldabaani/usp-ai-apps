"""Quality gates on the integrated result, before the final approval.

gates: secrets (changed files), dependencies (new vulnerabilities) and coverage (threshold for
new projects, no drop for existing repositories). If a gate fails and automatic fix rounds are
left, a fix task carrying the findings is scheduled (developer -> review -> QA as usual) and the
result is integrated and checked again. Otherwise the final approval shows the failures and the
human decides (secret findings can never be allowed through).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from langgraph.types import Command

from app.db.models import RunStatus
from app.events.types import EventType
from app.gates.checks import (
    GateReport,
    GateResult,
    coverage_gate,
    dependencies_gate,
    secrets_gate,
)
from app.gates.runner import measure_coverage, scan_dependencies, scan_secrets, vulnerability_keys
from app.graph.layout import LayoutEntry, resolve_layout, sandbox_target
from app.graph.runtime import GraphDeps, NodeFn
from app.graph.state import Plan, PlanTask, TaskState, dump, get_design, get_plan
from app.sandbox.runner import SandboxTarget
from app.tools.git import GitRepo

NODE = "gates"


async def take_baseline(
    deps: GraphDeps, run_id: str, target: SandboxTarget, layout: dict[str, LayoutEntry]
) -> dict[str, Any]:
    """Coverage and known vulnerabilities on the base branch of an existing repository."""
    assert deps.sandbox is not None
    await deps.emit(run_id, EventType.TOOL_CALL, node="scaffold", tool="gate_baseline", args={})
    coverage = await measure_coverage(deps.sandbox, target, layout)
    scan = await scan_dependencies(deps.sandbox, target, layout)
    baseline = {
        "coverage": coverage,
        "vulnerabilities": vulnerability_keys(scan.vulnerabilities),
        "errors": scan.errors,
    }
    shown = ", ".join(
        f"{s} {v:.1f}%" if v is not None else f"{s} n/a" for s, v in sorted(coverage.items())
    )
    await deps.emit(
        run_id,
        EventType.TOOL_RESULT,
        node="scaffold",
        tool="gate_baseline",
        ok=True,
        result=f"base branch coverage: {shown or 'not measured'}; "
        f"{len(baseline['vulnerabilities'])} known vulnerable package(s)",
    )
    return baseline


def gate_fix_task(plan: Plan, report: GateReport, number: int) -> PlanTask:
    stacks = [t.stack for t in plan.tasks]
    stack = Counter(stacks).most_common(1)[0][0]
    return PlanTask(
        id=f"GATEFIX{number}",
        title="Fix quality gate failures",
        description=(
            "The integrated change failed DevCrew's quality gates. Fix every finding below "
            "without weakening the checks (no disabled tests, no removed coverage):\n"
            f"{report.feedback()}\n\n"
            "Secrets: remove them from the code and read them from environment variables or "
            "configuration instead. Vulnerable dependencies: upgrade to a fixed version. "
            "Coverage: add meaningful tests for the changed code."
        ),
        target_files=[],
        depends_on=[],
        stack=stack,
    )


def auto_push(state: dict[str, Any]) -> bool:
    """A PR follow-up round pushes without final approval while the integration tests pass
    (failing tests go to the human's final approval, like a normal run)."""
    tests = ((state.get("integration") or {}).get("tests") or {}).values()
    return bool(state.get("followup_active")) and all(t.get("passed", True) for t in tests)


def make_gates(deps: GraphDeps) -> NodeFn:
    async def gates(state: dict[str, Any]) -> Command[str]:
        run_id = state["run_id"]
        rounds = int(state.get("gate_fix_rounds") or 0)
        report = GateReport(round=rounds)
        if not deps.settings.gates_enabled or deps.sandbox is None:
            reason = "gates disabled" if not deps.settings.gates_enabled else "sandbox disabled"
            report.results = [
                GateResult(name=n, status="skipped", summary=reason)
                for n in ("secrets", "dependencies", "coverage")
            ]
            if auto_push(state):  # PR follow-up round: push the fixes
                return Command(
                    goto="github_delivery",
                    update={"gates": dump(report), "status": RunStatus.DELIVERING.value},
                )
            return Command(
                goto="approve_final",
                update={"gates": dump(report), "status": RunStatus.AWAITING_FINAL_APPROVAL.value},
            )

        root = Path(state["workspace"])
        design = get_design(state)
        layout = resolve_layout(design, deps.templates)
        target = sandbox_target(run_id, None, root, design, layout)
        base = state.get("base_branch") or "main"
        changed = await GitRepo(root).changed_files(base, state["integration_branch"])
        baseline = state.get("gate_baseline") if design.existing_projects else None

        await deps.emit(run_id, EventType.TOOL_CALL, node=NODE, tool="secret_scan", args={})
        secrets = await scan_secrets(deps.sandbox, target, changed)
        secret_result = (
            GateResult(
                name="secrets",
                status="error",
                summary="secret scan did not run",
                details=secrets.errors,
                allowable=False,
            )
            if secrets.errors
            else secrets_gate(secrets.findings, changed)
        )
        await deps.emit(run_id, EventType.TOOL_CALL, node=NODE, tool="dependency_scan", args={})
        scan = await scan_dependencies(deps.sandbox, target, layout)
        deps_result = dependencies_gate(
            scan.vulnerabilities,
            known=(baseline or {}).get("vulnerabilities") or [],
            errors=scan.errors,
        )
        coverage = (state.get("integration") or {}).get("coverage") or {}
        coverage_result = coverage_gate(
            coverage,
            baseline=(baseline or {}).get("coverage") if baseline is not None else None,
            minimum=deps.settings.gate_coverage_min,
            tolerance=deps.settings.gate_coverage_tolerance,
        )
        report.results = [secret_result, deps_result, coverage_result]
        for r in report.results:
            await deps.emit(
                run_id,
                EventType.TOOL_RESULT,
                node=NODE,
                tool=f"gate_{r.name}",
                ok=r.status in ("passed", "skipped"),
                skipped=r.status == "skipped",
                result=f"{r.status}: {r.summary}",
                details=r.details[:20],
            )

        if auto_push(state) and not report.failed:
            # PR follow-up round: already approved; push the fixes to the PR branch.
            return Command(
                goto="github_delivery",
                update={"gates": dump(report), "status": RunStatus.DELIVERING.value},
            )
        if report.failed and rounds < deps.settings.max_gate_fix_rounds:
            plan = get_plan(state)
            task = gate_fix_task(plan, report, rounds + 1)
            new_plan = plan.model_copy(update={"tasks": [*plan.tasks, task]})
            return Command(
                goto="schedule",
                update={
                    "plan": dump(new_plan),
                    "tasks": {task.id: dump(TaskState(id=task.id))},
                    "gates": dump(report),
                    "gate_fix_rounds": rounds + 1,
                    "status": RunStatus.EXECUTING.value,
                },
            )
        return Command(
            goto="approve_final",
            update={"gates": dump(report), "status": RunStatus.AWAITING_FINAL_APPROVAL.value},
        )

    return gates
