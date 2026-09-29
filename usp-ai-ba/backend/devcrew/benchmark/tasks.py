"""Benchmark task definitions (benchmarks/tasks/*.yaml) and their hidden acceptance tests."""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class BenchStack(StrEnum):
    PYTHON = "python"
    JAVA = "java"
    ANGULAR = "angular"


# Run from the stack's project directory after the hidden files were copied into it.
HIDDEN_TEST_COMMANDS: dict[BenchStack, str] = {
    BenchStack.PYTHON: "pytest -q -p no:cacheprovider tests_hidden",
    BenchStack.JAVA: "mvn test -Dtest='Hidden*Test' -Dsurefire.failIfNoSpecifiedTests=false",
    BenchStack.ANGULAR: "npx ng test --watch=false --browsers=ChromeHeadless --include=src/hidden",
}

# How to count the test cases in a hidden suite (the expected total, even if nothing compiles).
TEST_CASE_PATTERNS: dict[BenchStack, tuple[str, re.Pattern[str]]] = {
    BenchStack.PYTHON: ("*.py", re.compile(r"^\s*(?:async\s+)?def test_", re.MULTILINE)),
    BenchStack.JAVA: ("*.java", re.compile(r"^\s*@Test\b", re.MULTILINE)),
    BenchStack.ANGULAR: ("*.spec.ts", re.compile(r"^\s*it\(", re.MULTILINE)),
}


class BenchmarkTask(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    stack: BenchStack
    difficulty: int = Field(ge=1, le=4)
    title: str
    request: str
    hidden_test_cmd: str | None = None
    hidden_dir: Path
    expected_tests: int = Field(ge=1)

    @property
    def test_command(self) -> str:
        return self.hidden_test_cmd or HIDDEN_TEST_COMMANDS[self.stack]


class TaskFileError(ValueError):
    pass


def count_test_cases(stack: BenchStack, hidden_dir: Path) -> int:
    glob, pattern = TEST_CASE_PATTERNS[stack]
    return sum(
        len(pattern.findall(path.read_text(encoding="utf-8")))
        for path in hidden_dir.rglob(glob)
        if path.is_file()
    )


def load_tasks(
    tasks_dir: Path,
    hidden_root: Path,
    *,
    stacks: Iterable[str] = (),
    ids: Iterable[str] = (),
) -> list[BenchmarkTask]:
    """Load every task file, validate it against its hidden suite, then filter."""
    tasks: list[BenchmarkTask] = []
    for path in sorted(tasks_dir.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(doc, dict) or "stack" not in doc:
            raise TaskFileError(f"{path.name}: expected a mapping with 'stack' and 'tasks'")
        stack = BenchStack(doc["stack"])
        for raw in doc.get("tasks") or []:
            hidden_dir = hidden_root / str(raw.get("id", ""))
            if not hidden_dir.is_dir():
                raise TaskFileError(f"{path.name}: no hidden tests at {hidden_dir}")
            expected = count_test_cases(stack, hidden_dir)
            if expected == 0:
                raise TaskFileError(f"{path.name}: {hidden_dir} contains no {stack} test cases")
            tasks.append(
                BenchmarkTask.model_validate(
                    {**raw, "stack": stack, "hidden_dir": hidden_dir, "expected_tests": expected}
                )
            )
    seen: set[str] = set()
    for task in tasks:
        if task.id in seen:
            raise TaskFileError(f"duplicate task id {task.id}")
        seen.add(task.id)

    wanted_stacks, wanted_ids = set(stacks), set(ids)
    unknown = wanted_ids - seen
    if unknown:
        raise TaskFileError(f"unknown task id(s): {', '.join(sorted(unknown))}")
    return [
        t
        for t in tasks
        if (not wanted_stacks or t.stack in wanted_stacks)
        and (not wanted_ids or t.id in wanted_ids)
    ]
