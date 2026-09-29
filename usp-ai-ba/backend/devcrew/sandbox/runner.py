"""Sandbox execution contract. Generated code is NEVER executed on the host."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel


class CommandResult(BaseModel):
    exit_code: int
    output: str
    timed_out: bool = False
    duration_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass(frozen=True)
class SandboxTarget:
    """One container per (run, task worktree); task_id None = the run's integration container."""

    run_id: str
    task_id: str | None
    workdir: Path  # host path, mounted at /workspace
    image_stack: str  # python | java | angular | mixed
    # stack -> project path relative to /workspace (drives dependency volumes)
    project_paths: Mapping[str, str] = field(default_factory=dict)


class CommandRunner(Protocol):
    async def run(
        self,
        target: SandboxTarget,
        command: str,
        *,
        cwd: str = ".",
        timeout_s: int | None = None,
        network: bool = False,
    ) -> CommandResult: ...

    async def release(self, run_id: str, task_id: str | None) -> None: ...

    async def cleanup_run(self, run_id: str) -> None: ...
