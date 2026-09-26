"""Quality gates: secret scanning, dependency vulnerabilities, test coverage.

The tools run in the sandbox (gitleaks and osv-scanner are installed in the sandbox images);
this module holds their commands, output parsers and the pass/fail rules.

Rules:
- secrets: any finding in a file this run changed fails, and can never be allowed through;
- dependencies: a vulnerability fails unless it was already present on the base branch;
- coverage: new projects must reach COVERAGE_MIN_PERCENT; existing repositories must not drop
  more than COVERAGE_TOLERANCE percentage points below the base branch.
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, Field

GateName = Literal["secrets", "dependencies", "coverage"]
GateStatus = Literal["passed", "failed", "skipped", "error"]

SCAN_DIR = "/tmp/devcrew-scan"
SECRET_SCAN_CMD = (
    "gitleaks dir . --no-banner --redact --log-level error "
    "--report-format json --report-path - --exit-code 0"
)


def secret_scan_command(files: Iterable[str]) -> str:
    """Scan only the given files: copy them (with their paths) to a scratch dir in /tmp first.

    Scanning the whole checkout would also walk mounted dependency folders (node_modules) and
    build output; only changed files can fail the gate anyway.
    """
    quoted = " ".join(shlex.quote(f) for f in sorted(set(files)))
    return (
        f"rm -rf {SCAN_DIR} && mkdir -p {SCAN_DIR} && "
        f'for f in {quoted}; do if [ -f "$f" ]; then cp --parents -- "$f" {SCAN_DIR}/; fi; done; '
        f"cd {SCAN_DIR} && {SECRET_SCAN_CMD}"
    )


# osv-scanner exits 1 when vulnerabilities are found and 128 when there is nothing to scan.
OSV_CMDS: dict[str, str] = {
    # the virtualenv is the truth for Python (pyproject.toml alone has no pinned versions)
    "python": (
        "pip freeze --exclude-editable > /tmp/devcrew-requirements.txt && "
        "osv-scanner scan source --format json -L requirements.txt:/tmp/devcrew-requirements.txt"
    ),
    "java": "osv-scanner scan source --format json -L pom.xml",
    "angular": "osv-scanner scan source --format json -L package-lock.json",
}

_PYTEST_TOTAL = re.compile(r"^TOTAL\s.*?(\d+(?:\.\d+)?)%\s*$", re.MULTILINE)
_KARMA_LINES = re.compile(r"^\s*Lines\s*:\s*(\d+(?:\.\d+)?)%", re.MULTILINE)
_JACOCO = re.compile(r"^COVERAGE_LINES\s+(\d+(?:\.\d+)?)\s*$", re.MULTILINE)
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class GateResult(BaseModel):
    name: GateName
    status: GateStatus
    summary: str
    details: list[str] = Field(default_factory=list)
    # Can the human accept a failure at the final approval? Never for secrets.
    allowable: bool = True
    data: dict[str, Any] = Field(default_factory=dict)


class GateReport(BaseModel):
    results: list[GateResult] = Field(default_factory=list)
    round: int = 0

    @property
    def failed(self) -> list[GateResult]:
        return [r for r in self.results if r.status == "failed"]

    @property
    def blocking(self) -> list[GateResult]:
        """Failures the human cannot allow through."""
        return [r for r in self.failed if not r.allowable]

    def feedback(self) -> str:
        """What a developer must fix (used for the automatic fix task)."""
        parts = []
        for r in self.failed:
            parts.append(f"- {r.name}: {r.summary}")
            parts.extend(f"    - {d}" for d in r.details[:20])
        return "\n".join(parts)


# ------------------------------------------------------------------------------ coverage
def parse_coverage(stack: str, output: str) -> float | None:
    """Line coverage in percent from a coverage_cmd's output (None: not reported)."""
    text = _ANSI.sub("", output)
    pattern = {"python": _PYTEST_TOTAL, "angular": _KARMA_LINES, "java": _JACOCO}.get(stack)
    if pattern is None:
        return None
    matches = pattern.findall(text)
    return float(matches[-1]) if matches else None


def coverage_gate(
    measured: dict[str, float | None],
    *,
    baseline: dict[str, float | None] | None,
    minimum: float,
    tolerance: float,
) -> GateResult:
    """New projects: every stack >= minimum. Existing repos: no drop beyond `tolerance`."""
    if not measured:
        return GateResult(name="coverage", status="skipped", summary="no test runs to measure")
    details, failed = [], False
    for stack, value in sorted(measured.items()):
        if value is None:
            details.append(f"{stack}: coverage not reported by the test run")
            continue
        if baseline is not None:
            before = baseline.get(stack)
            if before is None:
                details.append(f"{stack}: {value:.1f}% (no baseline)")
            elif value < before - tolerance:
                failed = True
                details.append(f"{stack}: {value:.1f}%, down from {before:.1f}% on the base branch")
            else:
                details.append(f"{stack}: {value:.1f}% (base branch {before:.1f}%)")
        elif value < minimum:
            failed = True
            details.append(f"{stack}: {value:.1f}% is below the {minimum:.0f}% minimum")
        else:
            details.append(f"{stack}: {value:.1f}%")
    if all(v is None for v in measured.values()):
        return GateResult(
            name="coverage", status="skipped", summary="coverage not reported", details=details
        )
    rule = "no drop vs the base branch" if baseline is not None else f"minimum {minimum:.0f}%"
    return GateResult(
        name="coverage",
        status="failed" if failed else "passed",
        summary=("coverage too low" if failed else "coverage ok") + f" ({rule})",
        details=details,
        data={"measured": measured},
    )


# ------------------------------------------------------------------------------ secrets
class SecretFinding(BaseModel):
    file: str
    line: int
    rule: str


def _json_payload(output: str, opener: str) -> Any:
    """The JSON document in a tool's (stdout + stderr) output."""
    start = output.find(opener)
    if start < 0:
        raise ValueError("no JSON in the tool output")
    return json.loads(output[start : output.rfind("]" if opener == "[" else "}") + 1])


def parse_gitleaks(output: str) -> list[SecretFinding]:
    data = _json_payload(output, "[")
    return [
        SecretFinding(
            file=str(f.get("File", "")).removeprefix("./"),
            line=int(f.get("StartLine") or 0),
            rule=str(f.get("RuleID", "")),
        )
        for f in data
    ]


def secrets_gate(findings: Iterable[SecretFinding], changed_files: Iterable[str]) -> GateResult:
    """Fail on findings in files this run changed (pre-existing secrets are reported only)."""
    changed = {f.removeprefix("./") for f in changed_files}
    new = [f for f in findings if f.file in changed]
    old = [f for f in findings if f.file not in changed]
    details = [f"{f.file}:{f.line} ({f.rule})" for f in new]
    if old:
        details.append(f"{len(old)} finding(s) in files this run did not change (not blocking)")
    return GateResult(
        name="secrets",
        status="failed" if new else "passed",
        summary=f"{len(new)} secret(s) in changed files" if new else "no secrets in changed files",
        details=details,
        allowable=False,
    )


# ------------------------------------------------------------------------------ dependencies
class Vulnerability(BaseModel):
    package: str
    version: str
    ecosystem: str
    ids: list[str]

    @property
    def key(self) -> str:
        return f"{self.ecosystem}:{self.package}@{self.version}:{','.join(sorted(self.ids))}"


def parse_osv(output: str) -> list[Vulnerability]:
    try:
        data = _json_payload(output, "{")
    except ValueError:
        return []
    vulns = []
    for result in data.get("results") or []:
        for pkg in result.get("packages") or []:
            info = pkg.get("package") or {}
            ids = [v.get("id", "") for v in pkg.get("vulnerabilities") or []]
            if ids:
                vulns.append(
                    Vulnerability(
                        package=str(info.get("name", "")),
                        version=str(info.get("version", "")),
                        ecosystem=str(info.get("ecosystem", "")),
                        ids=sorted(i for i in ids if i),
                    )
                )
    return vulns


def dependencies_gate(
    found: Iterable[Vulnerability], *, known: Iterable[str] = (), errors: Iterable[str] = ()
) -> GateResult:
    known_keys = set(known)
    vulns = list(found)
    new = [v for v in vulns if v.key not in known_keys]
    problems = list(errors)
    details = [f"{v.package} {v.version} ({v.ecosystem}): {', '.join(v.ids[:5])}" for v in new]
    if len(vulns) > len(new):
        details.append(
            f"{len(vulns) - len(new)} known vulnerability(ies) already on the base branch"
        )
    details += problems
    if new:
        status: GateStatus = "failed"
        summary = f"{len(new)} vulnerable package(s) introduced"
    elif problems and not vulns:
        status, summary = "error", "dependency scan did not complete"
    else:
        status, summary = "passed", "no new vulnerable dependencies"
    return GateResult(
        name="dependencies",
        status=status,
        summary=summary,
        details=details,
        data={"keys": sorted(v.key for v in vulns)},
    )


def tool_missing(output: str, tool: str) -> bool:
    return f"{tool}: not found" in output or f"{tool}: command not found" in output


_FAILURE_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    # pytest: "FAILED tests/test_x.py::test_y - assert ..." / "ERROR tests/test_x.py::test_z"
    "python": [re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)],
    # Maven surefire: "[ERROR]   TodoServiceTest.create:42 expected ..."
    "java": [re.compile(r"^\[ERROR\]\s+([A-Za-z_][\w$]*(?:\.[\w$]+)+):\d+", re.MULTILINE)],
    # Karma/Jasmine: "Chrome Headless 141 (Linux) AppComponent should create FAILED"
    "angular": [re.compile(r"\)\s+(.+?) FAILED\s*$", re.MULTILINE)],
}


def failing_tests(stack: str, output: str) -> list[str]:
    """Names of the failing tests in a test run's output (best effort; empty if unknown)."""
    names: set[str] = set()
    for pattern in _FAILURE_PATTERNS.get(stack, []):
        names.update(m.strip() for m in pattern.findall(output))
    return sorted(names)
