"""Scoped, budgeted context for each role.

Every node gets only the artifacts its role needs, never the run history. Sections have
priorities; when the prompt would exceed its token budget, the lowest-priority sections are
truncated first (whole lines, with a marker) and required sections are kept intact.

Raw file contents are never inlined here: code arrives as retrieved RAG chunks (a budgeted,
truncatable section) or through the agents' read_file / search_codebase tools.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from app.graph.state import Design, Plan, PlanTask, QAEntry, TaskState
from app.llm.tokens import estimate_tokens, truncate_to_tokens

# Share of the prompt budget the initial context may use; the rest is for the tool loop.
INITIAL_CONTEXT_SHARE = 0.55
SECTION_HEADER_TOKENS = 8


@dataclass(frozen=True)
class Section:
    title: str
    body: str
    priority: int = 5  # lower = more important
    required: bool = False

    def render(self) -> str:
        return f"## {self.title}\n{self.body.strip()}\n"


def fit_sections(sections: Sequence[Section], budget_tokens: int) -> str:
    """Render sections within `budget_tokens`, truncating low-priority ones first."""
    items = [s for s in sections if s.body.strip()]
    sizes = {id(s): estimate_tokens(s.render()) for s in items}
    total = sum(sizes.values())
    bodies = {id(s): s.body for s in items}
    if total > budget_tokens:
        overflow = total - budget_tokens
        for section in sorted(
            (s for s in items if not s.required), key=lambda s: s.priority, reverse=True
        ):
            if overflow <= 0:
                break
            current = sizes[id(section)]
            keep = max(0, current - overflow - SECTION_HEADER_TOKENS)
            if keep < 40:
                bodies[id(section)] = ""
                overflow -= current
            else:
                bodies[id(section)] = truncate_to_tokens(section.body, keep)
                overflow -= current - estimate_tokens(
                    Section(section.title, bodies[id(section)]).render()
                )
    return "\n".join(
        Section(s.title, bodies[id(s)]).render() for s in items if bodies[id(s)].strip()
    )


def budget_for(prompt_budget: int, system_prompt: str) -> int:
    return max(256, int(prompt_budget * INITIAL_CONTEXT_SHARE) - estimate_tokens(system_prompt))


# --------------------------------------------------------------------------------------------
# Rendering helpers
# --------------------------------------------------------------------------------------------
def render_stories(plan: Plan, story_ids: Sequence[str] | None = None) -> str:
    stories = [s for s in plan.user_stories if not story_ids or s.id in story_ids]
    lines = []
    for s in stories or plan.user_stories:
        lines.append(f"- {s.id}: {s.story}")
        lines.extend(f"    - AC: {ac}" for ac in s.acceptance_criteria)
    return "\n".join(lines)


def render_task(task: PlanTask) -> str:
    deps = ", ".join(task.depends_on) or "none"
    files = "\n".join(f"  - {f}" for f in task.target_files) or "  (none listed)"
    return (
        f"id: {task.id}\ntitle: {task.title}\nstack: {task.stack}\ndepends_on: {deps}\n"
        f"description: {task.description}\ntarget_files:\n{files}"
    )


def render_plan_tasks(plan: Plan) -> str:
    return "\n".join(
        f"- {t.id} [{t.stack}] {t.title} (depends on: {', '.join(t.depends_on) or 'none'}; "
        f"files: {', '.join(t.target_files)})"
        for t in plan.tasks
    )


def _related(module_path: str, files: Sequence[str]) -> bool:
    mod = PurePosixPath(module_path.rstrip("/"))
    for f in files:
        fp = PurePosixPath(f)
        if fp == mod or mod in fp.parents or fp.parent == mod.parent:
            return True
    return False


def render_contracts(design: Design, focus_files: Sequence[str] | None = None) -> str:
    """Module contracts; modules related to `focus_files` first."""
    modules = list(design.modules)
    if focus_files:
        modules.sort(key=lambda m: not _related(m.path, focus_files))
    return "\n\n".join(
        f"### {m.name} ({m.path})\n{m.responsibility}\n{m.interface}" for m in modules
    )


def render_qa(entries: Sequence[QAEntry]) -> str:
    return "\n".join(f"- Q ({e.asker}->{e.target}): {e.question}\n  A: {e.answer}" for e in entries)


# --------------------------------------------------------------------------------------------
# Role contexts
# --------------------------------------------------------------------------------------------
QUICK_FIX_NOTE = (
    "Quick-fix mode: there is no design step. Produce the smallest change plan that satisfies "
    "the request: usually 1-3 tasks, each naming the existing files it changes (plus the tests "
    "it adds or updates)."
)


def render_repository(repo: Mapping[str, Any]) -> str:
    projects = "\n".join(
        f"- {p['stack']} project at `{p['path']}` (tests: `{p['test_cmd']}`)"
        for p in repo.get("projects") or []
    )
    parts = [
        f"Base branch: {repo.get('base_branch')}. Detected projects:\n{projects}",
        "You are CHANGING this existing codebase, not creating a new one: keep its structure, "
        "naming and conventions, reuse existing modules, and use real paths from the listing "
        "below in target_files. Use read_file / search_codebase to look at the code.",
    ]
    for note in repo.get("notes") or []:
        parts.append(f"Note: {note}")
    return "\n\n".join(parts)


def repository_sections(repo: Mapping[str, Any] | None, mode: str | None) -> list[Section]:
    if not repo:
        return []
    sections = [
        Section("Existing repository", render_repository(repo), priority=0, required=True),
        Section("Repository files", str(repo.get("tree", "")), priority=4),
        Section("README", str(repo.get("readme", "")), priority=5),
    ]
    if mode == "quick":
        sections.append(Section("Mode", QUICK_FIX_NOTE, priority=0, required=True))
    return sections


def notes_sections(notes: Sequence[str]) -> list[Section]:
    """The human's chat messages (Phase 13): instructions every agent must follow."""
    if not notes:
        return []
    text = "\n".join(f"- {n}" for n in notes)
    return [Section("Notes from the human (follow them)", text, priority=0, required=True)]


def planner_context(
    request: str,
    budget: int,
    *,
    feedback: str | None = None,
    previous_plan: Plan | None = None,
    qa: Sequence[QAEntry] = (),
    repo: Mapping[str, Any] | None = None,
    mode: str | None = None,
    notes: Sequence[str] = (),
) -> str:
    sections = [Section("Feature request", request, priority=0, required=True)]
    sections += repository_sections(repo, mode)
    sections += notes_sections(notes)
    if feedback:
        sections.append(
            Section(
                "Human feedback on your previous plan (address all of it)",
                feedback,
                priority=0,
                required=True,
            )
        )
    if previous_plan is not None:
        sections.append(Section("Previous plan", previous_plan.model_dump_json(indent=1), 6))
    if qa:
        sections.append(Section("Answers from the human", render_qa(qa), 1))
    return fit_sections(sections, budget)


def architect_context(
    request: str,
    plan: Plan,
    budget: int,
    *,
    feedback: str | None = None,
    previous_design: Design | None = None,
    qa: Sequence[QAEntry] = (),
    repo: Mapping[str, Any] | None = None,
    notes: Sequence[str] = (),
) -> str:
    stacks = sorted({t.stack for t in plan.tasks})
    sections = [
        Section("Feature request", request, priority=0, required=True),
        *repository_sections(repo, None),
        Section("Plan summary", plan.summary, priority=1),
        Section("Stacks used by tasks", ", ".join(stacks), priority=0, required=True),
        Section("Tasks", render_plan_tasks(plan), priority=1, required=True),
        Section("User stories", render_stories(plan), priority=3),
        *notes_sections(notes),
    ]
    if feedback:
        sections.append(
            Section(
                "Human feedback on your previous design (address all of it)",
                feedback,
                priority=0,
                required=True,
            )
        )
    if previous_design is not None:
        sections.append(Section("Previous design", previous_design.model_dump_json(indent=1), 6))
    if qa:
        sections.append(Section("Answers from the human", render_qa(qa), 1))
    return fit_sections(sections, budget)


def developer_context(
    task: PlanTask,
    plan: Plan,
    design: Design,
    task_state: TaskState,
    rules: str,
    file_tree: str,
    budget: int,
    *,
    qa: Sequence[QAEntry] = (),
    related_code: str = "",
    notes: Sequence[str] = (),
) -> str:
    sections = [
        Section("Your task", render_task(task), priority=0, required=True),
        *notes_sections(notes),
        Section("Acceptance criteria", render_stories(plan, task.story_ids), priority=1),
        Section(
            "Design contracts (code against these)",
            render_contracts(design, task.target_files),
            priority=2,
        ),
        Section("Key design decisions", "\n".join(f"- {d}" for d in design.key_decisions), 4),
        Section("Relevant existing code (retrieved; may be partial)", related_code, priority=3),
        Section(f"{task.stack} rules", rules, priority=5),
        Section("Project files", file_tree, priority=6),
    ]
    if task_state.feedback:
        sections.insert(
            1,
            Section(
                f"Feedback to address (attempt {task_state.iterations + 1})",
                task_state.feedback,
                priority=0,
                required=True,
            ),
        )
    if qa:
        sections.append(Section("Q&A for this task", render_qa(qa), 1))
    return fit_sections(sections, budget)


def reviewer_context(
    task: PlanTask,
    plan: Plan,
    design: Design,
    rules: str,
    diff: str,
    budget: int,
    *,
    related_code: str = "",
    notes: Sequence[str] = (),
) -> str:
    return fit_sections(
        [
            Section("Task under review", render_task(task), priority=0, required=True),
            *notes_sections(notes),
            Section("Acceptance criteria", render_stories(plan, task.story_ids), priority=1),
            Section("Design contracts", render_contracts(design, task.target_files), 2),
            Section("Diff (integration...task branch)", diff or "(no changes)", 3),
            Section(f"{task.stack} rules", rules, priority=4),
            Section("Existing code the change interacts with (retrieved)", related_code, 5),
        ],
        budget,
    )


def qa_context(
    task: PlanTask,
    plan: Plan,
    design: Design,
    changed_files: Sequence[str],
    test_cmd: str,
    rules: str,
    budget: int,
    *,
    existing_tests: str = "",
) -> str:
    return fit_sections(
        [
            Section("Task to test", render_task(task), priority=0, required=True),
            Section("Acceptance criteria", render_stories(plan, task.story_ids), priority=0),
            Section("Files changed by the developer", "\n".join(changed_files) or "(none)", 1),
            Section("Design contracts", render_contracts(design, task.target_files), 3),
            Section("Test command (run for you after you finish)", test_cmd, 1),
            Section("Existing tests (follow their fixtures and style)", existing_tests, 4),
            Section(f"{task.stack} testing rules", rules, priority=5),
        ],
        budget,
    )


def qa_report_context(task: PlanTask, test_cmd: str, logs: str, budget: int) -> str:
    return fit_sections(
        [
            Section("Task", render_task(task), priority=1),
            Section("Command", test_cmd, priority=0, required=True),
            Section("Test output (tail)", logs, priority=2),
        ],
        budget,
    )


def json_block(data: object) -> str:
    return json.dumps(data, indent=1, default=str)
