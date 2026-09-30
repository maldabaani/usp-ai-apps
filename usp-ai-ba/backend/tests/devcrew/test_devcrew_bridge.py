"""Covers the merge plan's Phase 6: pipeline/devcrew_bridge.py's
build_plan/build_design, and devcrew/graph/nodes/repository.py's new
conditional branch in prepare_repo that skips DevCrew's own Planner and
Architect entirely for a bridged run -- going straight from "Send to
DevCrew" to scaffold and per-task execution (Developer -> Reviewer -> QA),
with zero DevCrew-side approval pause (StoryForge's own review step is the
sole "approve what to build" checkpoint -- see the plan's Revision note).

Uses the exact same real-local-git-clone harness (existing_harness/
seeded_repo/FakeGitHub) test_existing_repo.py's own "existing repository"
tests already use, per this test suite's established cross-file-import
convention (test_gates.py/test_robustness.py already do the same) --
prepare_repo's real clone/detect logic runs for real here (against a local
bare repo, no network), so this is a genuine integration test of the new
branch, not just a unit test of the bridge module in isolation.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from devcrew.graph.state import Plan, dump
from devcrew.services.workflow import build_workflow
from pipeline.devcrew_bridge import UnsupportedStackError, build_design, build_plan
from tests.devcrew.fake_github import FakeGitHub
from tests.devcrew.graph_harness import Harness, make_harness
from tests.devcrew.test_existing_repo import RUN, seeded_repo

JAVA_REPO = {
    "pom.xml": "<project/>",
    "src/main/java/com/acme/shop/App.java": "package com.acme.shop;\n\npublic class App {}\n",
    "README.md": "# Shop\n\nA small shop backend.\n",
}


def java_harness(tmp_path: Path) -> tuple[Harness, FakeGitHub, str]:
    """Like existing_harness (test_existing_repo.py), but a java repo: STORY's dev_task touches
    "backend", which build_plan always derives as stack "java" (_derive_stack) -- the bridged
    integration tests below need a repository that stack can actually run in, now that
    build_design validates the Design against the plan's own task stacks (quick_design)."""
    gh = FakeGitHub(tmp_path / "remotes")
    sha = seeded_repo(gh, "acme", "shop", JAVA_REPO)
    return make_harness(tmp_path, github=gh.delivery()), gh, sha

STORY = {
    "epic_title": "Add a discount field",
    "user_story": "As a shopper, I want a discount field on my order.",
    "acceptance_criteria": ["Given an order, when discount is applied, then total reflects it."],
    "dev_tasks": [
        {
            "title": "1 Task - Add discount field",
            "user_story": "As a backend dev, I want to add a discount field.",
            "acceptance_criteria": ["Given a request, when discount is set, then it's persisted."],
            "technical_approach": ["Add a discount field to the order schema"],
            "affected_components": {
                "frontend": "N/A",
                "backend": "the order service",
                "middleware": "N/A",
                "database": "the orders table",
            },
            "api_contract": {
                "endpoint": "POST /orders",
                "request": {"discount": "number"},
                "response_success": {},
                "response_error": {},
                "status_codes": [200],
            },
            "business_rules": ["Rule 1: discount must be >= 0"],
            "error_handling": ["Scenario 1: negative discount -> 400"],
        }
    ],
    "unit_test_tasks": [
        {
            "title": "1 Unit Test - discount field",
            "test_objective": "Verify discount persists correctly.",
            "test_scenarios": {
                "happy_path": ["TC-01: discount=5 -> persisted"],
                "negative": ["TC-02: discount=-1 -> 400"],
                "edge_cases": [],
            },
            "test_data": {"valid": {"discount": 5}, "invalid": {"discount": -1}},
            "mock_setup": ["Mock the order repository"],
            "assertions": ["Assert response status 200", "Assert discount == 5"],
        }
    ],
}


# --------------------------------------------------------------------------------- unit-level


def test_build_plan_skips_architect_shaped_fields_but_keeps_them_as_context():
    plan = build_plan(STORY)

    assert len(plan.user_stories) == 1
    assert plan.user_stories[0].id == "US1"
    assert len(plan.tasks) == 1
    task = plan.tasks[0]
    assert task.id == "T1"
    assert task.stack == "java"  # backend affected -> java
    assert task.target_files == []  # never fabricate paths
    assert task.depends_on == []
    assert "Required Verification" in task.description
    assert "TC-01: discount=5 -> persisted" in task.description  # unit test scenario preserved


def test_build_plan_raises_when_every_dev_task_is_unsupported():
    story = {**STORY, "dev_tasks": [{**STORY["dev_tasks"][0], "affected_components": {
        "frontend": "the legacy jQuery admin screen", "backend": "N/A", "middleware": "N/A", "database": "N/A",
    }}]}

    with pytest.raises(UnsupportedStackError, match="legacy"):
        build_plan(story)


def test_build_plan_java_wins_even_with_a_legacy_frontend_mention():
    # A task touching both backend and a legacy-described frontend is still
    # automatable (Developer works on the backend layer) -- only a task with
    # NO non-N/A layer DevCrew can act on is unsupported.
    story = {**STORY, "dev_tasks": [{**STORY["dev_tasks"][0], "affected_components": {
        "frontend": "the legacy jQuery admin screen", "backend": "the order service", "middleware": "N/A", "database": "N/A",
    }}]}

    plan = build_plan(story)

    assert plan.tasks[0].stack == "java"


def test_build_design_derives_modules_from_affected_components():
    # STORY's dev_task touches "backend" -> build_plan derives stack "java" (see
    # test_build_plan_skips_architect_shaped_fields_but_keeps_them_as_context above), so the
    # fixture repo must be java too: build_design now validates the Design against the plan's
    # own task stacks (quick_design), and a mismatch would raise here otherwise.
    plan = build_plan(STORY)
    repo_info = {
        "base_branch": "main",
        "projects": [
            {
                "stack": "java",
                "path": ".",
                "install_cmd": "mvn -q -DskipTests install",
                "build_cmd": "mvn -q compile",
                "test_cmd": "mvn -q test",
                "coverage_cmd": None,
                "preview_cmd": None,
                "preview_port": None,
            }
        ],
        "source": "detected",
        "notes": [],
        "file_count": 5,
        "tree": "",
        "readme": "",
        "commit": "abc",
    }

    design = build_design(repo_info, STORY, plan)

    assert design.stack.value == "java"
    assert len(design.modules) == 2  # backend + database, both non-N/A
    assert "Add a discount field" in design.design_doc
    assert design.existing_projects[0].stack == "java"


def test_build_design_raises_when_a_task_stack_has_no_matching_project():
    # The repository StoryForge's epic targets is python-only; a task the bridge derives as
    # angular has nowhere to run. This must fail loudly here, not hand TaskCtx.project an
    # arbitrary other stack's template later (see task_common.py's TaskCtx.project fallback).
    story = {
        **STORY,
        "dev_tasks": [
            {
                **STORY["dev_tasks"][0],
                "affected_components": {
                    "frontend": "an Angular component showing the discount",
                    "backend": "N/A",
                    "middleware": "N/A",
                    "database": "N/A",
                },
            }
        ],
    }
    plan = build_plan(story)
    assert plan.tasks[0].stack == "angular"
    repo_info = {
        "base_branch": "main",
        "projects": [
            {
                "stack": "python",
                "path": ".",
                "install_cmd": "pip install -e .",
                "build_cmd": "",
                "test_cmd": "pytest -q",
                "coverage_cmd": None,
                "preview_cmd": None,
                "preview_port": None,
            }
        ],
        "source": "detected",
        "notes": [],
        "file_count": 5,
        "tree": "",
        "readme": "",
        "commit": "abc",
    }

    with pytest.raises(ValidationError, match=r"\['angular'\].*only contains \['python'\]"):
        build_design(repo_info, story, plan)


# --------------------------------------------------------------------------------- integration


async def test_bridged_run_reaches_scaffold_without_calling_planner_or_architect(
    tmp_path: Path,
) -> None:
    h, gh, sha = java_harness(tmp_path)

    def _must_not_be_called(role: str):
        def responder(call):
            raise AssertionError(f"{role} must not be called for a bridged run")

        return responder

    h.brain.responders["planner"] = _must_not_be_called("planner")
    h.brain.responders["architect"] = _must_not_be_called("architect")

    plan = build_plan(STORY)

    outcome = await h.driver.start(
        RUN,
        "Add a discount field to todos",
        "acme/shop",
        target="existing",
        mode="quick",
        plan=dump(plan),
        storyforge_epic=STORY,
    )

    state = await h.driver.state(RUN)
    # Never paused at approve_plan/approve_design -- either it's mid-execution
    # (scaffolding/task work already dispatched) or finished; it must not be
    # sitting at an awaiting_plan_approval/awaiting_design_approval interrupt.
    assert state["status"] not in ("awaiting_plan_approval", "awaiting_design_approval")
    assert state.get("design") is not None  # built by prepare_repo's new branch, not Architect
    assert state["repo_info"] is not None  # prepare_repo's real clone/detect still ran
    assert outcome is not None


async def test_bridged_run_design_reflects_storyforges_own_content(tmp_path: Path) -> None:
    h, gh, sha = java_harness(tmp_path)
    h.brain.responders["planner"] = lambda call: (_ for _ in ()).throw(
        AssertionError("planner must not be called")
    )
    h.brain.responders["architect"] = lambda call: (_ for _ in ()).throw(
        AssertionError("architect must not be called")
    )
    plan = build_plan(STORY)

    await h.driver.start(
        RUN,
        "Add a discount field to todos",
        "acme/shop",
        target="existing",
        mode="quick",
        plan=dump(plan),
        storyforge_epic=STORY,
    )

    state = await h.driver.state(RUN)
    round_tripped_plan = Plan.model_validate(state["plan"])  # survives unchanged through the graph
    assert round_tripped_plan.tasks[0].id == "T1"
    assert "discount" in (state["design"]["design_doc"] or "").lower()


async def test_bridged_run_workflow_detail_is_not_mislabeled_quick_fix(tmp_path: Path) -> None:
    """Regression test: workflow.py used to mark a bridged run's Architect/Design
    approval/Requirements nodes with the same "quick fix: no design step" text a
    genuine quick-fix run gets, even though a bridged run *does* have a real,
    StoryForge-authored design (see build_bridged_run_design_reflects_storyforges_own_content
    above) -- both cases set target="existing", mode="quick", and only
    storyforge_epic tells them apart. The workflow UI should say so, not imply
    there's no design at all."""
    h, gh, sha = java_harness(tmp_path)
    h.brain.responders["planner"] = lambda call: (_ for _ in ()).throw(
        AssertionError("planner must not be called")
    )
    h.brain.responders["architect"] = lambda call: (_ for _ in ()).throw(
        AssertionError("architect must not be called")
    )
    plan = build_plan(STORY)

    await h.driver.start(
        RUN,
        "Add a discount field to todos",
        "acme/shop",
        target="existing",
        mode="quick",
        plan=dump(plan),
        storyforge_epic=STORY,
    )

    state = await h.driver.state(RUN)
    events = await h.events(RUN)
    wf = build_workflow(
        run_id=RUN,
        status=state["status"],
        request=state["request"],
        created_at=None,
        state=state,
        events=events,
        pending=await h.driver.pending_interrupts(RUN),
        pr_url=None,
        max_dev_iterations=3,
    )
    nodes = {n.id: n for n in wf.nodes}
    assert nodes["architect"].status == "skipped" and nodes["approve_design"].status == "skipped"
    assert "StoryForge" in (nodes["architect"].detail or "")
    assert "StoryForge" in (nodes["approve_design"].detail or "")
    assert "quick fix" not in (nodes["architect"].detail or "")
    assert "StoryForge" in (nodes["requirements"].detail or "")
