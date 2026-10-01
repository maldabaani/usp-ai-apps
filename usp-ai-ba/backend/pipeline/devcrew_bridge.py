"""Translation bridge: StoryForge's approved epic -> DevCrew's Plan+Design,
skipping DevCrew's own Planner AND Architect entirely (see the merge plan's
Phase 6, and the "Revision note" recorded there -- the user explicitly
asked for execution to start straight from Developer/Reviewer/QA, with no
DevCrew-side plan/design approval pause; StoryForge's own review step is
the sole "approve what to build" checkpoint).

One epic -> one Plan: a single synthetic UserStory carrying the epic's own
acceptance criteria, and one PlanTask per dev_task+paired-unit_test_task
(the unit test's authored scenarios are folded into that task's
description as required verification points, not discarded -- QA still
writes and runs its own tests, but is pointed at these specific scenarios
too). `depends_on=[]`/`target_files=[]` always: StoryForge declares neither
today, and this pipeline's own generation prompt already forbids
fabricating file paths -- the same rule applies here.

The Design is built separately, by build_design below, once DevCrew's own
prepare_repo node has detected the target repo's real project layout
(repo_info) -- see devcrew/graph/nodes/repository.py's new conditional
branch, which calls build_design via a deferred import to avoid a
StoryForge<->DevCrew import cycle (pipeline/ imports devcrew/ here, so
devcrew/ cannot import pipeline/ at module level in return).

StoryForge's own SDD-generation prompt is written entirely in Spring
Boot/Angular terms (see prompts/system_prompt.py), so a dev_task's
`affected_components.backend` text never actually names a language -- the
earlier build_plan() stamps every backend/middleware-affected task's stack
as the literal string "java", which here really just means "some
non-frontend backend change, language undetermined". build_design
reconciles that placeholder against the target repo's own real, detected
stack (see _reconcile_task_stacks below) once prepare_repo knows it --
e.g. a Python-only repo -- so non-Java target repos are supported without
StoryForge needing to know or guess the target repo's language up front.
"""
from __future__ import annotations

from typing import Any

from devcrew.graph.nodes.repository import quick_design
from devcrew.graph.state import Design, ExistingProject, ModuleContract, Plan, PlanTask, UserStory

_LEGACY_FRONTEND_MARKERS = (
    "jquery",
    "legacy js",
    "legacy javascript",
    ".jsp",
    "html5 frontend",
    "legacy frontend",
)
_ANGULAR_MARKERS = ("angular",)


class UnsupportedStackError(ValueError):
    """A dev task's affected_components has no DevCrew-compatible stack
    (python/java/angular) -- e.g. legacy JS/jQuery-only. Surfaced to the
    human at dispatch time (see api/routers/devcrew_bridge.py), never
    silently mismapped to the wrong stack."""


def _is_na(value: str | None) -> bool:
    return not value or value.strip().upper() in ("N/A", "NONE", "")


def _derive_stack(affected: dict[str, Any]) -> str:
    backend = affected.get("backend")
    middleware = affected.get("middleware")
    frontend = affected.get("frontend")
    if not _is_na(backend) or not _is_na(middleware):
        return "java"
    if not _is_na(frontend):
        lowered = frontend.lower()
        is_legacy = any(marker in lowered for marker in _LEGACY_FRONTEND_MARKERS)
        is_angular = any(marker in lowered for marker in _ANGULAR_MARKERS)
        if is_legacy and not is_angular:
            raise UnsupportedStackError(
                "affected_components.frontend describes the legacy JS/jQuery layer, which has no "
                f"DevCrew-compatible stack (python/java/angular): {frontend!r}"
            )
        return "angular"
    database = affected.get("database")
    if not _is_na(database):
        return "java"  # a Spring Boot microservice owns its own persistence layer
    raise UnsupportedStackError(f"affected_components has no non-N/A layer to act on: {affected!r}")


def _render_dict_section(value: Any) -> str:
    if isinstance(value, dict):
        return "\n".join(f"- **{key}**: {val}" for key, val in value.items())
    if isinstance(value, list):
        return "\n".join(f"- {item}" for item in value)
    return str(value)


def _dev_task_markdown(task: dict[str, Any]) -> str:
    sections = [
        ("User Story", task.get("user_story", "")),
        ("Acceptance Criteria", "\n".join(f"- {c}" for c in task.get("acceptance_criteria", []))),
        ("Technical Approach", "\n".join(f"- {t}" for t in task.get("technical_approach", []))),
        ("Affected Components", _render_dict_section(task.get("affected_components", {}))),
        ("API Contract", _render_dict_section(task.get("api_contract", {}))),
        ("Business Rules", "\n".join(f"- {r}" for r in task.get("business_rules", []))),
        ("Error Handling", "\n".join(f"- {e}" for e in task.get("error_handling", []))),
    ]
    return "\n\n".join(f"### {title}\n{body}" for title, body in sections if body)


def _unit_test_markdown(test: dict[str, Any]) -> str:
    scenarios = test.get("test_scenarios", {})
    scenarios_md = "\n".join(
        f"**{category}**\n" + "\n".join(f"- {item}" for item in items)
        for category, items in scenarios.items()
    )
    sections = [
        ("Test Objective", test.get("test_objective", "")),
        ("Test Scenarios", scenarios_md),
        ("Test Data", _render_dict_section(test.get("test_data", {}))),
        ("Mock Setup", "\n".join(f"- {m}" for m in test.get("mock_setup", []))),
        ("Assertions", "\n".join(f"- {a}" for a in test.get("assertions", []))),
    ]
    return "\n\n".join(f"### {title}\n{body}" for title, body in sections if body)


def build_plan(story: dict[str, Any]) -> Plan:
    """One StoryForge epic -> one DevCrew Plan. A dev_task whose
    affected_components can't be mapped to a DevCrew stack is skipped, not
    silently mismapped -- raises UnsupportedStackError only if every
    dev_task in the epic turns out unsupported, since Plan.tasks requires
    at least one."""
    epic_title = story.get("epic_title", "")
    user_story_text = story.get("user_story", "") or epic_title
    acceptance_criteria = story.get("acceptance_criteria") or ["(none specified)"]

    dev_tasks = story.get("dev_tasks", [])
    unit_test_tasks = story.get("unit_test_tasks", [])

    tasks: list[PlanTask] = []
    skipped: list[str] = []
    for i, dev_task in enumerate(dev_tasks):
        try:
            stack = _derive_stack(dev_task.get("affected_components", {}))
        except UnsupportedStackError as exc:
            skipped.append(f"{dev_task.get('title', f'dev_task[{i}]')}: {exc}")
            continue
        description_parts = [_dev_task_markdown(dev_task)]
        unit_test = unit_test_tasks[i] if i < len(unit_test_tasks) else None
        if unit_test:
            description_parts.append(
                "## Required Verification\n\n"
                "The following test scenarios were specified during requirements analysis and "
                "MUST be verified, in addition to whatever other tests you write:\n\n"
                + _unit_test_markdown(unit_test)
            )
        tasks.append(
            PlanTask(
                id=f"T{i + 1}",
                title=dev_task.get("title", f"Task {i + 1}"),
                description="\n\n".join(description_parts),
                target_files=[],
                depends_on=[],
                stack=stack,
                story_ids=["US1"],
            )
        )

    if not tasks:
        reason = "; ".join(skipped) if skipped else "no dev_tasks in this epic"
        raise UnsupportedStackError(f"epic {epic_title!r} has no dev_task DevCrew can act on -- {reason}")

    return Plan(
        summary=f"{epic_title}\n\n{user_story_text}",
        user_stories=[
            UserStory(id="US1", story=user_story_text, acceptance_criteria=acceptance_criteria)
        ],
        tasks=tasks,
    )


def _reconcile_task_stacks(plan: Plan, projects: list[ExistingProject]) -> Plan:
    """build_plan() runs before the target repo is even known, so
    _derive_stack's "java" is really just a placeholder meaning "some
    non-frontend backend change" -- it has no way to tell which backend
    language the repo actually uses. Now that prepare_repo has cloned the
    repo and detected its real project(s), remap any "java"-stamped task to
    the repo's own backend language when the repo makes that unambiguous
    (exactly one python/java project) -- e.g. a Python-only repo like
    DevCrew's own python test fixture. A task genuinely derived as "angular"
    is a real frontend-framework signal from the SDD, not a placeholder, and
    is never remapped here -- a true mismatch there still fails loudly in
    Design validation below instead of being silently reassigned to backend
    code (see test_build_design_raises_when_a_task_stack_has_no_matching_project).
    Multiple backend-capable projects (a genuinely mixed-stack repo) are
    left alone too -- there's no way to guess which one a task belongs to,
    so the existing validation error is the right outcome there."""
    backend_projects = [p for p in projects if p.stack in ("python", "java")]
    if len(backend_projects) != 1:
        return plan
    backend_stack = backend_projects[0].stack
    if backend_stack == "java" or not any(t.stack == "java" for t in plan.tasks):
        return plan
    return plan.model_copy(
        update={
            "tasks": [
                t.model_copy(update={"stack": backend_stack}) if t.stack == "java" else t
                for t in plan.tasks
            ]
        }
    )


def build_design(repo_info: dict[str, Any], story: dict[str, Any], plan: Plan) -> tuple[Plan, Design]:
    """Built once DevCrew's own prepare_repo node has detected the target
    repo's real project layout. Reuses quick_design()'s own detected-
    project scaffolding (existing_projects/stack -- no LLM call, matches
    what "quick fix on an existing repo" already does natively) but
    replaces its generic placeholder key_decisions/design_doc/modules with
    real content StoryForge's assessment already authored, instead of the
    minimal filler quick_design() falls back to when nothing better exists.

    Also reconciles the plan's own task stacks against the now-known repo
    (see _reconcile_task_stacks) -- returns the (possibly corrected) Plan
    alongside the Design so the caller can persist it back into run state;
    otherwise a corrected Design would validate fine here while task
    execution later still read the plan's original, wrong stack."""
    projects = [ExistingProject.model_validate(p) for p in repo_info.get("projects") or []]
    plan = _reconcile_task_stacks(plan, projects)
    base = quick_design({"repo_info": repo_info}, plan)

    modules: list[ModuleContract] = []
    seen: set[tuple[str, str]] = set()
    default_path = base.existing_projects[0].path if base.existing_projects else "."
    for dev_task in story.get("dev_tasks", []):
        affected = dev_task.get("affected_components", {}) or {}
        for layer, description in affected.items():
            if _is_na(description):
                continue
            key = (layer, description)
            if key in seen:
                continue
            seen.add(key)
            modules.append(
                ModuleContract(
                    name=f"{layer}: {dev_task.get('title', layer)}",
                    path=default_path,
                    responsibility=description,
                    interface=_render_dict_section(dev_task.get("api_contract", {})) or "N/A",
                )
            )
    if not modules:
        modules = base.modules  # every dev_task's affected_components were all N/A (unlikely)

    design_doc_sections = [f"# {story.get('epic_title', '')}\n\n{story.get('user_story', '')}"]
    design_doc_sections.extend(f"## {task.title}\n\n{task.description}" for task in plan.tasks)

    # base is already validated against plan's task stacks (quick_design); re-validated here too
    # since this Design's existing_projects/stack are what actually gets returned to the caller.
    design = Design.model_validate(
        {
            "stack": base.stack,
            "template_id": base.template_id,
            "components": base.components,
            "project_structure": base.project_structure,
            "modules": modules,
            "key_decisions": [
                "Design authored by StoryForge's own assessment pipeline, not DevCrew's "
                "Architect -- see the epic's acceptance criteria and each task's technical "
                "approach above."
            ],
            "design_doc": "\n\n".join(design_doc_sections),
            "plan_assessment": None,
            "existing_projects": base.existing_projects,
        },
        context={"task_stacks": {t.stack for t in plan.tasks}},
    )
    return plan, design
