"""Phase 12: GitHub automation (issue intake by label / import, issue comments, PR follow-up)."""

from __future__ import annotations

import asyncio
import re
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

from app.graph.nodes.followup import MARKER, forced_big
from app.services.github_watch import comment_text, issue_request
from tests.api_harness import Api, api
from tests.fake_github import FakeGitHub
from tests.fakes import Call, final, tool_call, tool_results
from tests.graph_harness import ESCALATE, Harness, make_harness
from tests.test_existing_repo import GIT, PY_REPO, seeded_repo

REPO = "acme/shop"
NOW = datetime(2026, 9, 1, tzinfo=UTC)
Decide = Callable[[str, str], dict[str, Any]]


def triage_coordinator(decide: Decide) -> Callable[[Call], AIMessage]:
    """The Coordinator: triage on PR comments (decide(key, body) -> item), else escalate."""

    def respond(call: Call) -> AIMessage:
        context = str(call.messages[1].content)
        if "Review comments:" not in context:
            return final(ESCALATE)
        items = []
        for key, body in re.findall(r"- key=(\S+) by @\S+(?: on \S+)?:\n  (.*)", context):
            items.append({"key": key, "reason": "because", **decide(key, body)})
        return final({"items": items})

    return respond


def default_decide(key: str, body: str) -> dict[str, Any]:
    if body.endswith("?"):
        return {"category": "question", "reply": "It keeps the API backwards compatible."}
    if "thanks" in body.lower():
        return {"category": "not_actionable", "reply": "Glad it helps."}
    category = "big" if "redesign" in body.lower() else "small"
    return {"category": category, "task_title": "Apply review", "task_description": body}


def harness(tmp_path: Path, decide: Decide = default_decide, **settings: Any) -> Harness:
    gh = FakeGitHub(tmp_path / "remotes")
    seeded_repo(gh, "acme", "shop", PY_REPO)
    gh.permissions.update({"alice": "write", "bob": "read"})
    settings = {
        "github_delivery_enabled": True,
        "watch_prs": True,
        "github_poll_tick_s": 3600.0,
        "pr_poll_max_interval_s": 0.0,  # check on every poll unless a test enables backoff
        **settings,
    }
    h = make_harness(tmp_path, github=gh.delivery(), **settings)
    h.brain.responders["coordinator"] = triage_coordinator(decide)
    h.gh = gh  # type: ignore[attr-defined]
    return h


def fake(h: Harness) -> FakeGitHub:
    gh: FakeGitHub = h.gh  # type: ignore[attr-defined]
    return gh


async def run_to_pr(a: Api) -> str:
    """A quick-fix run on the existing repository, approved through to its pull request."""
    resp = await a.client.post(
        "/runs",
        json={
            "request": "Add a discount field to todos",
            "repo_target": REPO,
            "target": "existing",
            "mode": "quick",
        },
    )
    assert resp.status_code == 201, resp.text
    run_id = str(resp.json()["id"])
    run = await a.settle(run_id)
    while run["status"] != "watching_pr":
        assert run["pending"], run
        run = await a.approve(run_id)
    return run_id


async def poll(a: Api, run_id: str) -> dict[str, Any]:
    await a.container.watcher.tick(NOW)
    return await a.settle(run_id)


def replies(gh: FakeGitHub) -> list[dict[str, Any]]:
    found = [
        c for cs in gh.review_comments_.values() for c in cs if MARKER in str(c.get("body"))
    ] + [c for cs in gh.issue_comments.values() for c in cs if MARKER in str(c.get("body"))]
    return found


async def workflow(a: Api, run_id: str) -> dict[str, str]:
    wf = (await a.client.get(f"/runs/{run_id}/workflow")).json()
    return {n["id"]: n["status"] for n in wf["nodes"]} | {"_attention": ",".join(wf["attention"])}


# ------------------------------------------------------------------------------ PR follow-up
async def test_small_review_comment_is_fixed_pushed_replied_and_resolved(tmp_path: Path) -> None:
    h = harness(tmp_path)
    gh = fake(h)
    async with api(tmp_path, harness=h) as a:
        run_id = await run_to_pr(a)
        [pr] = gh.pulls
        assert "watch" in (await workflow(a, run_id))
        head_before = gh.heads("acme", "shop")[pr["head"]]
        assert await poll(a, run_id) and not replies(gh)  # nothing new: no round

        comment = gh.add_review_comment("acme", "shop", 1, "alice", "Rename X to TOTAL")
        gh.add_review_comment("acme", "shop", 1, "bob", "Delete everything")  # no write access
        gh.add_conversation_comment("acme", "shop", 1, "alice", "Why a module constant?")
        run = await poll(a, run_id)
        assert run["status"] == "watching_pr"

        # the task ran through the normal flow and was pushed to the same PR branch
        assert run["tasks"]["R1-1"]["status"] == "merged"
        assert gh.heads("acme", "shop")[pr["head"]] != head_before
        log = gh.log("acme", "shop", pr["head"])
        assert log[0].startswith("R1-1:")
        assert len(gh.pulls) == 1  # same PR

        posted = replies(gh)
        review_reply = next(r for r in posted if r.get("in_reply_to_id") == comment["id"])
        assert review_reply["body"].startswith("Addressed in ")
        question = next(r for r in posted if "backwards compatible" in r["body"])
        assert "in_reply_to_id" not in question  # a conversation comment
        assert not any("Delete everything" in r["body"] for r in posted)
        assert gh.threads[f"T_{comment['id']}"]["resolved"] is True
        assert run["followup"]["round"] == 1
        assert any("Delete everything" in i for i in run["followup"]["ignored"])

        wf = await workflow(a, run_id)
        assert wf["followup:1"] == "done" and wf["task:R1-1"] == "done"
        assert wf["push:1"] == "done" and wf["watch"] == "running"
        assert wf["delivery"] == "done" and wf["_attention"] == ""

        # our own replies are never picked up again: the next poll starts no round
        run = await poll(a, run_id)
        assert run["followup"]["round"] == 1


async def test_big_change_waits_for_approval_and_rejection_replies(tmp_path: Path) -> None:
    h = harness(tmp_path)
    gh = fake(h)
    async with api(tmp_path, harness=h) as a:
        run_id = await run_to_pr(a)
        big = gh.add_review_comment("acme", "shop", 1, "alice", "Please redesign the module")
        # the model calls it small, but the hard rules make dependency changes big
        dep = gh.add_review_comment(
            "acme", "shop", 1, "alice", "Also bump fastapi", path="pyproject.toml"
        )
        run = await poll(a, run_id)
        assert run["status"] == "needs_human"
        [pending] = run["pending"]
        assert pending["artifact"] == "followup"
        reasons = {i["item"]["key"]: i["reason"] for i in pending["data"]["items"]}
        assert set(reasons) == {f"review:{big['id']}", f"review:{dep['id']}"}
        assert "pyproject.toml" in reasons[f"review:{dep['id']}"]
        wf = await workflow(a, run_id)
        assert wf["followup:1"] == "waiting" and wf["_attention"] == "followup:1"

        resp = await a.client.post(
            f"/runs/{run_id}/resume",
            json={"action": "reject", "feedback": "out of scope for this PR"},
        )
        assert resp.status_code == 202
        run = await a.settle(run_id)
        assert run["status"] == "watching_pr"
        bodies = [r["body"] for r in replies(gh)]
        assert bodies.count("Not changed: out of scope for this PR\n\n" + MARKER) == 2
        assert "R1-1" not in run["tasks"]


async def test_approved_big_change_runs_as_a_round(tmp_path: Path) -> None:
    h = harness(tmp_path)
    gh = fake(h)
    async with api(tmp_path, harness=h) as a:
        run_id = await run_to_pr(a)
        gh.add_review_comment("acme", "shop", 1, "alice", "Please redesign the module")
        await poll(a, run_id)
        run = await a.approve(run_id)
        assert run["status"] == "watching_pr"
        assert run["tasks"]["R1-1"]["status"] == "merged"
        assert any(r["body"].startswith("Addressed in") for r in replies(gh))


async def test_failed_ci_becomes_a_fix_task_with_the_job_log(tmp_path: Path) -> None:
    h = harness(tmp_path)
    gh = fake(h)
    async with api(tmp_path, harness=h) as a:
        run_id = await run_to_pr(a)
        head = gh.heads("acme", "shop")[gh.pulls[0]["head"]]
        gh.set_checks(head, ("lint", "in_progress", ""), ("tests", "completed", "failure"))
        run = await poll(a, run_id)
        assert "R1-CI" not in run["tasks"]  # checks still running: wait
        gh.set_checks(head, ("lint", "completed", "success"), ("tests", "completed", "failure"))
        run = await poll(a, run_id)
        assert run["tasks"]["R1-CI"]["status"] == "merged"
        dev = [c for c in h.brain.calls_for("developer") if "R1-CI" in str(c.messages[1].content)]
        assert "FAILED tests/test_x.py::test_y" in str(dev[0].messages[1].content)
        assert run["status"] == "watching_pr"
        # the same failure is not handled twice
        run = await poll(a, run_id)
        assert run["followup"]["round"] == 1


def push_to_master(gh: FakeGitHub, work: Path, files: dict[str, str]) -> None:
    """Someone else pushes to the base branch."""
    subprocess.run(["git", "clone", "-q", str(gh.bare("acme", "shop")), str(work)], check=True)
    for rel, text in files.items():
        (work / rel).write_text(text)
    subprocess.run(["git", *GIT, "-C", str(work), "add", "-A"], check=True)
    subprocess.run(["git", *GIT, "-C", str(work), "commit", "-q", "-m", "news"], check=True)
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "master"], check=True)


async def test_merge_conflict_merges_the_base_branch_in(tmp_path: Path) -> None:
    h = harness(tmp_path)
    gh = fake(h)
    base_developer = h.brain.responders["developer"]

    def developer(call: Call) -> AIMessage:
        prompt = str(call.messages[1].content)
        if "id: R1-MERGE" in prompt and not tool_results(call):
            # the conflicted file is named in the feedback; resolve it
            assert "app/schemas/todo.py" in prompt
            return tool_call("write_file", path="app/schemas/todo.py", content="X = 1\nY = 2\n")
        return base_developer(call)

    h.brain.responders["developer"] = developer
    async with api(tmp_path, harness=h) as a:
        run_id = await run_to_pr(a)
        # master changes the same file as the PR meanwhile
        await asyncio.to_thread(
            push_to_master, gh, tmp_path / "other", {"app/schemas/todo.py": "Y = 2\n"}
        )
        gh.pulls[0]["mergeable"] = False
        run = await poll(a, run_id)
        assert run["tasks"]["R1-MERGE"]["status"] == "merged", run["tasks"]["R1-MERGE"]
        log = gh.log("acme", "shop", gh.pulls[0]["head"])
        assert "news" in log  # master's commit is in the PR branch (a merge, no force push)
        assert run["status"] == "watching_pr"


async def test_round_limit_needs_the_human(tmp_path: Path) -> None:
    h = harness(tmp_path, max_pr_rounds=1)
    gh = fake(h)
    async with api(tmp_path, harness=h) as a:
        run_id = await run_to_pr(a)
        gh.add_review_comment("acme", "shop", 1, "alice", "Rename X to A")
        run = await poll(a, run_id)
        assert run["status"] == "watching_pr" and run["followup"]["round"] == 1
        gh.add_review_comment("acme", "shop", 1, "alice", "Rename A to B")
        run = await poll(a, run_id)
        assert run["status"] == "needs_human"
        assert "limit of 1 rounds" in run["pending"][0]["data"]["items"][0]["reason"]


async def test_merged_closed_and_stop_watching_end_the_run(tmp_path: Path) -> None:
    for how in ("merged", "closed", "stop"):
        h = harness(tmp_path / how)
        gh = fake(h)
        async with api(tmp_path / how, harness=h) as a:
            run_id = await run_to_pr(a)
            if how == "stop":
                resp = await a.client.post(
                    f"/runs/{run_id}/resume", json={"action": "reject", "feedback": "done here"}
                )
                assert resp.status_code == 202
                run = await a.settle(run_id)
            else:
                gh.pulls[0]["merged" if how == "merged" else "state"] = (
                    True if how == "merged" else "closed"
                )
                run = await poll(a, run_id)
            assert run["status"] == "completed"
            assert run["followup"]["closed_as"] == ("stopped" if how == "stop" else how)
            wf = await workflow(a, run_id)
            assert wf["watch"] == "done"


async def test_clients_cannot_send_pr_activity(tmp_path: Path) -> None:
    h = harness(tmp_path)
    async with api(tmp_path, harness=h) as a:
        run_id = await run_to_pr(a)
        resp = await a.client.post(
            f"/runs/{run_id}/resume", json={"action": "update", "artifact": {"items": []}}
        )
        assert resp.status_code == 422


def test_hard_rules_mark_risky_comments_big() -> None:
    assert forced_big({"path": ".github/workflows/ci.yml", "body": "x"})
    assert forced_big({"path": "app/auth/login.py", "body": "x"})
    assert forced_big({"path": "app/x.py", "body": "Can you add a migration for this?"})
    assert forced_big({"path": "app/x.py", "body": "Rename the variable"}) is None


# ------------------------------------------------------------------------------ issue intake
async def watch(a: Api, **body: Any) -> dict[str, Any]:
    resp = await a.client.post("/watched-repos", json={"repo": REPO, **body})
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


async def test_watched_repos_crud(tmp_path: Path) -> None:
    async with api(tmp_path, harness=harness(tmp_path)) as a:
        row = await watch(a, extra_reviewers=["@Carol", "dave", "dave"])
        assert row["poll_interval_s"] == 300 and row["enabled"] is True
        assert row["extra_reviewers"] == ["Carol", "dave"]
        dup = await a.client.post("/watched-repos", json={"repo": "ACME/shop"})
        assert dup.status_code == 409
        bad = await a.client.post("/watched-repos", json={"repo": "no slash"})
        assert bad.status_code == 422
        bad = await a.client.post(
            "/watched-repos", json={"repo": "a/b", "extra_reviewers": ["not a user!"]}
        )
        assert bad.status_code == 422
        resp = await a.client.patch(
            f"/watched-repos/{row['id']}", json={"enabled": False, "poll_interval_s": 60}
        )
        assert resp.json()["enabled"] is False and resp.json()["poll_interval_s"] == 60
        assert (await a.client.patch("/watched-repos/999", json={})).status_code == 404
        assert (await a.client.delete(f"/watched-repos/{row['id']}")).status_code == 204
        assert (await a.client.get("/watched-repos")).json() == []


async def test_labelled_issues_start_runs_with_the_mode_from_the_label(tmp_path: Path) -> None:
    h = harness(tmp_path, max_issue_runs=2)
    gh = fake(h)
    gh.add_issue("acme", "shop", "Add discounts", "Todos need a discount.", ["devcrew"])
    gh.add_issue("acme", "shop", "Fix typo", "README typo.", ["devcrew:quick", "bug"])
    gh.add_issue("acme", "shop", "Third", "Waits for a free slot.", ["devcrew"])
    gh.add_issue("acme", "shop", "Unrelated", "No label.", ["bug"])
    async with api(tmp_path, harness=h) as a:
        await watch(a)
        await a.container.watcher.tick(NOW)
        rows = (await a.client.get("/issue-runs")).json()
        assert sorted(r["issue_number"] for r in rows) == [1, 2]  # MAX_ISSUE_RUNS = 2
        by_issue = {r["issue_number"]: r for r in rows}
        for r in rows:
            await a.container.manager.wait(r["run_id"])
        full = (await a.client.get(f"/runs/{by_issue[1]['run_id']}")).json()
        quick = (await a.client.get(f"/runs/{by_issue[2]['run_id']}")).json()
        assert full["mode"] == "full" and quick["mode"] == "quick"
        assert full["status"] == "awaiting_plan_approval"  # still stops at plan approval
        assert full["request"].startswith("# Add discounts") and "issues/1" in full["request"]
        assert full["issue"] == {
            "repo": REPO,
            "number": 1,
            "url": "https://github.com/acme/shop/issues/1",
            "title": "Add discounts",
        }
        # the issue got one DevCrew comment, posted when the run started
        [c1] = gh.issue_comments[("acme", "shop", 1)]
        assert "Status: pending" in c1["body"] and MARKER in c1["body"]

        # not due yet: no new poll; the comment is edited in place, never re-posted
        await a.container.watcher.tick(NOW + timedelta(seconds=10))
        assert len((await a.client.get("/issue-runs")).json()) == 2
        [c1] = gh.issue_comments[("acme", "shop", 1)]
        assert "Waiting for a human in DevCrew (awaiting plan approval)" in c1["body"]
        await a.client.post(f"/runs/{by_issue[1]['run_id']}/cancel")
        await a.container.watcher.tick(NOW + timedelta(seconds=20))
        [c1] = gh.issue_comments[("acme", "shop", 1)]
        assert "cancelled" in c1["body"]

        # a slot is free after the cancel: the third issue starts on the next due poll
        await a.container.watcher.tick(NOW + timedelta(seconds=301))
        rows = (await a.client.get("/issue-runs")).json()
        assert sorted(r["issue_number"] for r in rows) == [1, 2, 3]
        for r in rows:
            await a.container.manager.wait(r["run_id"])

        # re-adding the label on a finished issue starts a new run; the same event does not
        gh.label("acme", "shop", 1, "devcrew")
        await a.container.watcher.tick(NOW + timedelta(seconds=602))
        rows = (await a.client.get("/issue-runs")).json()
        assert [r["issue_number"] for r in rows].count(1) == 1  # 2 runs active: no slot
        await a.client.post(f"/runs/{by_issue[2]['run_id']}/cancel")
        await a.container.watcher.tick(NOW + timedelta(seconds=903))
        rows = (await a.client.get("/issue-runs")).json()
        assert [r["issue_number"] for r in rows].count(1) == 2
        for r in rows:
            await a.container.manager.wait(r["run_id"])
        await a.container.watcher.tick(NOW + timedelta(seconds=1204))
        assert [r["issue_number"] for r in (await a.client.get("/issue-runs")).json()].count(1) == 2


async def test_poll_errors_are_recorded(tmp_path: Path) -> None:
    h = harness(tmp_path)
    gh = fake(h)
    async with api(tmp_path, harness=h) as a:
        await watch(a)
        gh.fail_status = 401
        await a.container.watcher.tick(NOW)
        [row] = (await a.client.get("/watched-repos")).json()
        assert row["last_error"] and row["last_polled_at"]


async def test_manual_issue_import(tmp_path: Path) -> None:
    h = harness(tmp_path)
    gh = fake(h)
    gh.add_issue("acme", "shop", "Import me", "Body text.", [])
    async with api(tmp_path, harness=h) as a:
        resp = await a.client.post("/issues/import", json={"repo": REPO, "number": 1})
        assert resp.status_code == 201, resp.text
        run = await a.settle(resp.json()["id"])
        assert run["issue"]["number"] == 1 and run["mode"] == "full"
        [row] = (await a.client.get("/issue-runs")).json()
        assert row["trigger"].startswith("manual:") and row["run_status"] == run["status"]
        missing = await a.client.post("/issues/import", json={"repo": REPO, "number": 9})
        assert missing.status_code in (404, 502)


def test_issue_request_is_bounded_and_links_the_issue() -> None:
    issue = {"title": "T", "body": "x" * 500, "html_url": "https://github.com/a/b/issues/3"}
    text = issue_request(issue, 200)
    assert len(text) <= 200 and text.endswith("https://github.com/a/b/issues/3")
    assert "[issue text truncated]" in text


def test_comment_text_follows_the_run_status() -> None:
    from app.db.models import Run

    run = Run(id="r" * 32, status="watching_pr", pr_url="https://github.com/a/b/pull/1")
    assert "Pull request: https://github.com/a/b/pull/1 (following" in comment_text(run, {})
    run.status, run.pr_url, run.error = "failed", None, "boom"
    assert "The run failed: boom" in comment_text(run, {})


async def test_chat_messages_while_watching_start_a_round(tmp_path: Path) -> None:
    h = harness(tmp_path)
    gh = fake(h)
    async with api(tmp_path, harness=h) as a:
        run_id = await run_to_pr(a)
        sent = []
        for text in ("Please redesign the store as a class", "Why a module constant?"):
            resp = await a.client.post(f"/runs/{run_id}/messages", json={"text": text})
            assert resp.status_code == 201
            sent.append(resp.json()["id"])
        run = await poll(a, run_id)
        # the owner's request is not "big": they asked for it themselves
        assert run["status"] == "watching_pr"
        assert run["tasks"]["R1-1"]["status"] == "merged"
        assert gh.log("acme", "shop", gh.pulls[0]["head"])[0].startswith("R1-1:")
        rows = {m["id"]: m for m in (await a.client.get(f"/runs/{run_id}/messages")).json()}
        assert rows[sent[0]]["reply"].startswith("Addressed in ")
        assert rows[sent[1]]["reply"] == "It keeps the API backwards compatible."
        assert not replies(gh)  # answered in DevCrew's chat, never on GitHub
        run = await poll(a, run_id)
        assert run["followup"]["round"] == 1  # each message is used once


# ------------------------------------------------------------------------------ Phase 15
async def test_quiet_prs_use_conditional_requests_and_back_off(tmp_path: Path) -> None:
    h = harness(tmp_path, pr_poll_max_interval_s=120.0, github_poll_tick_s=30.0)
    gh = fake(h)
    async with api(tmp_path, harness=h) as a:
        run_id = await run_to_pr(a)
        watcher = a.container.watcher

        async def poll_at(seconds: float) -> int:
            before = len(gh.requests)
            await watcher.follow_prs(NOW + timedelta(seconds=seconds))
            await a.settle(run_id)
            return len(gh.requests) - before

        assert await poll_at(0) > 0  # first check: full responses, ETags stored
        assert await poll_at(10) == 0  # quiet: next check after one tick (30 s)
        unchanged = gh.not_modified
        assert await poll_at(31) > 0
        assert gh.not_modified > unchanged  # nothing changed: GitHub answers 304
        assert await poll_at(80) == 0  # the wait doubled to 60 s
        assert await poll_at(92) > 0
        # activity resets the wait
        gh.add_review_comment("acme", "shop", 1, "alice", "Rename X to TOTAL")
        await poll_at(300)
        run = await a.settle(run_id)
        assert run["followup"]["round"] == 1
        assert await poll_at(301) > 0


async def test_edited_review_comments_are_handled_again(tmp_path: Path) -> None:
    h = harness(tmp_path)
    gh = fake(h)
    async with api(tmp_path, harness=h) as a:
        run_id = await run_to_pr(a)
        comment = gh.add_review_comment("acme", "shop", 1, "alice", "Rename X to TOTAL")
        run = await poll(a, run_id)
        assert run["followup"]["round"] == 1
        gh.edit_review_comment(comment["id"], "Rename X to GRAND_TOTAL instead")
        run = await poll(a, run_id)
        assert run["followup"]["round"] == 2
        developer = [
            str(c.messages[1].content)
            for c in h.brain.calls_for("developer")
            if "R2-1" in str(c.messages[1].content)
        ]
        assert "GRAND_TOTAL" in developer[0]
        run = await poll(a, run_id)
        assert run["followup"]["round"] == 2  # the same edit is handled once


async def test_commit_statuses_and_other_ci_apps_become_fix_tasks(tmp_path: Path) -> None:
    h = harness(tmp_path)
    gh = fake(h)
    async with api(tmp_path, harness=h) as a:
        run_id = await run_to_pr(a)
        head = gh.heads("acme", "shop")[gh.pulls[0]["head"]]
        gh.set_status(head, ("jenkins", "pending"))
        run = await poll(a, run_id)
        assert "R1-CI" not in run["tasks"]  # still running
        gh.set_status(head, ("jenkins", "failure"), ("lint", "success"))
        gh.set_checks(head, ("sonar", "completed", "failure"))
        gh.checks[head][0]["app"] = {"slug": "sonarcloud"}
        gh.checks[head][0]["details_url"] = "https://sonar.example.test/r/1"
        gh.checks[head][0]["output"] = {"title": "Quality gate failed", "summary": "2 bugs"}
        run = await poll(a, run_id)
        assert run["tasks"]["R1-CI"]["status"] == "merged"
        task = next(t for t in run["plan"]["tasks"] if t["id"] == "R1-CI")
        assert (
            "jenkins" in task["description"]
            and "https://ci.example.test/jenkins" in task["description"]
        )
        assert "Quality gate failed" in task["description"]
        assert "https://sonar.example.test/r/1" in task["description"]


async def test_each_push_refreshes_the_pr_description(tmp_path: Path) -> None:
    h = harness(tmp_path)
    gh = fake(h)
    async with api(tmp_path, harness=h) as a:
        run_id = await run_to_pr(a)
        assert "R1-1" not in gh.pulls[0]["body"]
        gh.add_review_comment("acme", "shop", 1, "alice", "Rename X to TOTAL")
        await poll(a, run_id)
        assert "| R1-1 |" in gh.pulls[0]["body"]
        assert ("PATCH", "/repos/acme/shop/pulls/1") in gh.calls()


def test_gate_fixes_inside_a_round_belong_to_that_round() -> None:
    from app.gates.checks import GateReport
    from app.graph.nodes.gates import gate_fix_task
    from app.graph.state import Plan
    from app.services.workflow import task_round
    from tests.graph_harness import PLAN

    plan = Plan.model_validate(PLAN)
    assert gate_fix_task(plan, GateReport(), 1).id == "GATEFIX1"
    task = gate_fix_task(plan, GateReport(), 1, "R2-")
    assert task.id == "R2-GATEFIX1" and task_round(task.id) == 2


async def test_removing_the_label_cancels_a_run_before_plan_approval(tmp_path: Path) -> None:
    h = harness(tmp_path)
    gh = fake(h)
    gh.add_issue("acme", "shop", "Add discounts", "Todos need a discount.", ["devcrew"])
    gh.add_issue("acme", "shop", "Fix typo", "README typo.", ["devcrew:quick"])
    gh.add_issue("acme", "shop", "Manual", "Imported by hand.", [])
    async with api(tmp_path, harness=h) as a:
        await watch(a)
        await a.container.watcher.tick(NOW)
        imported = await a.client.post("/issues/import", json={"repo": REPO, "number": 3})
        runs = {r["issue_number"]: r["run_id"] for r in (await a.client.get("/issue-runs")).json()}
        for run_id in runs.values():
            await a.container.manager.wait(run_id)
        assert imported.status_code == 201
        await a.approve(runs[2])  # issue 2's plan is approved: it keeps going when unlabelled
        gh.unlabel("acme", "shop", 1, "devcrew")
        gh.unlabel("acme", "shop", 2, "devcrew:quick")
        await a.container.watcher.tick(NOW + timedelta(seconds=301))
        statuses = {n: (await a.client.get(f"/runs/{r}")).json()["status"] for n, r in runs.items()}
        assert statuses[1] == "cancelled"
        assert statuses[2] != "cancelled" and statuses[3] != "cancelled"
        events = [e async for e in a.container.events.replay(runs[1])]
        assert any("label was removed" in str(e.payload.get("message")) for e in events)
