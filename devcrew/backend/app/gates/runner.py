"""Run the gate tools in the sandbox and turn their output into GateResults."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from app.gates.checks import (
    OSV_CMDS,
    SecretFinding,
    Vulnerability,
    parse_coverage,
    parse_gitleaks,
    parse_osv,
    secret_scan_command,
    tool_missing,
)
from app.graph.layout import LayoutEntry
from app.sandbox.runner import SandboxTarget
from app.sandbox.service import Sandbox


@dataclass
class ScanOutcome:
    findings: list[SecretFinding] = field(default_factory=list)
    vulnerabilities: list[Vulnerability] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


async def scan_secrets(
    sandbox: Sandbox, target: SandboxTarget, files: Iterable[str]
) -> ScanOutcome:
    """gitleaks over the given files (offline; secret values are redacted in its report)."""
    files = [f for f in files if f]
    if not files:
        return ScanOutcome()
    result = await sandbox.runner.run(target, secret_scan_command(files), cwd=".")
    if tool_missing(result.output, "gitleaks"):
        return ScanOutcome(errors=["gitleaks is not installed in the sandbox image (rebuild it)"])
    try:
        return ScanOutcome(findings=parse_gitleaks(result.output))
    except ValueError as exc:
        return ScanOutcome(errors=[f"gitleaks failed (exit {result.exit_code}): {exc}"])


async def scan_dependencies(
    sandbox: Sandbox, target: SandboxTarget, layout: Mapping[str, LayoutEntry]
) -> ScanOutcome:
    """osv-scanner per project. It queries osv.dev, so it runs with network (like installs)."""
    await sandbox.ensure_dependencies(target, layout)
    outcome = ScanOutcome()
    for stack, entry in sorted(layout.items()):
        command = OSV_CMDS.get(stack)
        if command is None:
            continue
        result = await sandbox.runner.run(target, command, cwd=entry.path, network=True)
        if tool_missing(result.output, "osv-scanner"):
            outcome.errors.append("osv-scanner is not installed in the sandbox image (rebuild it)")
            continue
        if result.exit_code not in (0, 1, 128):
            outcome.errors.append(
                f"{stack}: osv-scanner failed (exit {result.exit_code}): {result.output[-300:]}"
            )
            continue
        outcome.vulnerabilities += parse_osv(result.output)
    return outcome


async def measure_coverage(
    sandbox: Sandbox, target: SandboxTarget, layout: Mapping[str, LayoutEntry]
) -> dict[str, float | None]:
    """Run each project's coverage command (used for the base-branch baseline)."""
    measured: dict[str, float | None] = {}
    for stack, entry in sorted(layout.items()):
        command = entry.template.coverage_cmd
        if not command:
            continue
        result = await sandbox.exec(target, layout, command, cwd=entry.path)
        measured[stack] = parse_coverage(stack, result.output)
    return measured


def vulnerability_keys(vulns: Iterable[Vulnerability]) -> list[str]:
    return sorted(v.key for v in vulns)
