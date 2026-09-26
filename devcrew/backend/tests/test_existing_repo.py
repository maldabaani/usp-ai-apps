"""Phase 11: existing repositories (detection, clone, full and quick change flows, delivery)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from app.db.models import RunStatus
from app.graph.interrupts import ResumePayload
from app.repo.detect import CONFIG_FILE, DetectionError, detect_projects, repository_map
from app.services.workflow import build_workflow
from app.tools.git import GitRepo
from tests.api_harness import api
from tests.fake_github import FakeGitHub
from tests.fakes import Call, final
from tests.graph_harness import APPROVE, PLAN, Harness, make_harness

RUN = "run0011aaaabbbbccccdddd"
GIT = ["-c", "user.email=dev@example.com", "-c", "user.name=Dev"]


def write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


PY_REPO = {
    "pyproject.toml": "[project]\nname = 'shop'\nversion = '1.0'\n",
    "app/__init__.py": "",
    "app/main.py": "def health() -> str:\n    return 'ok'\n",
    "app/schemas/todo.py": "# existing module\n",
    "tests/test_main.py": (
        "from app.main import health\n\n\ndef test_health():\n    assert health() == 'ok'\n"
    ),
    "README.md": "# Shop\n\nA small shop backend.\n",
}


def seeded_repo(
    gh: FakeGitHub, owner: str, name: str, files: dict[str, str], branch: str = "master"
) -> str:
    """A GitHub repository with one commit on `branch` (its default branch)."""
    work = gh.root.parent / f"seed-{name}"
    write(work, files)
    subprocess.run(["git", "init", "-q", "-b", branch, str(work)], check=True)
    subprocess.run(["git", *GIT, "-C", str(work), "add", "-A"], check=True)
    subprocess.run(["git", *GIT, "-C", str(work), "commit", "-q", "-m", "initial"], check=True)
    bare = gh.bare(owner, name)
    bare.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    return gh.heads(owner, name)[branch]


def existing_harness(tmp_path: Path, **settings: Any) -> tuple[Harness, FakeGitHub, str]:
    gh = FakeGitHub(tmp_path / "remotes")
    sha = seeded_repo(gh, "acme", "shop", PY_REPO)
    return make_harness(tmp_path, github=gh.delivery(), **settings), gh, sha


async def start(h: Harness, mode: str = "full") -> Any:
    return await h.driver.start(
        RUN, "Add a discount field to todos", "acme/shop", target="existing", mode=mode
    )


# ------------------------------------------------------------------------------ detection
@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"pyproject.toml": ""}, [("python", ".")]),
        ({"requirements.txt": "fastapi\n"}, [("python", ".")]),
        ({"pom.xml": "<project/>", "src/main/java/A.java": ""}, [("java", ".")]),
        ({"angular.json": "{}", "package.json": "{}"}, [("angular", ".")]),
        (
            {"backend/pyproject.toml": "", "frontend/angular.json": "{}", "docs/x.md": ""},
            [("python", "backend"), ("angular", "frontend")],
        ),
        # a Maven multi-module project: modules below the root are part of the root project
        ({"pom.xml": "", "core/pom.xml": "", "web/pom.xml": ""}, [("java", ".")]),
    ],
)
def test_detects_supported_projects(
    tmp_path: Path, files: dict[str, str], expected: list[tuple[str, str]]
) -> None:
    write(tmp_path, files)
    detection = detect_projects(tmp_path)
    assert sorted((p.stack, p.path) for p in detection.projects) == sorted(expected)
    assert detection.source == "detected"
    python = next((p for p in detection.projects if p.stack == "python"), None)
    if python:
        assert "--cov" in (python.coverage_cmd or "") and "pytest-cov" in python.install_cmd


def test_unsupported_and_ambiguous_repositories(tmp_path: Path) -> None:
    write(tmp_path / "gradle", {"build.gradle": ""})
    with pytest.raises(DetectionError, match="Gradle builds are not supported"):
        detect_projects(tmp_path / "gradle")
    write(tmp_path / "empty", {"README.md": "hi"})
    with pytest.raises(DetectionError, match="no supported project"):
        detect_projects(tmp_path / "empty")
    write(tmp_path / "two", {"a/pyproject.toml": "", "b/pyproject.toml": ""})
    with pytest.raises(DetectionError, match=f"several python projects.*{CONFIG_FILE}"):
        detect_projects(tmp_path / "two")


def test_devcrew_yaml_overrides_detection(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "a/pyproject.toml": "",
            "b/pyproject.toml": "",
            CONFIG_FILE: (
                "projects:\n  - stack: python\n    path: b\n    test_cmd: pytest -q tests/unit\n"
            ),
        },
    )
    detection = detect_projects(tmp_path)
    [project] = detection.projects
    assert (project.path, project.test_cmd, detection.source) == (
        "b",
        "pytest -q tests/unit",
        CONFIG_FILE,
    )
    assert project.install_cmd.startswith("if [ -f pyproject.toml ]")  # default kept
    (tmp_path / CONFIG_FILE).write_text("projects:\n  - stack: rust\n")
    with pytest.raises(DetectionError, match="unsupported stack"):
        detect_projects(tmp_path)
    (tmp_path / CONFIG_FILE).write_text("projects: [\n")
    with pytest.raises(DetectionError, match="invalid"):
        detect_projects(tmp_path)


def test_repository_map_is_bounded() -> None:
    files = [f"src/f{i}.py" for i in range(5)]
    assert repository_map(files, limit=3).endswith("… and 2 more files")


# ------------------------------------------------------------------------------ full flow
async def test_full_change_to_an_existing_repository(tmp_path: Path) -> None:
    h, gh, base_sha = existing_harness(tmp_path, github_delivery_enabled=True)
    outcome = await start(h)
    assert outcome.status is RunStatus.AWAITING_PLAN_APPROVAL
    state = await h.driver.state(RUN)
    assert state["base_branch"] == "master" and state["target"] == "existing"
    info = state["repo_info"]
    assert [(p["stack"], p["path"]) for p in info["projects"]] == [("python", ".")]
    assert "app/main.py" in info["tree"] and "small shop backend" in info["readme"]
    # the Planner saw the repository and could read its code
    planner_call = h.brain.calls_for("planner")[0]
    context = str(planner_call.messages[1].content)
    assert "Existing repository" in context and "app/main.py" in context
    assert "read_file" in planner_call.tool_names

    outcome = await h.driver.resume(RUN, APPROVE)
    assert outcome.status is RunStatus.AWAITING_DESIGN_APPROVAL
    design = outcome.interrupts[0].value["data"]["design"]
    assert design["template_id"] == "existing" and design["stack"] == "python"
    assert design["existing_projects"][0]["path"] == "."
    architect = h.brain.calls_for("architect")[0]
    assert "EXISTING codebase" in str(architect.messages[0].content)
    assert "list_templates" not in architect.tool_names

    outcome = await h.driver.resume(RUN, APPROVE)
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL
    state = await h.driver.state(RUN)
    root = Path(state["workspace"])
    assert (root / "app/main.py").read_text() == PY_REPO["app/main.py"]  # existing code kept
    assert not (root / "docs/design.md").exists()  # nothing added outside the tasks

    outcome = await h.driver.resume(RUN, APPROVE)
    assert outcome.status is RunStatus.COMPLETED
    state = await h.driver.state(RUN)
    [pr] = gh.pulls
    assert pr["base"] == "master" and pr["head"] == state["integration_branch"]
    heads = gh.heads("acme", "shop")
    assert heads["master"] == base_sha  # the base branch is never pushed
    assert state["integration_branch"] in heads
    log = gh.log("acme", "shop", state["integration_branch"])
    assert log[-1] == "initial" and any(m.startswith("T1:") for m in log)
    assert "existing `python` project" in pr["body"]


async def test_quick_fix_skips_the_architect(tmp_path: Path) -> None:
    h, _, _ = existing_harness(tmp_path)
    await start(h, mode="quick")
    context = str(h.brain.calls_for("planner")[0].messages[1].content)
    assert "Quick-fix mode" in context
    outcome = await h.driver.resume(RUN, APPROVE)
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL
    assert h.brain.calls_for("architect") == []
    state = await h.driver.state(RUN)
    assert state["design"]["template_id"] == "existing"
    assert state["design"]["design_doc"].startswith("Quick fix run")
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
    assert nodes["prepare_repo"].status == "done"
    assert nodes["prepare_repo"].detail == "python (.) · base master"
    assert "quick fix change" in (nodes["requirements"].detail or "")


async def test_plan_must_use_the_repository_stacks(tmp_path: Path) -> None:
    h, _, _ = existing_harness(tmp_path, max_coordinator_actions=0)
    angular_plan = {**PLAN, "tasks": [{**t, "stack": "angular"} for t in PLAN["tasks"]]}

    def planner(call: Call) -> AIMessage:
        return final(angular_plan)

    h.brain.responders["planner"] = planner
    outcome = await start(h)
    # the plan never validates, so the planner escalates to the human
    assert outcome.status is RunStatus.NEEDS_HUMAN
    assert "only has ['python'] projects" in outcome.interrupts[0].value["data"]["reason"]


# ------------------------------------------------------------------------------ failures
async def test_unsupported_repository_escalates_and_can_be_aborted(tmp_path: Path) -> None:
    gh = FakeGitHub(tmp_path / "remotes")
    seeded_repo(gh, "acme", "docs", {"README.md": "just docs"})
    h = make_harness(tmp_path, github=gh.delivery())
    outcome = await h.driver.start(RUN, "Change things", "acme/docs", target="existing")
    assert outcome.status is RunStatus.NEEDS_HUMAN
    value = outcome.interrupts[0].value
    assert value["kind"] == "escalation" and value["data"]["node"] == "prepare_repo"
    assert "no supported project" in value["data"]["reason"]
    assert h.brain.calls_for("planner") == []
    outcome = await h.driver.resume(RUN, ResumePayload(action="reject", feedback="wrong repo"))
    assert outcome.status is RunStatus.FAILED


async def test_missing_repository_and_missing_github_access(tmp_path: Path) -> None:
    gh = FakeGitHub(tmp_path / "remotes")
    h = make_harness(tmp_path, github=gh.delivery())
    outcome = await h.driver.start(RUN, "Change things", "acme/nope", target="existing")
    assert "does not exist" in outcome.interrupts[0].value["data"]["reason"]

    h2 = make_harness(tmp_path / "b")
    outcome = await h2.driver.start(RUN, "Change things", "acme/shop", target="existing")
    assert "needs GitHub access" in outcome.interrupts[0].value["data"]["reason"]


async def test_retry_after_fixing_the_repository(tmp_path: Path) -> None:
    gh = FakeGitHub(tmp_path / "remotes")
    h = make_harness(tmp_path, github=gh.delivery())
    outcome = await h.driver.start(RUN, "Change things", "acme/shop", target="existing")
    assert outcome.status is RunStatus.NEEDS_HUMAN
    seeded_repo(gh, "acme", "shop", PY_REPO)  # the human creates/fixes the repository
    outcome = await h.driver.resume(RUN, ResumePayload(action="approve"))
    assert outcome.status is RunStatus.AWAITING_PLAN_APPROVAL


# ------------------------------------------------------------------------------ API
async def test_api_existing_target(tmp_path: Path) -> None:
    body = {
        "request": "Add a discount field",
        "repo_target": "acme/shop",
        "target": "existing",
        "mode": "quick",
    }
    async with api(tmp_path) as a:  # no GitHub configured
        resp = await a.client.post("/runs", json=body)
        assert resp.status_code == 422 and "needs GitHub access" in resp.json()["detail"]

    gh = FakeGitHub(tmp_path / "remotes")
    seeded_repo(gh, "acme", "shop", PY_REPO)
    harness = make_harness(tmp_path / "b", github=gh.delivery())
    async with api(tmp_path / "b", harness=harness) as a:
        resp = await a.client.post("/runs", json={**body, "create_repo": True})
        assert resp.status_code == 201
        run = await a.settle(resp.json()["id"])
        assert (run["target"], run["mode"], run["base_branch"]) == ("existing", "quick", "master")
        assert run["create_repo"] is False  # never create an existing repository
        assert run["repo_info"]["projects"][0]["stack"] == "python"
        files = (await a.client.get(f"/runs/{run['id']}/files", params={"ref": "main"})).json()
        assert "app/main.py" in [f["path"] for f in files["files"]]
        wf = (await a.client.get(f"/runs/{run['id']}/workflow")).json()
        ids = [n["id"] for n in wf["nodes"]]
        assert ids[:3] == ["requirements", "prepare_repo", "planner"] and "gates" in ids


async def test_existing_delivery_refuses_a_missing_base_or_pushing_the_base(tmp_path: Path) -> None:
    from app.github.delivery import DeliveryError

    gh = FakeGitHub(tmp_path / "remotes")
    seeded_repo(gh, "acme", "shop", PY_REPO)
    work = tmp_path / "work"
    await GitRepo(tmp_path).run("clone", "-q", str(gh.bare("acme", "shop")), str(work))
    state = {
        "final_approved": True,
        "target": "existing",
        "repo_target": "acme/shop",
        "workspace": str(work),
        "base_branch": "develop",
        "integration_branch": "devcrew/x",
    }
    with pytest.raises(DeliveryError, match="no branch develop"):
        await gh.delivery().deliver(state)
    with pytest.raises(DeliveryError, match="refusing to push the base branch"):
        await gh.delivery().deliver(
            {**state, "base_branch": "master", "integration_branch": "master"}
        )
