"""Benchmark metrics, test-output parsing and the JSON / markdown reports."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.benchmark.tasks import BenchStack

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PYTEST_COUNT = re.compile(r"(\d+) (passed|failed|errors?)\b")
MAVEN_SUMMARY = re.compile(r"Tests run: (\d+), Failures: (\d+), Errors: (\d+), Skipped: (\d+)")
KARMA_EXECUTED = re.compile(r"Executed (\d+) of (\d+)(.*)")
KARMA_FAILED = re.compile(r"\((\d+) FAILED\)")


class SuiteCounts(BaseModel):
    passed: int = 0
    failed: int = 0
    reported: bool = False  # the runner printed a summary we could parse


def parse_test_output(stack: BenchStack, output: str) -> SuiteCounts:
    """Pass/fail counts from pytest, Maven Surefire or Karma console output.

    A build or collection failure usually prints no summary: that yields reported=False and zero
    passes, and the caller scores it against the expected number of hidden tests.
    """
    text = ANSI.sub("", output)
    if stack is BenchStack.PYTHON:
        summaries = [
            line for line in text.splitlines() if PYTEST_COUNT.search(line) and " in " in line
        ]
        if not summaries:
            return SuiteCounts()
        counts = {kind: int(n) for n, kind in PYTEST_COUNT.findall(summaries[-1])}
        failed = counts.get("failed", 0) + counts.get("error", 0) + counts.get("errors", 0)
        return SuiteCounts(passed=counts.get("passed", 0), failed=failed, reported=True)
    if stack is BenchStack.JAVA:
        matches = MAVEN_SUMMARY.findall(text)
        if not matches:
            return SuiteCounts()
        run, failures, errors, skipped = (int(x) for x in matches[-1])
        bad = failures + errors
        return SuiteCounts(passed=max(run - bad - skipped, 0), failed=bad, reported=True)
    matches = KARMA_EXECUTED.findall(text)
    if not matches:
        return SuiteCounts()
    executed, _total, rest = matches[-1]
    failed_match = KARMA_FAILED.search(rest)
    failed = int(failed_match.group(1)) if failed_match else 0
    return SuiteCounts(passed=max(int(executed) - failed, 0), failed=failed, reported=True)


class HiddenTestResult(BaseModel):
    ran: bool
    passed: int = 0
    failed: int = 0
    expected: int
    pass_rate: float = 0.0
    command: str | None = None
    exit_code: int | None = None
    reported: bool = False
    detail: str | None = None  # why tests did not run, or the output tail

    @classmethod
    def scored(
        cls, counts: SuiteCounts, *, expected: int, command: str, exit_code: int, output: str
    ) -> HiddenTestResult:
        # Score against the known suite size: tests that never ran (compile error, missing
        # component) count as failures.
        total = max(expected, counts.passed + counts.failed)
        return cls(
            ran=True,
            passed=counts.passed,
            failed=total - counts.passed,
            expected=expected,
            pass_rate=round(counts.passed / total, 4) if total else 0.0,
            command=command,
            exit_code=exit_code,
            reported=counts.reported,
            detail=ANSI.sub("", output)[-3000:],
        )


class TokenCounts(BaseModel):
    input: int = 0
    output: int = 0
    calls: int = 0
    by_role: dict[str, dict[str, int]] = Field(default_factory=dict)


class TaskResult(BaseModel):
    task_id: str
    stack: str
    difficulty: int
    title: str
    run_id: str
    status: str  # final run status, or "timeout"
    error: str | None = None
    wall_time_s: float
    hidden: HiddenTestResult
    dev_iterations: int = 0
    escalations: int = 0
    questions_to_human: int = 0
    questions_to_agents: int = 0
    coordinator_calls: int = 0
    planned_tasks: int = 0
    merged_tasks: int = 0
    failed_tasks: int = 0
    integration_tests_passed: bool | None = None
    tokens: TokenCounts = Field(default_factory=TokenCounts)
    workspace: str | None = None


class ConfigSummary(BaseModel):
    tasks: int
    completed: int
    hidden_passed: int
    hidden_total: int
    mean_pass_rate: float
    dev_iterations: int
    escalations: int
    questions: int
    wall_time_s: float
    tokens_in: int
    tokens_out: int
    pass_rate_by_stack: dict[str, float]

    @classmethod
    def of(cls, results: list[TaskResult]) -> ConfigSummary:
        by_stack: dict[str, list[float]] = {}
        for r in results:
            by_stack.setdefault(r.stack, []).append(r.hidden.pass_rate)
        return cls(
            tasks=len(results),
            completed=sum(1 for r in results if r.status == "completed"),
            hidden_passed=sum(r.hidden.passed for r in results),
            hidden_total=sum(
                max(r.hidden.expected, r.hidden.passed + r.hidden.failed) for r in results
            ),
            mean_pass_rate=_mean([r.hidden.pass_rate for r in results]),
            dev_iterations=sum(r.dev_iterations for r in results),
            escalations=sum(r.escalations for r in results),
            questions=sum(r.questions_to_human + r.questions_to_agents for r in results),
            wall_time_s=round(sum(r.wall_time_s for r in results), 1),
            tokens_in=sum(r.tokens.input for r in results),
            tokens_out=sum(r.tokens.output for r in results),
            pass_rate_by_stack={s: _mean(v) for s, v in sorted(by_stack.items())},
        )


class ConfigResult(BaseModel):
    label: str
    models_config: str
    models: dict[str, str]  # role -> model name
    tasks: list[TaskResult] = Field(default_factory=list)
    summary: ConfigSummary | None = None


class BenchmarkReport(BaseModel):
    started_at: datetime
    finished_at: datetime | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    configs: list[ConfigResult] = Field(default_factory=list)


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def _pct(rate: float) -> str:
    return f"{rate * 100:.0f}%"


def _tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def render_markdown(report: BenchmarkReport) -> str:
    lines = [f"# DevCrew benchmark — {report.started_at:%Y-%m-%d %H:%M:%S} UTC", ""]
    if report.settings:
        lines.append(
            "Settings: " + ", ".join(f"`{k}={v}`" for k, v in sorted(report.settings.items()))
        )
        lines.append("")
    for config in report.configs:
        models = ", ".join(f"{role}: `{model}`" for role, model in sorted(config.models.items()))
        lines += [
            f"## {config.label}",
            "",
            f"Models config `{config.models_config}` — {models}",
            "",
        ]
        lines += [
            "| Task | Stack | Diff. | Status | Hidden tests | Dev iter. | Escal. | Questions "
            "(human/agents) | Wall time | Tokens in/out |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in config.tasks:
            hidden = (
                f"{r.hidden.passed}/{max(r.hidden.expected, r.hidden.passed + r.hidden.failed)} "
                f"({_pct(r.hidden.pass_rate)})"
                if r.hidden.ran
                else "not run"
            )
            lines.append(
                f"| {r.task_id} | {r.stack} | {r.difficulty} | {r.status} | {hidden} | "
                f"{r.dev_iterations} | {r.escalations} | {r.questions_to_human}/"
                f"{r.questions_to_agents} | {r.wall_time_s / 60:.1f} min | "
                f"{_tokens(r.tokens.input)}/{_tokens(r.tokens.output)} |"
            )
        s = config.summary
        if s is not None:
            stacks = ", ".join(f"{k} {_pct(v)}" for k, v in s.pass_rate_by_stack.items())
            lines += [
                "",
                f"**{s.completed}/{s.tasks} runs completed; hidden tests {s.hidden_passed}/"
                f"{s.hidden_total} passed (mean pass rate {_pct(s.mean_pass_rate)}; {stacks}).** "
                f"{s.dev_iterations} dev iterations, {s.escalations} escalations, {s.questions} "
                f"questions, {s.wall_time_s / 60:.1f} min, {_tokens(s.tokens_in)} input / "
                f"{_tokens(s.tokens_out)} output tokens.",
            ]
        failures = [r for r in config.tasks if r.error or not r.hidden.ran]
        if failures:
            lines += ["", "Notes:"]
            for r in failures:
                note = r.error or r.hidden.detail or "hidden tests not run"
                lines.append(f"- `{r.task_id}`: {note.strip().splitlines()[-1][:300]}")
        lines.append("")
    summaries = [(c.label, c.summary) for c in report.configs if c.summary is not None]
    if len(summaries) > 1:
        lines += [
            "## Comparison",
            "",
            "| Config | Completed | Mean pass rate | Hidden passed | Dev iter. | Escal. | "
            "Questions | Wall time | Tokens in/out |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for label, s in summaries:
            assert s is not None
            lines.append(
                f"| {label} | {s.completed}/{s.tasks} | {_pct(s.mean_pass_rate)} | "
                f"{s.hidden_passed}/{s.hidden_total} | {s.dev_iterations} | {s.escalations} | "
                f"{s.questions} | {s.wall_time_s / 60:.1f} min | {_tokens(s.tokens_in)}/"
                f"{_tokens(s.tokens_out)} |"
            )
        lines.append("")
    return "\n".join(lines)


def write_report(report: BenchmarkReport, out_dir: Path) -> tuple[Path, Path]:
    """Write <timestamp>.json and <timestamp>.md; returns both paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = report.started_at.strftime("%Y%m%dT%H%M%SZ")
    json_path, md_path = out_dir / f"{stem}.json", out_dir / f"{stem}.md"
    json_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path
