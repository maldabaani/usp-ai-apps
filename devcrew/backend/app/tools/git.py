"""Git operations on a run workspace.

Git runs on the host but never executes repository content: hooks are disabled and
fsmonitor is off for every invocation.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from app.tools.base import ToolError, ToolSpec

SAFE_CONFIG = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "user.name=DevCrew",
    "-c",
    "user.email=devcrew@localhost",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "init.defaultBranch=main",
)
MAX_DIFF_CHARS = 60_000


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class MergeResult:
    ok: bool
    commit: str | None
    conflicts: list[str]


class GitRepo:
    def __init__(self, root: Path) -> None:
        self.root = root

    async def run(self, *args: str, check: bool = True) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *SAFE_CONFIG,
            *args,
            cwd=self.root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin:/usr/local/bin"},
        )
        out, err = await proc.communicate()
        if check and proc.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {err.decode(errors='replace').strip()}")
        return out.decode(errors="replace")

    async def is_repo(self) -> bool:
        return (self.root / ".git").is_dir()

    async def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        await self.run("init", "-q", "-b", "main")

    async def commit_all(self, message: str, *, allow_empty: bool = False) -> str | None:
        """Stage everything and commit. Returns the new sha, or None if nothing changed."""
        await self.run("add", "-A")
        if not (await self.run("status", "--porcelain")).strip():
            if not allow_empty:
                return None
            await self.run("commit", "-q", "--allow-empty", "-m", message)
            return await self.head()
        await self.run("commit", "-q", "-m", message)
        return await self.head()

    async def head(self, ref: str = "HEAD") -> str:
        return (await self.run("rev-parse", ref)).strip()

    async def current_branch(self) -> str:
        return (await self.run("rev-parse", "--abbrev-ref", "HEAD")).strip()

    async def branch_exists(self, name: str) -> bool:
        out = await self.run("rev-parse", "--verify", "--quiet", f"refs/heads/{name}", check=False)
        return bool(out.strip())

    async def checkout(self, branch: str, *, create_from: str | None = None) -> None:
        if create_from is not None:
            await self.run("checkout", "-q", "-B", branch, create_from)
        else:
            await self.run("checkout", "-q", branch)

    async def diff(self, base: str, head: str = "HEAD", paths: list[str] | None = None) -> str:
        args = ["diff", "--no-color", "--no-ext-diff", f"{base}...{head}"]
        if paths:
            args += ["--", *paths]
        return await self.run(*args)

    async def changed_files(self, base: str, head: str = "HEAD") -> list[str]:
        out = await self.run("diff", "--name-only", f"{base}...{head}")
        return [line for line in out.splitlines() if line]

    async def squash_merge(self, branch: str, into: str, message: str) -> MergeResult:
        """Merge `branch` into `into` as exactly one commit (one commit per task)."""
        await self.checkout(into)
        proc = await self.run("merge", "--squash", "--no-commit", branch, check=False)
        conflicts = [
            line[3:]
            for line in (await self.run("status", "--porcelain")).splitlines()
            if line[:2] in ("UU", "AA", "DD", "AU", "UA", "DU", "UD")
        ]
        if conflicts or "CONFLICT" in proc:
            await self.run("reset", "-q", "--hard", "HEAD")
            return MergeResult(ok=False, commit=None, conflicts=conflicts)
        sha = await self.commit_all(message)
        return MergeResult(ok=True, commit=sha, conflicts=[])


class GitDiffArgs(BaseModel):
    path: str | None = Field(default=None, description="Optional file to restrict the diff to.")


def git_diff_tool(repo: GitRepo, base: str, head: str) -> ToolSpec:
    async def handler(args: GitDiffArgs) -> str:
        try:
            diff = await repo.diff(base, head, [args.path] if args.path else None)
        except GitError as exc:
            raise ToolError(str(exc)) from exc
        if not diff.strip():
            return "(no changes)"
        if len(diff) > MAX_DIFF_CHARS:
            return diff[:MAX_DIFF_CHARS] + "\n... [diff truncated; pass path= to see one file]"
        return diff

    return ToolSpec(
        "git_diff", "Show the task's diff against the integration branch.", GitDiffArgs, handler
    )
