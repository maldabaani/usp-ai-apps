"""PR follow-up: watch the pull request, triage new activity, implement, reply.

    github_delivery -> watch_pr --(poller: new activity)--> followup --> schedule -> ... -> gates
                          ^  \\--(merged / closed / stop)--> done       \\-> approve_followup (big)
                          |                                               \\-> report_followup
                          +----------------- report_followup <-- github_delivery (push) <--+

- The poller (app/services/github_watch.py) resumes `watch_pr` with new review comments from
  allowed reviewers, failed checks (with GitHub Actions logs) and merge conflicts.
- `followup` triages review comments with the Coordinator model; hard rules make risky changes
  "big". Small items and CI/conflict fixes become tasks right away; big ones wait for the human.
- Tasks run through the normal developer -> review -> QA -> merge -> integration -> gates flow;
  the result is pushed to the same PR branch (never force), then every handled thread gets a
  reply and fixed review threads are resolved.
- At most MAX_PR_ROUNDS automatic rounds; after that everything needs the human.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal, Self

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import Command
from pydantic import BaseModel, ValidationInfo, model_validator

from app.db.models import RunStatus
from app.events.types import EventType
from app.github.client import GitHubError
from app.github.delivery import DeliveryError
from app.graph.interrupts import InterruptKind, InterruptRequest, ResumeAction, request_input
from app.graph.runtime import GraphDeps, NodeFn
from app.graph.state import Design, Plan, PlanTask, Stack, TaskState, dump, get_design, get_plan
from app.llm.models_config import Role
from app.llm.structured import StructuredOutputError, generate_structured
from app.tools.git import GitError, GitRepo

MARKER = "<!-- devcrew -->"
REVIEW_KINDS = ("review", "comment", "review_body")

# Changes a reviewer asks for in these places always need the human.
BIG_PATHS = re.compile(
    r"(^|/)(pyproject\.toml|setup\.py|setup\.cfg|requirements[^/]*\.txt|pom\.xml|build\.gradle"
    r"|package(-lock)?\.json|yarn\.lock|Dockerfile|docker-compose[^/]*|\.github/|\.gitlab-ci"
    r"|Jenkinsfile)|auth|security|secret|crypt|password|token",
    re.IGNORECASE,
)
BIG_WORDS = re.compile(
    r"\b(dependenc|upgrade|bump|add (a |the )?(library|package)|install|migrat|rewrite|redesign)",
    re.IGNORECASE,
)


class TriageItem(BaseModel):
    key: str
    category: Literal["small", "big", "question", "not_actionable"]
    reason: str
    reply: str = ""
    task_title: str = ""
    task_description: str = ""


class Triage(BaseModel):
    items: list[TriageItem]

    @model_validator(mode="after")
    def _one_per_comment(self, info: ValidationInfo) -> Self:
        keys: set[str] | None = (info.context or {}).get("keys")
        if keys is not None:
            got = {i.key for i in self.items}
            if got != keys:
                raise ValueError(
                    f"return exactly one item per comment key; missing {sorted(keys - got)}, "
                    f"unknown {sorted(got - keys)}"
                )
        for i in self.items:
            if i.category in ("small", "big") and not i.task_description.strip():
                raise ValueError(f"{i.key}: {i.category} items need a task_description")
            if i.category in ("question", "not_actionable") and not i.reply.strip():
                raise ValueError(f"{i.key}: {i.category} items need a reply")
        return self


def followup_state(state: dict[str, Any]) -> dict[str, Any]:
    fs = dict(state.get("followup") or {})
    fs.setdefault("round", 0)
    fs.setdefault("handled", [])
    fs.setdefault("pending_replies", [])
    fs.setdefault("round_tasks", {})
    fs.setdefault("ignored", [])
    return fs


def pr_number(pr_url: str) -> int:
    match = re.search(r"/pull/(\d+)", pr_url)
    if not match:
        raise ValueError(f"not a pull request URL: {pr_url}")
    return int(match.group(1))


def forced_big(item: dict[str, Any]) -> str | None:
    """A reason when hard rules make a review comment 'big', else None."""
    if BIG_PATHS.search(str(item.get("path") or "")):
        return f"touches {item['path']} (dependencies, CI or security code need your approval)"
    if BIG_WORDS.search(str(item.get("body") or "")):
        return "asks for a dependency, migration or redesign change (needs your approval)"
    return None


def stack_for(design: Design, plan: Plan, path: str) -> str:
    """The stack whose project contains `path` (for tasks created from comments)."""
    projects = [(p.path, p.stack) for p in design.existing_projects]
    if design.stack is Stack.MIXED and not projects:
        return next(
            (
                t.stack
                for t in plan.tasks
                if any(f.startswith(path.split("/")[0]) for f in t.target_files)
            ),
            plan.tasks[0].stack,
        )
    for prefix, stack in sorted(projects, key=lambda p: -len(p[0])):
        if prefix == "." or path.startswith(prefix.rstrip("/") + "/"):
            return stack
    return design.stack.value if design.stack is not Stack.MIXED else plan.tasks[0].stack


def triage_context(state: dict[str, Any], reviews: list[dict[str, Any]]) -> str:
    plan = state.get("plan") or {}
    files = sorted({f for t in plan.get("tasks", []) for f in t.get("target_files", [])})
    lines = [
        f"Feature request: {str(state.get('request', '')).strip()[:3000]}",
        f"Plan summary: {plan.get('summary', '')}",
        "Files the pull request changes: " + (", ".join(files[:80]) or "(unknown)"),
        "",
        "Review comments:",
    ]
    for r in reviews:
        where = f" on {r['path']}:{r.get('line') or ''}" if r.get("path") else ""
        lines.append(f"- key={r['key']} by @{r.get('user')}{where}:\n  {r.get('body', '').strip()}")
    return "\n".join(lines)


def _reply(item: dict[str, Any], body: str, *, resolve: bool = False) -> dict[str, Any]:
    return {
        "key": item["key"],
        "kind": item["kind"],
        "comment_id": item.get("thread_root") or item.get("comment_id"),
        "body": body,
        "resolve": resolve and item["kind"] == "review",
    }


def make_watch_pr(deps: GraphDeps) -> NodeFn:
    async def watch_pr(state: dict[str, Any]) -> Command[str]:
        fs = followup_state(state)
        payload = request_input(
            InterruptRequest(
                kind=InterruptKind.WATCH,
                title="Watching the pull request",
                allowed_actions=[ResumeAction.UPDATE, ResumeAction.REJECT],
                data={
                    "pr_url": state.get("pr_url"),
                    "round": fs["round"],
                    "max_rounds": deps.settings.max_pr_rounds,
                },
            )
        )
        if payload.action is ResumeAction.REJECT:
            await deps.emit(
                state["run_id"],
                EventType.TOOL_RESULT,
                node="watch_pr",
                tool="watch",
                ok=True,
                result=f"stopped watching: {payload.feedback}",
            )
            return Command(goto="done", update={"followup": {**fs, "closed_as": "stopped"}})
        activity = payload.artifact or {}
        pr_state = activity.get("pr_state", "open")
        if pr_state in ("merged", "closed"):
            await deps.emit(
                state["run_id"],
                EventType.TOOL_RESULT,
                node="watch_pr",
                tool="watch",
                ok=True,
                result=f"pull request {pr_state}",
            )
            return Command(goto="done", update={"followup": {**fs, "closed_as": pr_state}})
        items = [i for i in activity.get("items") or [] if i.get("key") not in fs["handled"]]
        if all(i["kind"] == "ignored" for i in items):
            # comments from users DevCrew does not act on: remember them, keep watching
            fs["handled"] = sorted(set(fs["handled"]) | {i["key"] for i in items})
            fs["ignored"] = fs["ignored"] + [
                f"@{i.get('user')}: {str(i.get('body', ''))[:80]}" for i in items
            ]
            return Command(goto="watch_pr", update={"followup": fs})
        return Command(
            goto="followup",
            update={"followup_items": items, "status": RunStatus.EXECUTING.value},
        )

    return watch_pr


def make_followup(deps: GraphDeps) -> NodeFn:
    async def followup(state: dict[str, Any]) -> Command[str]:
        run_id = state["run_id"]
        fs = followup_state(state)
        number = fs["round"] + 1
        over_limit = number > deps.settings.max_pr_rounds
        items: list[dict[str, Any]] = list(state.get("followup_items") or [])
        reviews = [i for i in items if i["kind"] in REVIEW_KINDS]
        ignored = [i for i in items if i["kind"] == "ignored"]
        plan, design = get_plan(state), get_design(state)

        decisions: dict[str, TriageItem] = {}
        if reviews:
            system = deps.prompts.get("followup_triage", Triage)
            try:
                result = await generate_structured(
                    deps.llm,
                    Role.COORDINATOR,
                    [
                        SystemMessage(content=system),
                        HumanMessage(content=triage_context(state, reviews)),
                    ],
                    Triage,
                    context={"keys": {r["key"] for r in reviews}},
                )
                decisions = {i.key: i for i in result.value.items}
            except StructuredOutputError as exc:
                await deps.emit(
                    run_id,
                    EventType.ERROR,
                    node="followup",
                    message=f"triage failed, asking the human: {exc}",
                )

        proposals: list[dict[str, Any]] = []  # {task, keys, category, reason}
        replies: list[dict[str, Any]] = []
        for i, item in enumerate(reviews, start=1):
            d = decisions.get(item["key"]) or TriageItem(
                key=item["key"],
                category="big",
                reason="could not be triaged automatically",
                task_title="Address review comment",
                task_description=item.get("body", ""),
            )
            if d.category in ("question", "not_actionable"):
                replies.append(_reply(item, d.reply))
                continue
            category = d.category
            reason = d.reason
            forced = forced_big(item)
            if forced and category == "small":
                category, reason = "big", forced
            if over_limit:
                category, reason = (
                    "big",
                    f"the automatic limit of {deps.settings.max_pr_rounds} rounds is reached",
                )
            task = PlanTask(
                id=f"R{number}-{i}",
                title=(d.task_title or "Address review comment")[:120],
                description=(
                    f"Review comment by @{item.get('user')} on {item.get('path') or 'the PR'}:\n"
                    f"{item.get('body', '').strip()}\n\nWhat to do: {d.task_description}"
                ),
                target_files=[item["path"]] if item.get("path") else [],
                depends_on=[],
                stack=stack_for(design, plan, str(item.get("path") or "")),
            )
            proposals.append(
                {
                    "task": dump(task),
                    "keys": [item["key"]],
                    "category": category,
                    "reason": reason,
                    "item": item,
                }
            )

        ci = [i for i in items if i["kind"] == "ci"]
        if ci:
            logs = "\n\n".join(f"### {c['name']}\n{c.get('log', '')[-3000:]}" for c in ci)
            task = PlanTask(
                id=f"R{number}-CI",
                title="Fix failing CI checks",
                description=(
                    "These CI checks failed on the pull request. Reproduce the failure with the "
                    "project's tests, fix the cause (never skip or disable tests) and make sure "
                    f"the tests pass:\n{logs}"
                ),
                target_files=[],
                depends_on=[],
                stack=stack_for(design, plan, ""),
            )
            proposals.append(
                {
                    "task": dump(task),
                    "keys": [c["key"] for c in ci],
                    "category": "big" if over_limit else "small",
                    "reason": "failed checks: " + ", ".join(c["name"] for c in ci),
                    "item": ci[0],
                }
            )

        conflict = next((i for i in items if i["kind"] == "conflict"), None)
        merge_ref = None
        if conflict is not None and deps.github is not None:
            owner, _, name = str(state["repo_target"]).partition("/")
            base = state.get("base_branch") or "main"
            try:
                merge_ref = await deps.github.fetch_base(
                    GitRepo(Path(state["workspace"])), owner, name, base
                )
            except DeliveryError as exc:
                await deps.emit(run_id, EventType.ERROR, node="followup", message=str(exc))
            if merge_ref:
                task = PlanTask(
                    id=f"R{number}-MERGE",
                    title=f"Merge {base} and resolve conflicts",
                    description=(
                        f"The pull request conflicts with {base}. Merge it in and resolve the "
                        "conflicts."
                    ),
                    target_files=[],
                    depends_on=[],
                    stack=stack_for(design, plan, ""),
                )
                proposals.append(
                    {
                        "task": dump(task),
                        "keys": [conflict["key"]],
                        "category": "big" if over_limit else "small",
                        "reason": f"conflicts with {base}",
                        "item": conflict,
                        "merge_from": merge_ref,
                    }
                )

        for p in proposals:
            await deps.emit(
                run_id,
                EventType.TOOL_RESULT,
                node="followup",
                tool="triage",
                ok=True,
                result=f"{p['category']}: {p['task']['title']} ({p['reason']})",
            )
        for r in replies:
            await deps.emit(
                run_id,
                EventType.TOOL_RESULT,
                node="followup",
                tool="triage",
                ok=True,
                result=f"reply: {r['body'][:160]}",
            )

        fs.update(
            round=number,
            handled=sorted(set(fs["handled"]) | {i["key"] for i in items}),
            pending_replies=fs["pending_replies"] + replies,
            ignored=fs["ignored"]
            + [f"@{i.get('user')}: {str(i.get('body', ''))[:80]}" for i in ignored],
        )
        update: dict[str, Any] = {
            "followup": fs,
            "followup_triage": proposals,
            "followup_items": None,
        }
        if any(p["category"] == "big" for p in proposals):
            return Command(
                goto="approve_followup", update={**update, "status": RunStatus.NEEDS_HUMAN.value}
            )
        return start_round(state, fs, proposals, update)

    return followup


def start_round(
    state: dict[str, Any],
    fs: dict[str, Any],
    proposals: list[dict[str, Any]],
    update: dict[str, Any],
) -> Command[str]:
    """Schedule the approved tasks (or go straight to the replies when there are none)."""
    if not proposals:
        return Command(goto="report_followup", update=update)
    plan = get_plan(state)
    tasks = [PlanTask.model_validate(p["task"]) for p in proposals]
    task_states = {
        t.id: dump(TaskState(id=t.id, merge_from=p.get("merge_from")))
        for t, p in zip(tasks, proposals, strict=True)
    }
    fs = {
        **fs,
        "round_tasks": {
            t.id: {"keys": p["keys"], "title": t.title, "items": [p["item"]]}
            for t, p in zip(tasks, proposals, strict=True)
        },
    }
    return Command(
        goto="schedule",
        update={
            **update,
            "followup": fs,
            "plan": dump(plan.model_copy(update={"tasks": [*plan.tasks, *tasks]})),
            "tasks": task_states,
            "followup_active": True,
            "gate_fix_rounds": 0,
            "status": RunStatus.EXECUTING.value,
        },
    )


def make_approve_followup(deps: GraphDeps) -> NodeFn:
    async def approve_followup(state: dict[str, Any]) -> Command[str]:
        fs = followup_state(state)
        proposals: list[dict[str, Any]] = list(state.get("followup_triage") or [])
        big = [p for p in proposals if p["category"] == "big"]
        payload = request_input(
            InterruptRequest(
                kind=InterruptKind.APPROVAL,
                artifact="followup",
                title=f"Approve follow-up changes (round {fs['round']})",
                allowed_actions=[ResumeAction.APPROVE, ResumeAction.REJECT],
                data={
                    "round": fs["round"],
                    "items": [
                        {"task": p["task"], "reason": p["reason"], "item": p["item"]} for p in big
                    ],
                    "small": [p["task"]["title"] for p in proposals if p["category"] == "small"],
                },
            )
        )
        if payload.action is ResumeAction.APPROVE:
            return start_round(state, fs, proposals, {})
        declined = [
            _reply(p["item"], f"Not changed: {payload.feedback}")
            for p in big
            if p["item"]["kind"] in REVIEW_KINDS
        ]
        fs = {**fs, "pending_replies": fs["pending_replies"] + declined}
        small = [p for p in proposals if p["category"] == "small"]
        return start_round(state, fs, small, {"followup": fs})

    return approve_followup


def make_report_followup(deps: GraphDeps) -> NodeFn:
    async def report_followup(state: dict[str, Any]) -> Command[str]:
        fs = followup_state(state)
        replies = list(fs["pending_replies"])
        commit = ""
        if fs["round_tasks"]:
            commit = (await GitRepo(Path(state["workspace"])).head(state["integration_branch"]))[
                :10
            ]
            tasks = state.get("tasks") or {}
            for tid, info in fs["round_tasks"].items():
                ts = tasks.get(tid) or {}
                merged = ts.get("status") == "merged"
                body = (
                    f"Addressed in {commit}: {info['title']}"
                    if merged
                    else "Could not complete this automatically: "
                    + str(ts.get("error") or ts.get("status"))
                )
                for item in info["items"]:
                    if item["kind"] in REVIEW_KINDS:
                        replies.append(_reply(item, body, resolve=merged))
        if deps.github is not None and replies and state.get("pr_url"):
            await post_replies(deps, state, replies)
        fs.update(pending_replies=[], round_tasks={})
        return Command(
            goto="watch_pr",
            update={"followup": fs, "followup_active": False, "status": RunStatus.WATCHING.value},
        )

    return report_followup


async def post_replies(
    deps: GraphDeps, state: dict[str, Any], replies: list[dict[str, Any]]
) -> None:
    assert deps.github is not None
    run_id = state["run_id"]
    owner, _, name = str(state["repo_target"]).partition("/")
    number = pr_number(str(state["pr_url"]))
    client = deps.github.client()
    try:
        threads: list[dict[str, Any]] | None = None
        for r in replies:
            body = f"{r['body']}\n\n{MARKER}"
            try:
                if r["kind"] == "review" and r.get("comment_id"):
                    await client.reply_to_review_comment(
                        owner, name, number, int(r["comment_id"]), body
                    )
                else:
                    await client.create_issue_comment(owner, name, number, body)
                if r.get("resolve") and r.get("comment_id"):
                    if threads is None:
                        threads = await client.review_threads(owner, name, number)
                    for t in threads:
                        if t["comment_id"] == r["comment_id"] and not t["resolved"]:
                            await client.resolve_thread(t["id"])
                await deps.emit(
                    run_id,
                    EventType.TOOL_RESULT,
                    node="report_followup",
                    tool="github",
                    ok=True,
                    result=f"replied: {r['body'][:160]}",
                )
            except (GitHubError, GitError) as exc:
                await deps.emit(
                    run_id,
                    EventType.ERROR,
                    node="report_followup",
                    message=f"could not reply on the PR: {exc}",
                )
    finally:
        await client.aclose()
