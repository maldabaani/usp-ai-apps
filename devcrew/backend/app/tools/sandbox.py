from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, Field

from app.graph.layout import LayoutEntry
from app.llm.tokens import tail_text
from app.sandbox.policy import PolicyViolation
from app.sandbox.runner import SandboxTarget
from app.sandbox.service import Sandbox
from app.tools.base import ToolError, ToolSpec

OUTPUT_TOKENS = 2000


class RunCommandArgs(BaseModel):
    command: str = Field(description="Shell command, e.g. `pytest -q tests/test_todos.py`.")
    cwd: str = Field(default=".", description="Directory relative to the project root.")
    timeout_s: int | None = Field(default=None, ge=1, description="Optional shorter timeout.")


def run_command_tool(
    sandbox: Sandbox,
    target: SandboxTarget,
    layout: Mapping[str, LayoutEntry],
    *,
    default_cwd: str = ".",
) -> ToolSpec:
    async def handler(args: RunCommandArgs) -> str:
        cwd = default_cwd if args.cwd in ("", ".") else args.cwd
        try:
            result = await sandbox.exec(
                target, layout, args.command, cwd=cwd, timeout_s=args.timeout_s
            )
        except PolicyViolation as exc:
            raise ToolError(f"command rejected by sandbox policy: {exc}") from exc
        status = f"exit_code={result.exit_code}" + (" (timed out)" if result.timed_out else "")
        return f"{status}\n{tail_text(result.output, OUTPUT_TOKENS)}"

    projects = ", ".join(f"{s} in '{e.path}'" for s, e in layout.items())
    return ToolSpec(
        "run_command",
        "Run a shell command in the project's isolated sandbox (no network; dependencies from "
        f"the manifest are installed automatically). Projects: {projects}.",
        RunCommandArgs,
        handler,
    )
