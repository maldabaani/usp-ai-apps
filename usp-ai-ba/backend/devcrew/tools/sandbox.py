from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, Field

from devcrew.graph.layout import LayoutEntry
from devcrew.llm.tokens import tail_text
from devcrew.sandbox.policy import PolicyViolation
from devcrew.sandbox.runner import SandboxTarget
from devcrew.sandbox.service import Sandbox
from devcrew.tools.base import ToolError, ToolSpec

OUTPUT_TOKENS = 2000


class RunCommandArgs(BaseModel):
    command: str = Field(description="Shell command, e.g. `pytest -q tests/test_todos.py`.")
    cwd: str = Field(default=".", description="Directory relative to the project root.")
    timeout_s: int | None = Field(default=None, ge=1, description="Optional shorter timeout.")


def _cwd_note(default_cwd: str) -> str:
    """Commands already run from inside the task's own project root (default_cwd) -- a model
    that doesn't realize that re-prefixes its command with that same directory name (e.g.
    `ruff check shop` while already inside `shop/`), which just fails with "no such file or
    directory" and burns a turn. Only worth spelling out when that root isn't "." already,
    since there's nothing to redundantly re-prefix in that case."""
    if default_cwd in ("", "."):
        return ""
    return (
        f" Commands run from inside your task's own project root ('{default_cwd}') already -- "
        "use plain paths relative to it (e.g. `pytest -q tests/...`, `ruff check .`), don't "
        "prefix a command with that directory's own name again."
    )


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
    cwd_note = _cwd_note(default_cwd)
    return ToolSpec(
        "run_command",
        "Run a shell command in the project's isolated sandbox (no network; dependencies from "
        f"the manifest are installed automatically).{cwd_note} Projects: {projects}.",
        RunCommandArgs,
        handler,
    )
