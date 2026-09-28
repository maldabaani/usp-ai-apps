"""File tools confined to one workspace directory.

Paths are relative to the workspace root; anything resolving outside it, or into .git,
is rejected. QA gets a write filter that only allows test files for its stack.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, Field, field_validator

from app.tools.base import ToolError, ToolSpec

IGNORED_DIRS = {
    ".git",
    "node_modules",
    "target",
    "dist",
    "build",
    ".angular",
    "__pycache__",
    ".venv",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
MAX_READ_LINES = 400
MAX_WRITE_BYTES = 200_000
MAX_LIST_ENTRIES = 300

TEST_PATH_PATTERNS: dict[str, tuple[str, ...]] = {
    "python": ("tests/*", "*/tests/*", "test_*.py", "*/test_*.py", "*_test.py", "*conftest.py"),
    "java": ("src/test/*", "*/src/test/*"),
    "angular": ("*.spec.ts",),
}


def is_test_path(path: str, stacks: Sequence[str]) -> bool:
    return any(
        fnmatch.fnmatch(path, pattern)
        for stack in stacks
        for pattern in TEST_PATH_PATTERNS.get(stack, ())
    )


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def resolve(self, path: str) -> Path:
        if not path or "\x00" in path:
            raise ToolError("path must be a non-empty relative path")
        candidate = PurePosixPath(path.replace("\\", "/"))
        if candidate.is_absolute():
            raise ToolError(f"absolute paths are not allowed: {path}")
        full = (self.root / candidate).resolve()
        if full != self.root and self.root not in full.parents:
            raise ToolError(f"path escapes the workspace: {path}")
        rel = full.relative_to(self.root)
        if rel.parts and rel.parts[0] == ".git":
            raise ToolError("the .git directory is off limits")
        return full

    def relative(self, full: Path) -> str:
        return full.relative_to(self.root).as_posix()

    def read(self, path: str, start_line: int = 1, max_lines: int = MAX_READ_LINES) -> str:
        full = self.resolve(path)
        if not full.is_file():
            raise ToolError(f"file not found: {path}")
        try:
            lines = full.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise ToolError(f"not a text file: {path}") from exc
        start = max(1, start_line)
        end = min(len(lines), start + min(max_lines, MAX_READ_LINES) - 1)
        body = "\n".join(f"{n:>5}| {lines[n - 1]}" for n in range(start, end + 1))
        more = (
            f"\n... ({len(lines) - end} more lines; use start_line={end + 1})"
            if end < len(lines)
            else ""
        )
        return f"{path} (lines {start}-{end} of {len(lines)})\n{body}{more}"

    def write(self, path: str, content: str) -> str:
        full = self.resolve(path)
        data = content.encode("utf-8")
        if len(data) > MAX_WRITE_BYTES:
            raise ToolError(f"content too large ({len(data)} bytes > {MAX_WRITE_BYTES})")
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(data)
        return f"wrote {path} ({content.count(chr(10)) + 1} lines)"

    def list(self, path: str = ".", depth: int = 3) -> list[str]:
        base = self.resolve(path)
        if not base.is_dir():
            raise ToolError(f"not a directory: {path}")
        entries: list[str] = []
        for dirpath, dirnames, filenames in os.walk(base):
            current = Path(dirpath)
            level = len(current.relative_to(base).parts)
            dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
            if level >= depth:
                dirnames[:] = []
            for name in sorted(filenames):
                entries.append(self.relative(current / name))
                if len(entries) >= MAX_LIST_ENTRIES:
                    return [*entries, f"... (truncated at {MAX_LIST_ENTRIES} entries)"]
        return entries


MIN_READ_LINES = 100


class ReadFileArgs(BaseModel):
    path: str = Field(description="File path relative to the project root.")
    start_line: int = Field(default=1, ge=1)
    max_lines: int = Field(default=200, ge=1, le=MAX_READ_LINES)

    @field_validator("max_lines")
    @classmethod
    def _at_least_a_page(cls, v: int) -> int:
        # Small models ask for a few lines at a time and then page through the file one call
        # per step (a real run read a 62-line doc in 16 calls). Always return a useful page.
        return max(v, MIN_READ_LINES)


class WriteFileArgs(BaseModel):
    path: str = Field(description="File path relative to the project root.")
    content: str = Field(description="The COMPLETE new file content.")


class ListDirArgs(BaseModel):
    path: str = Field(default=".", description="Directory relative to the project root.")
    depth: int = Field(default=3, ge=1, le=6)


def read_file_tool(ws: Workspace) -> ToolSpec:
    async def handler(args: ReadFileArgs) -> str:
        return ws.read(args.path, args.start_line, args.max_lines)

    return ToolSpec(
        "read_file",
        "Read a text file (numbered lines). Page with start_line.",
        ReadFileArgs,
        handler,
    )


def write_file_tool(
    ws: Workspace, allow: Callable[[str], bool] | None = None, *, note: str = ""
) -> ToolSpec:
    async def handler(args: WriteFileArgs) -> str:
        rel = ws.relative(ws.resolve(args.path))
        if allow is not None and not allow(rel):
            raise ToolError(f"writing {rel} is not allowed. {note}".strip())
        return ws.write(rel, args.content)

    description = "Create or overwrite a file with its complete content."
    if note:
        description += f" {note}"
    return ToolSpec("write_file", description, WriteFileArgs, handler)


def list_dir_tool(ws: Workspace) -> ToolSpec:
    async def handler(args: ListDirArgs) -> str:
        entries = ws.list(args.path, args.depth)
        return "\n".join(entries) if entries else "(empty)"

    return ToolSpec("list_dir", "List files under a directory.", ListDirArgs, handler)
