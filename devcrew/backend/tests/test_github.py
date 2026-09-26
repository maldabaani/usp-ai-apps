"""Phase 8: GitHub delivery (REST mocked, pushes to bare git repositories on disk)."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.db.models import RunStatus
from app.github.client import GitHubClient, GitHubError
from app.github.delivery import DeliveryError, git_auth_env
from app.github.pr_body import MAX_BODY, pr_body, pr_title
from app.graph.interrupts import ResumePayload
from app.tools.git import GitRepo
from tests.api_harness import api
from tests.fake_github import TOKEN, FakeGitHub
from tests.graph_harness import APPROVE, make_harness

RUN = "run0008aaaabbbbccccdddd"


# ------------------------------------------------------------------------------ REST client
async def test_client_endpoints_headers_and_errors(tmp_path: Path) -> None:
    gh = FakeGitHub(tmp_path)
    client = gh.client()
    assert await client.get_repo("octocat", "nope") is None
    await client.create_repo("octocat", "mine", description="d")
    await client.create_repo("acme", "team-repo", description="d")
    assert ("POST", "/user/repos") in gh.calls() and ("POST", "/orgs/acme/repos") in gh.calls()
    created = [r for r in gh.requests if r.method == "POST"]
    import json

    assert all(json.loads(r.content)["private"] is True for r in created)
    assert all(json.loads(r.content)["auto_init"] is False for r in created)
    assert gh.requests[0].headers["x-github-api-version"] == "2022-11-28"

    gh.fail_status = 401
    with pytest.raises(GitHubError) as info:
        await client.get_repo("octocat", "x")
    assert info.value.status == 401 and "rejected" in str(info.value)
    assert TOKEN not in str(info.value)
    await client.aclose()


async def test_client_network_error_is_wrapped() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    client = GitHubClient(TOKEN, transport=httpx.MockTransport(boom))
    with pytest.raises(GitHubError, match="cannot reach GitHub"):
        await client.login()
    await client.aclose()


def test_git_auth_env_uses_a_header_never_the_url() -> None:
    env = git_auth_env("https://github.com", TOKEN)
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    encoded = env["GIT_CONFIG_VALUE_0"].split("basic ", 1)[1]
    assert base64.b64decode(encoded).decode() == f"x-access-token:{TOKEN}"
    assert git_auth_env("file:///tmp/remotes", TOKEN) == {}


# ---------------------------------------------------------------------------------- PR body
STATE: dict[str, Any] = {
    "run_id": RUN,
    "request": "Build a FastAPI TODO API with CRUD\nand pytest tests",
    "plan": {
        "summary": "TODO service",
        "user_stories": [
            {"id": "US1", "story": "As a user I want todos", "acceptance_criteria": ["CRUD works"]}
        ],
        "tasks": [{"id": "T1", "title": "Todo | model", "stack": "python"}],
    },
    "design": {"stack": "python", "template_id": "python-fastapi", "design_doc": "# Design\nX"},
    "tasks": {
        "T1": {"status": "merged", "iterations": 2, "test_results": {"ran": True, "passed": True}}
    },
    "integration": {
        "tests": {
            "python": {
                "ran": True,
                "passed": False,
                "command": "pytest -q",
                "logs_excerpt": "FAILED test_x",
            }
        }
    },
    "qa_log": [
        {
            "asker": "developer",
            "target": "architect",
            "task_id": "T1",
            "question": "Int ids?",
            "answer": "Yes",
        }
    ],
}


def test_pr_body_contents() -> None:
    body = pr_body(STATE)
    for expected in (
        "## Request",
        "Build a FastAPI TODO API",
        "TODO service",
        "**US1**",
        "  - CRUD works",
        "<details><summary>Design document</summary>",
        "# Design",
        "| T1 | Todo \\| model | python | merged | 2 | passed |",
        "| python | ❌ failed | `pytest -q` |",
        "FAILED test_x",
        "**developer → architect** (T1): Int ids?",
        "  - Yes",
        RUN,
    ):
        assert expected in body, expected
    assert pr_title(STATE["request"]) == "DevCrew: Build a FastAPI TODO API with CRUD"
    assert pr_title("x" * 100).endswith("…") and len(pr_title("x" * 100)) < 90


def test_pr_body_is_capped() -> None:
    huge = {
        **STATE,
        "design": {**STATE["design"], "design_doc": "d" * 200_000},
        "qa_log": [STATE["qa_log"][0]] * 2000,
    }
    body = pr_body(huge)
    assert len(body) <= MAX_BODY + 40 and body.endswith("(truncated)")


# ------------------------------------------------------------------------------- delivery
async def _workspace(tmp_path: Path) -> dict[str, Any]:
    """A run workspace: main = scaffold commit, integration branch with two task commits."""
    ws = tmp_path / "ws" / "repo"
    repo = GitRepo(ws)
    await repo.init()
    (ws / "README.md").write_text("scaffold\n")
    await repo.commit_all("Scaffold from template python-fastapi")
    branch = f"devcrew/{RUN}-build-a-fastapi-todo-api"
    await repo.checkout(branch, create_from="main")
    for tid in ("T1", "T2"):
        (ws / f"{tid}.py").write_text(f"# {tid}\n")
        await repo.commit_all(f"{tid}: work")
    await repo.checkout(f"devcrew/{RUN[:8]}/task-T1", create_from="main")  # a local task branch
    await repo.checkout(branch)
    return {
        **STATE,
        "workspace": str(ws),
        "integration_branch": branch,
        "repo_target": "octocat/todo-api",
        "create_repo": False,
        "final_approved": True,
    }


async def test_empty_repo_gets_scaffold_on_main_and_a_pr(tmp_path: Path) -> None:
    gh = FakeGitHub(tmp_path / "gh")
    gh.make_repo("octocat", "todo-api")
    state = await _workspace(tmp_path)
    steps: list[str] = []

    async def progress(message: str) -> None:
        steps.append(message)

    result = await gh.delivery().deliver(state, progress=progress)
    assert result.pushed_main and not result.repo_created
    assert result.pr_url == "https://github.com/octocat/todo-api/pull/1"
    heads = gh.heads("octocat", "todo-api")
    assert set(heads) == {"main", state["integration_branch"]}  # task branches never pushed
    assert gh.log("octocat", "todo-api", "main") == ["Scaffold from template python-fastapi"]
    assert gh.log("octocat", "todo-api", state["integration_branch"])[:2] == [
        "T2: work",
        "T1: work",
    ]
    pr = gh.pulls[0]
    assert (pr["head"], pr["base"]) == (state["integration_branch"], "main")
    assert pr["title"].startswith("DevCrew: Build a FastAPI TODO API") and "## Tasks" in pr["body"]
    assert steps == [
        "pushing the template scaffold to main (empty repository)",
        f"pushing {state['integration_branch']}",
        "opening the pull request",
    ]


async def test_missing_repo_is_created_private_when_asked(tmp_path: Path) -> None:
    gh = FakeGitHub(tmp_path / "gh")
    state = {**await _workspace(tmp_path), "create_repo": True}
    result = await gh.delivery().deliver(state)
    assert result.repo_created and result.pushed_main
    assert ("POST", "/user/repos") in gh.calls()

    gh2 = FakeGitHub(tmp_path / "gh2")
    with pytest.raises(DeliveryError, match="does not exist"):
        await gh2.delivery().deliver({**state, "create_repo": False})
    assert gh2.heads("octocat", "todo-api") == {}


async def test_redelivery_is_idempotent(tmp_path: Path) -> None:
    gh = FakeGitHub(tmp_path / "gh")
    gh.make_repo("octocat", "todo-api")
    state = await _workspace(tmp_path)
    first = await gh.delivery().deliver(state)
    second = await gh.delivery().deliver(state)
    assert second.pr_url == first.pr_url and not second.pushed_main
    assert len(gh.pulls) == 1
    assert sum(1 for m, p in gh.calls() if m == "POST" and p.endswith("/pulls")) == 1


async def test_non_empty_repo_is_never_touched(tmp_path: Path) -> None:
    gh = FakeGitHub(tmp_path / "gh")
    bare = gh.make_repo("octocat", "todo-api")
    other = tmp_path / "other"
    other_repo = GitRepo(other)
    await other_repo.init()
    (other / "x.txt").write_text("someone else's project\n")
    await other_repo.commit_all("unrelated history")
    await other_repo.run("push", str(bare), "main")
    before = gh.heads("octocat", "todo-api")
    state = await _workspace(tmp_path)
    with pytest.raises(DeliveryError, match="not empty"):
        await gh.delivery().deliver(state)
    assert gh.heads("octocat", "todo-api") == before  # main untouched, branch not pushed
    assert gh.pulls == []


async def test_refuses_without_approval_or_token(tmp_path: Path) -> None:
    gh = FakeGitHub(tmp_path / "gh")
    gh.make_repo("octocat", "todo-api")
    state = await _workspace(tmp_path)
    with pytest.raises(DeliveryError, match="not been approved"):
        await gh.delivery().deliver({**state, "final_approved": False})
    with pytest.raises(DeliveryError, match="GITHUB_TOKEN is not set"):
        await gh.delivery(token="").deliver(state)
    assert gh.heads("octocat", "todo-api") == {} and gh.requests == []


# ----------------------------------------------------------------------------- in the graph
async def _to_final(h: Any, create_repo: bool = False) -> Any:
    await h.driver.start(RUN, "Build a FastAPI TODO API", "octocat/todo-api", create_repo)
    await h.driver.resume(RUN, APPROVE)
    return await h.driver.resume(RUN, APPROVE)


async def test_graph_pushes_only_after_final_approval(tmp_path: Path) -> None:
    gh = FakeGitHub(tmp_path / "gh")
    h = make_harness(tmp_path, github=gh.delivery())
    outcome = await _to_final(h, create_repo=True)
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL
    assert gh.requests == [] and not gh.bare("octocat", "todo-api").exists()  # nothing yet

    outcome = await h.driver.resume(RUN, APPROVE)
    assert outcome.status is RunStatus.COMPLETED
    state = await h.driver.state(RUN)
    assert state["pr_url"] == "https://github.com/octocat/todo-api/pull/1"
    heads = gh.heads("octocat", "todo-api")
    assert set(heads) == {"main", state["integration_branch"]}
    body = gh.pulls[0]["body"]
    assert "# Design" in body and "| T1 | Todo model |" in body and "| T2 | Todo router |" in body
    results = [e.payload for e in await h.events(RUN) if e.payload.get("tool") == "github"]
    assert results[-1]["pr_url"] == state["pr_url"] and results[-1]["repo_created"] is True


async def test_delivery_failure_asks_to_retry_or_skip(tmp_path: Path) -> None:
    gh = FakeGitHub(tmp_path / "gh")
    h = make_harness(tmp_path, github=gh.delivery())
    await _to_final(h, create_repo=False)  # the repository does not exist
    outcome = await h.driver.resume(RUN, APPROVE)
    assert outcome.status is RunStatus.NEEDS_HUMAN
    value = outcome.interrupts[0].value
    assert (
        value["title"] == "GitHub delivery failed" and "does not exist" in value["data"]["reason"]
    )

    gh.make_repo("octocat", "todo-api")  # the user creates it, then retries
    outcome = await h.driver.resume(RUN, APPROVE)
    assert outcome.status is RunStatus.COMPLETED
    assert (await h.driver.state(RUN))["pr_url"].endswith("/pull/1")


async def test_delivery_can_be_skipped(tmp_path: Path) -> None:
    gh = FakeGitHub(tmp_path / "gh")
    gh.fail_status = 403
    h = make_harness(tmp_path, github=gh.delivery())
    await _to_final(h)
    outcome = await h.driver.resume(RUN, APPROVE)
    assert "lacks permission" in outcome.interrupts[0].value["data"]["reason"]
    outcome = await h.driver.resume(RUN, ResumePayload(action="reject", feedback="no PR"))
    assert outcome.status is RunStatus.COMPLETED
    state = await h.driver.state(RUN)
    assert state["pr_url"] is None and "delivery skipped" in state["errors"][-1]


async def test_pr_url_reaches_the_api(tmp_path: Path) -> None:
    gh = FakeGitHub(tmp_path / "gh")
    gh.make_repo("octocat", "todo-api")
    async with api(tmp_path, github=gh.delivery()) as a:
        resp = await a.client.post(
            "/runs", json={"request": "Build a FastAPI TODO API", "repo_target": "octocat/todo-api"}
        )
        run_id = resp.json()["id"]
        await a.settle(run_id)
        for _ in range(3):
            run = await a.approve(run_id)
        assert run["status"] == "completed"
        assert run["pr_url"] == "https://github.com/octocat/todo-api/pull/1"
        listed = (await a.client.get("/runs")).json()
        assert listed[0]["pr_url"] == run["pr_url"]


async def test_git_sees_the_auth_header_only_through_the_environment(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path / "r")
    await repo.init()
    env = git_auth_env("https://github.com", TOKEN)
    seen = await repo.run("config", "--get", "http.https://github.com/.extraheader", extra_env=env)
    assert seen.startswith("AUTHORIZATION: basic ")
    assert TOKEN not in (tmp_path / "r" / ".git" / "config").read_text()
    missing = await repo.run("config", "--get", "http.https://github.com/.extraheader", check=False)
    assert missing == ""  # without the env, git has no credentials at all
