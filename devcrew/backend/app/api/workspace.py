"""Read-only views of a run's workspace: files and diffs (served from git, never executed)."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import ContainerDep
from app.api.schemas import DiffResponse, FileContent, FileEntry, FileList
from app.services.run_manager import RunNotFoundError
from app.tools.git import GitError, GitRepo

router = APIRouter(prefix="/runs/{run_id}", tags=["workspace"])

MAX_FILE_BYTES = 500_000
MAX_DIFF_CHARS = 400_000


async def _repo_state(container: Any, run_id: str) -> tuple[GitRepo, dict[str, Any]]:
    try:
        await container.manager.get(run_id)
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"run {run_id} not found") from None
    state = await container.manager.state(run_id)
    workspace = state.get("workspace")
    if not workspace or not (Path(workspace) / ".git").exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "the workspace has not been created yet")
    return GitRepo(Path(workspace)), state


def _allowed_refs(state: dict[str, Any]) -> dict[str, str]:
    """Only refs the run owns may be read (no arbitrary git revision syntax)."""
    base = state.get("base_branch") or "main"
    refs = {"main": base, "base": base}  # "main" is the UI's name for the PR base
    if state.get("integration_branch"):
        refs["integration"] = state["integration_branch"]
        refs[state["integration_branch"]] = state["integration_branch"]
    for task_id, task in (state.get("tasks") or {}).items():
        if task.get("branch"):
            refs[f"task:{task_id}"] = task["branch"]
            refs[task["branch"]] = task["branch"]
    return refs


def _resolve_ref(state: dict[str, Any], ref: str | None) -> str:
    refs = _allowed_refs(state)
    key = ref or ("integration" if "integration" in refs else "main")
    if key not in refs:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown ref {ref!r}")
    return refs[key]


def _safe_path(path: str) -> str:
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0] == ".git":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid path")
    return str(p)


@router.get("/files", response_model=FileList)
async def list_files(
    run_id: str,
    container: ContainerDep,
    ref: Annotated[str | None, Query(description="integration | main | task:<id>")] = None,
) -> FileList:
    repo, state = await _repo_state(container, run_id)
    branch = _resolve_ref(state, ref)
    out = await repo.run("ls-tree", "-r", "-l", "--full-tree", branch)
    files = []
    for line in out.splitlines():
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) >= 4 and parts[1] == "blob":
            files.append(FileEntry(path=path, size=int(parts[3]) if parts[3].isdigit() else 0))
    return FileList(ref=branch, files=files)


@router.get("/files/{path:path}", response_model=FileContent)
async def read_file(
    run_id: str,
    path: str,
    container: ContainerDep,
    ref: Annotated[str | None, Query()] = None,
) -> FileContent:
    repo, state = await _repo_state(container, run_id)
    branch = _resolve_ref(state, ref)
    rel = _safe_path(path)
    size_out = await repo.run("cat-file", "-s", f"{branch}:{rel}", check=False)
    if not size_out.strip().isdigit():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{rel} not found on {branch}")
    size = int(size_out.strip())
    raw = await repo.run_bytes("show", f"{branch}:{rel}")
    binary = b"\x00" in raw[:8192]
    content = "" if binary else raw[:MAX_FILE_BYTES].decode("utf-8", errors="replace")
    return FileContent(
        ref=branch, path=rel, content=content, truncated=size > MAX_FILE_BYTES, binary=binary
    )


@router.get("/diff", response_model=DiffResponse)
async def diff(
    run_id: str,
    container: ContainerDep,
    task_id: Annotated[str | None, Query()] = None,
) -> DiffResponse:
    """A task's changes (task branch vs. where it forked from the integration branch), or the
    whole run's changes (integration branch vs. the scaffold on main)."""
    repo, state = await _repo_state(container, run_id)
    run_base = state.get("base_branch") or "main"
    integration = state.get("integration_branch") or run_base
    if task_id is None:
        base, head = run_base, integration
    else:
        task = (state.get("tasks") or {}).get(task_id)
        if task is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"task {task_id} not found")
        if not task.get("branch"):
            return DiffResponse(task_id=task_id, base=integration, head="", diff="")
        base, head = integration, task["branch"]
    try:
        text = await repo.diff(base, head)
    except GitError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return DiffResponse(
        task_id=task_id,
        base=base,
        head=head,
        diff=text[:MAX_DIFF_CHARS],
        truncated=len(text) > MAX_DIFF_CHARS,
    )
