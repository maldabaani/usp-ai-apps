"""Git operations on a run workspace.

Git runs on the host but never executes repository content: hooks are disabled and
fsmonitor is off for every invocation.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Mapping
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
# Minimal environment for git (no inherited credentials). Proxy/CA variables are passed through
# so pushes work behind a corporate proxy.
GIT_BASE_ENV: dict[str, str] = {
    "GIT_TERMINAL_PROMPT": "0",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
    "HOME": "/tmp",
    **{
        k: v
        for k in (
            "HTTPS_PROXY",
            "https_proxy",
            "NO_PROXY",
            "no_proxy",
            "SSL_CERT_FILE",
            "GIT_SSL_CAINFO",
        )
        if (v := os.environ.get(k))
    },
}


CONFLICT_MARKER_RE = re.compile(r"^(<{7}|={7}|>{7})( |$)", re.MULTILINE)
UNMERGED_CODES = ("UU", "AA", "DD", "AU", "UA", "DU", "UD")

_repo_locks: dict[str, asyncio.Lock] = {}


def repo_lock(root: Path) -> asyncio.Lock:
    """Serializes operations that touch the shared repository state of a run: worktree
    add/remove, merges into the integration branch, and re-indexing after a merge.
    (Commits inside separate worktrees need no lock: each has its own index and branch.)"""
    key = str(root.resolve())
    lock = _repo_locks.get(key)
    if lock is None:
        lock = _repo_locks[key] = asyncio.Lock()
    return lock


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

    async def run(
        self, *args: str, check: bool = True, extra_env: Mapping[str, str] | None = None
    ) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *SAFE_CONFIG,
            *args,
            cwd=self.root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**GIT_BASE_ENV, **(extra_env or {})},
        )
        out, err = await proc.communicate()
        if check and proc.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {err.decode(errors='replace').strip()}")
        return out.decode(errors="replace")

    async def run_bytes(self, *args: str) -> bytes:
        """Like run(), but returns raw stdout (file contents may be binary)."""
        proc = await asyncio.create_subprocess_exec(
            "git",
            *SAFE_CONFIG,
            *args,
            cwd=self.root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=GIT_BASE_ENV,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {err.decode(errors='replace').strip()}")
        return out

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

    async def unmerged_files(self) -> list[str]:
        return [
            line[3:]
            for line in (await self.run("status", "--porcelain")).splitlines()
            if line[:2] in UNMERGED_CODES
        ]

    # ------------------------------------------------------------------ worktrees (Phase 5)
    async def worktree_add(self, path: Path, branch: str, base: str) -> None:
        """Create `path` as a worktree on a fresh `branch` starting at `base`."""
        path.parent.mkdir(parents=True, exist_ok=True)
        await self.run("worktree", "add", "-q", "-B", branch, str(path), base)

    async def worktree_remove(self, path: Path) -> None:
        await self.run("worktree", "remove", "--force", str(path), check=False)
        await self.run("worktree", "prune", check=False)

    async def worktrees(self) -> list[Path]:
        out = await self.run("worktree", "list", "--porcelain")
        return [Path(line[9:]) for line in out.splitlines() if line.startswith("worktree ")]

    async def is_worktree_of(self, path: Path, main: Path) -> bool:
        trees = await GitRepo(main).worktrees()
        return await asyncio.to_thread(lambda: path.resolve() in {p.resolve() for p in trees})

    async def merge_in(self, ref: str) -> list[str]:
        """Merge `ref` into the current branch without committing. Returns conflicted files
        (empty when the merge applied cleanly; the caller commits)."""
        await self.run("merge", "--no-commit", "--no-ff", ref, check=False)
        return await self.unmerged_files()

    async def in_merge(self) -> bool:
        return (
            await self.run("rev-parse", "-q", "--verify", "MERGE_HEAD", check=False)
        ).strip() != ""

    def files_with_markers(self, paths: list[str]) -> list[str]:
        found = []
        for rel in paths:
            file = self.root / rel
            if file.is_file():
                text = file.read_text(encoding="utf-8", errors="replace")
                if CONFLICT_MARKER_RE.search(text):
                    found.append(rel)
        return found

    async def squash_merge(self, branch: str, into: str, message: str) -> MergeResult:
        """Merge `branch` into `into` as exactly one commit (one commit per task)."""
        await self.checkout(into)
        proc = await self.run("merge", "--squash", "--no-commit", branch, check=False)
        conflicts = await self.unmerged_files()
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
