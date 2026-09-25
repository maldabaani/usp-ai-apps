"""Scaffold: copy the starter template, commit it on main, create the integration branch.

Phase 4 adds RAG indexing of the scaffolded workspace here.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from langgraph.types import Command

from app.db.models import RunStatus
from app.graph.runtime import GraphDeps, NodeFn
from app.graph.state import TaskState, dump, get_design, get_plan
from app.tools.git import GitRepo

TEMPLATE_META = "template.yaml"


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "run"


def integration_branch_name(run_id: str, request: str) -> str:
    return f"devcrew/{run_id}-{slugify(request)}"


def workspace_path(workspaces_dir: Path, run_id: str) -> Path:
    return workspaces_dir / run_id / "repo"


def make_scaffold(deps: GraphDeps) -> NodeFn:
    async def scaffold(state: dict[str, Any]) -> Command[str]:
        run_id = state["run_id"]
        design = get_design(state)
        plan = get_plan(state)
        template = deps.templates.get(design.template_id)
        root = workspace_path(deps.settings.workspaces_dir, run_id)
        repo = GitRepo(root)
        branch = integration_branch_name(run_id, state["request"])

        # Every step is idempotent so a re-run after a crash converges.
        if not await repo.is_repo():
            root.mkdir(parents=True, exist_ok=True)
            shutil.copytree(
                template.path,
                root,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(TEMPLATE_META, "node_modules", "target", ".venv"),
            )
            await repo.init()
        if not await repo.branch_exists("main"):
            # main must exist even if the template is empty: it is the PR base.
            await repo.commit_all(f"Scaffold from template {template.id}", allow_empty=True)
        if not await repo.branch_exists(branch):
            await repo.checkout(branch, create_from="main")
            docs = root / "docs" / "design.md"
            docs.parent.mkdir(parents=True, exist_ok=True)
            docs.write_text(design.design_doc, encoding="utf-8")
            await repo.commit_all("Add design document", allow_empty=True)

        existing = state.get("tasks") or {}
        tasks = {t.id: dump(TaskState(id=t.id)) for t in plan.tasks if t.id not in existing}
        return Command(
            goto="schedule",
            update={
                "workspace": str(root),
                "integration_branch": branch,
                "tasks": tasks,
                "status": RunStatus.EXECUTING,
            },
        )

    return scaffold
