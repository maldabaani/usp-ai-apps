"""Sandbox command execution contract. The Docker implementation arrives in Phase 3;
generated code is NEVER executed on the host."""

from __future__ import annotations

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


class CommandRunner(Protocol):
    async def run(
        self,
        *,
        run_id: str,
        task_id: str | None,
        workdir: Path,
        command: str,
        stack: str,
        network: bool = False,
        timeout_s: int | None = None,
    ) -> CommandResult: ...
