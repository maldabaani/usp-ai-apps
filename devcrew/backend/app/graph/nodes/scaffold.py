"""Scaffold: copy the starter template(s), commit on main, create the integration branch,
and run the dependency install step (the only step with network access).

Phase 4 adds RAG indexing of the scaffolded workspace here.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from langgraph.types import Command

from app.db.models import RunStatus
from app.events.types import EventType
from app.graph.layout import resolve_layout, sandbox_target
from app.graph.runtime import GraphDeps, NodeFn, reindex
from app.graph.state import TaskState, dump, get_design, get_plan
from app.llm.tokens import tail_text
from app.tools.git import GitRepo

TEMPLATE_META = "template.yaml"
COPY_IGNORE = shutil.ignore_patterns(
    TEMPLATE_META, "node_modules", "target", "dist", ".angular", ".venv", "__pycache__"
)


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
        layout = resolve_layout(design, deps.templates)
        root = workspace_path(deps.settings.workspaces_dir, run_id)
        repo = GitRepo(root)
        branch = integration_branch_name(run_id, state["request"])

        # Every step is idempotent so a re-run after a crash converges.
        if not await repo.is_repo():
            root.mkdir(parents=True, exist_ok=True)
            for entry in layout.values():
                shutil.copytree(
                    entry.template.path, root / entry.path, dirs_exist_ok=True, ignore=COPY_IGNORE
                )
            await repo.init()
        if not await repo.branch_exists("main"):
            # main must exist even if a template is empty: it is the PR base.
            ids = ", ".join(e.template.id for e in layout.values())
            await repo.commit_all(f"Scaffold from template {ids}", allow_empty=True)
        if not await repo.branch_exists(branch):
            await repo.checkout(branch, create_from="main")
            docs = root / "docs" / "design.md"
            docs.parent.mkdir(parents=True, exist_ok=True)
            docs.write_text(design.design_doc, encoding="utf-8")
            await repo.commit_all("Add design document", allow_empty=True)

        if deps.sandbox is not None:
            target = sandbox_target(run_id, None, root, design, layout)
            await deps.emit(
                run_id,
                EventType.TOOL_CALL,
                node="scaffold",
                tool="install_dependencies",
                args={"stacks": sorted(layout)},
            )
            for outcome in await deps.sandbox.ensure_dependencies(target, layout):
                result = outcome.result
                ok = result is None or result.ok
                await deps.emit(
                    run_id,
                    EventType.TOOL_RESULT if ok else EventType.ERROR,
                    node="scaffold",
                    tool="install_dependencies",
                    stack=outcome.stack,
                    ok=ok,
                    skipped=outcome.skipped,
                    result="" if result is None else tail_text(result.output, 500),
                    message=None if ok else f"dependency install failed for {outcome.stack}",
                )

        if deps.rag is not None:
            await reindex(deps, run_id, root, "scaffold")
            try:
                await deps.rag.sync_rules(run_id, deps.rules, layout.keys())
            except Exception as exc:
                await deps.emit(
                    run_id,
                    EventType.ERROR,
                    node="scaffold",
                    message=f"indexing rules failed: {exc}",
                )

        existing = state.get("tasks") or {}
        tasks = {t.id: dump(TaskState(id=t.id)) for t in plan.tasks if t.id not in existing}
        return Command(
            goto="schedule",
            update={
                "workspace": str(root),
                "integration_branch": branch,
                "tasks": tasks,
                "status": RunStatus.EXECUTING.value,
            },
        )

    return scaffold
