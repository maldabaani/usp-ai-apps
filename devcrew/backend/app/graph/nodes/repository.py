"""Existing repositories: clone, detect projects, summarize and index before planning."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from langgraph.types import Command

from app.db.models import RunStatus
from app.events.types import EventType
from app.github.delivery import DeliveryError
from app.graph.nodes.scaffold import workspace_path
from app.graph.runtime import GraphDeps, NodeFn, reindex
from app.graph.state import Design, ExistingProject, ModuleContract, Plan, Stack
from app.repo.detect import DetectionError, detect_projects, repository_map
from app.tools.git import GitError, GitRepo

NODE = "prepare_repo"
README_CHARS = 4000
QUICK_DESIGN_DOC = (
    "Quick fix run: no design step. Tasks change the existing code in place, following its "
    "current structure and conventions."
)


def _readme(root: Path) -> str:
    for name in ("README.md", "README.rst", "README.txt", "README", "readme.md"):
        path = root / name
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")[:README_CHARS]
    return ""


def repo_projects(state: dict[str, Any]) -> list[ExistingProject]:
    info = state.get("repo_info") or {}
    return [ExistingProject.model_validate(p) for p in info.get("projects") or []]


def repo_stack(projects: list[ExistingProject]) -> Stack:
    return Stack.MIXED if len(projects) > 1 else Stack(projects[0].stack)


def quick_design(state: dict[str, Any], plan: Plan) -> Design:
    """Quick-fix runs skip the Architect: a minimal design that carries the detected layout."""
    projects = repo_projects(state)
    return Design(
        stack=repo_stack(projects),
        template_id="existing",
        project_structure=sorted({f for t in plan.tasks for f in t.target_files}),
        modules=[_module(p) for p in projects],
        key_decisions=["Quick fix: keep the existing structure and conventions."],
        design_doc=QUICK_DESIGN_DOC,
        existing_projects=projects,
    )


def _module(project: ExistingProject) -> ModuleContract:
    """One module per project: tasks code against what is already there."""
    return ModuleContract(
        name=f"{project.stack} project",
        path=project.path,
        responsibility="Existing code, changed in place by the tasks.",
        interface="Keep existing public interfaces unless a task says otherwise.",
    )


async def _fail(deps: GraphDeps, run_id: str, reason: str) -> Command[str]:
    """Infrastructure problems go straight to the human (retry / abort), not to the LLM."""
    await deps.emit(run_id, EventType.ERROR, node=NODE, message=reason)
    return Command(
        goto="escalate",
        update={"escalation": {"node": NODE, "reason": reason}, "errors": [f"{NODE}: {reason}"]},
    )


def make_prepare_repo(deps: GraphDeps) -> NodeFn:
    async def prepare_repo(state: dict[str, Any]) -> Command[str]:
        run_id = state["run_id"]
        owner, _, name = str(state["repo_target"]).partition("/")
        if deps.github is None:
            return await _fail(
                deps,
                run_id,
                "working on an existing repository needs GitHub access: set GITHUB_TOKEN and "
                "GITHUB_DELIVERY_ENABLED=true, restart the backend, then retry",
            )
        root = workspace_path(deps.settings.workspaces_dir, run_id)
        repo = GitRepo(root)
        try:
            base = state.get("base_branch") or await deps.github.default_branch(owner, name)
            if not await repo.is_repo():
                if root.exists():
                    shutil.rmtree(root)  # half-finished clone from an interrupted attempt
                await deps.emit(
                    run_id,
                    EventType.TOOL_CALL,
                    node=NODE,
                    tool="clone",
                    args={"target": f"{owner}/{name}@{base}"},
                )
                await deps.github.clone(owner, name, base, root)
            await repo.checkout(base)
        except (DeliveryError, GitError) as exc:
            return await _fail(deps, run_id, f"cannot clone {owner}/{name}: {exc}")

        try:
            detection = await asyncio.to_thread(detect_projects, root)
        except DetectionError as exc:
            return await _fail(deps, run_id, str(exc))
        files = [f for f in (await repo.run("ls-files")).splitlines() if f]
        info = {
            "base_branch": base,
            "projects": [p.model_dump() for p in detection.projects],
            "source": detection.source,
            "notes": detection.notes,
            "file_count": len(files),
            "tree": repository_map(files),
            "readme": _readme(root),
            "commit": await repo.head(),
        }
        summary = ", ".join(f"{p.stack} at {p.path}" for p in detection.projects)
        await deps.emit(
            run_id,
            EventType.TOOL_RESULT,
            node=NODE,
            tool="detect_projects",
            ok=True,
            result=f"{summary} ({detection.source}); {len(files)} tracked files on {base}",
            projects=info["projects"],
        )
        if deps.rag is not None:
            await reindex(deps, run_id, root, NODE)
            try:
                await deps.rag.sync_rules(run_id, deps.rules, [p.stack for p in detection.projects])
            except Exception as exc:
                await deps.emit(
                    run_id, EventType.ERROR, node=NODE, message=f"indexing rules failed: {exc}"
                )
        return Command(
            goto="planner",
            update={
                "workspace": str(root),
                "base_branch": base,
                "repo_info": info,
                "status": RunStatus.PLANNING.value,
            },
        )

    return prepare_repo
