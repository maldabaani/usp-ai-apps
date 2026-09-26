"""Where each stack's project lives inside the workspace, and which template it came from."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.graph.state import Design, Stack
from app.sandbox.runner import SandboxTarget
from app.tools.catalog import TemplateInfo, TemplatesCatalog


@dataclass(frozen=True)
class LayoutEntry:
    stack: str
    path: str  # relative to the workspace root; "." for single-stack projects
    template: TemplateInfo


def resolve_layout(design: Design, templates: TemplatesCatalog) -> dict[str, LayoutEntry]:
    if design.existing_projects:
        return {
            p.stack: LayoutEntry(
                p.stack,
                p.path,
                TemplateInfo(
                    id=f"existing-{p.stack}",
                    stack=p.stack,
                    description="existing repository",
                    install_cmd=p.install_cmd,
                    build_cmd=p.build_cmd,
                    test_cmd=p.test_cmd,
                    coverage_cmd=p.coverage_cmd,
                ),
            )
            for p in design.existing_projects
        }
    if design.stack is Stack.MIXED:
        entries = [(templates.get(c.template_id), c.path) for c in design.components]
    else:
        entries = [(templates.get(design.template_id), ".")]
    return {t.stack: LayoutEntry(t.stack, path, t) for t, path in entries}


def image_stack(design: Design) -> str:
    """Sandbox image family: one per stack, or the combined image for mixed designs."""
    return design.stack.value


def entry_for(layout: dict[str, LayoutEntry], stack: str) -> LayoutEntry:
    try:
        return layout[stack]
    except KeyError:
        raise KeyError(f"the design has no {stack} project (layout: {sorted(layout)})") from None


def sandbox_target(
    run_id: str,
    task_id: str | None,
    workdir: Path,
    design: Design,
    layout: dict[str, LayoutEntry],
) -> SandboxTarget:
    return SandboxTarget(
        run_id=run_id,
        task_id=task_id,
        workdir=workdir,
        image_stack=image_stack(design),
        project_paths={s: e.path for s, e in layout.items()},
    )
