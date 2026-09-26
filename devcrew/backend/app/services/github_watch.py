"""GitHub automation by polling (no webhooks: DevCrew runs locally).

Every GITHUB_POLL_TICK_S the watcher:
1. polls each enabled watched repository that is due for open issues labelled `devcrew`
   (Full run) or `devcrew:quick` (Quick fix) and starts one run per label event, at most
   MAX_ISSUE_RUNS active at a time (the others wait for the next poll);
2. keeps one DevCrew comment per issue up to date (edited in place, never re-posted);
3. for every run that is watching its pull request, collects new activity (review comments
   from allowed reviewers, failed checks with GitHub Actions logs, merge conflicts, merged or
   closed) and resumes the run with it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.config import Settings
from app.db.models import IssueRun, Run, RunStatus, WatchedRepo
from app.db.watch import WatchStore
from app.github.client import GitHubClient, GitHubError
from app.github.delivery import GitHubDelivery
from app.graph.interrupts import InterruptKind, ResumeAction, ResumePayload
from app.graph.nodes.followup import MARKER, chat_items, followup_state, pr_number
from app.services.run_manager import RunConflictError, RunManager

logger = logging.getLogger(__name__)

FULL_LABEL = "devcrew"
QUICK_LABEL = "devcrew:quick"
WRITE_ROLES = {"admin", "maintain", "write"}
FAILED_CONCLUSIONS = {"failure", "timed_out"}
LOG_TAIL = 6000


def issue_request(issue: dict[str, Any], limit: int) -> str:
    """The run's requirements: the issue title and body, cut to the request size limit."""
    footer = f"\n\n---\nGitHub issue: {issue.get('html_url', '')}"
    text = f"# {issue.get('title', '').strip()}\n\n{(issue.get('body') or '').strip()}"
    if len(text) + len(footer) > limit:
        text = text[: max(0, limit - len(footer) - 40)] + "\n\n[issue text truncated]"
    return text + footer


def comment_text(run: Run, state: dict[str, Any]) -> str:
    """The single DevCrew comment on an issue, derived from the run's status."""
    status = RunStatus(run.status)
    head = f"🤖 **DevCrew** is working on this issue (run `{run.id[:12]}`)."
    pr = state.get("pr_url") or run.pr_url
    if pr and status in (RunStatus.WATCHING, RunStatus.COMPLETED, RunStatus.EXECUTING):
        body = f"Pull request: {pr}"
        if status is RunStatus.WATCHING:
            body += " (following review comments and CI)"
    elif status is RunStatus.COMPLETED:
        body = "Finished without a pull request."
    elif status in (RunStatus.FAILED, RunStatus.CANCELLED):
        body = f"The run {status.value}" + (f": {run.error}" if run.error else ".")
    elif status.value.startswith("awaiting_") or status is RunStatus.NEEDS_HUMAN:
        body = f"Waiting for a human in DevCrew ({status.value.replace('_', ' ')})."
    else:
        body = f"Status: {status.value.replace('_', ' ')}."
    return f"{head}\n\n{body}\n\n{MARKER}"


class GitHubWatcher:
    def __init__(
        self,
        settings: Settings,
        manager: RunManager,
        store: WatchStore,
        github: GitHubDelivery | None,
    ) -> None:
        self.settings = settings
        self.manager = manager
        self.store = store
        self.github = github
        self._lock = asyncio.Lock()

    async def run_forever(self) -> None:
        # the first tick waits one interval: startup recovery resumes runs meanwhile
        while True:
            await asyncio.sleep(self.settings.github_poll_tick_s)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("GitHub watcher tick failed")

    async def tick(self, now: datetime | None = None) -> None:
        if self.github is None:
            return
        async with self._lock:
            now = now or datetime.now(UTC)
            for repo in await self.store.list_repos():
                if repo.enabled and self._due(repo, now):
                    await self.poll_repo(repo, now)
            await self.sync_issue_comments()
            if self.settings.watch_prs:
                await self.follow_prs()

    @staticmethod
    def _due(repo: WatchedRepo, now: datetime) -> bool:
        last = repo.last_polled_at
        if last is None:
            return True
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        return (now - last).total_seconds() >= repo.poll_interval_s

    # ------------------------------------------------------------------------------ issues
    async def active_issue_runs(self) -> int:
        active = 0
        for row in await self.store.issue_runs():
            run = await self.manager.runs.get(row.run_id)
            if run is not None:
                status = RunStatus(run.status)
                if not status.is_terminal and status is not RunStatus.WATCHING:
                    active += 1
        return active

    async def poll_repo(self, repo: WatchedRepo, now: datetime) -> list[str]:
        """Start runs for newly labelled issues; returns the started run ids."""
        assert self.github is not None
        owner, _, name = repo.repo.partition("/")
        started: list[str] = []
        client = self.github.client()
        try:
            issues: dict[int, dict[str, Any]] = {}
            for label in (FULL_LABEL, QUICK_LABEL):
                for issue in await client.labelled_issues(owner, name, label):
                    issues[int(issue["number"])] = issue
            known = await self.store.issue_runs(repo.repo)
            for number, issue in sorted(issues.items()):
                if await self.active_issue_runs() >= self.settings.max_issue_runs:
                    break  # the rest are picked up by a later poll
                trigger, trigger_label = await self._trigger(client, owner, name, number)
                if trigger is None or self._handled(known, number, trigger):
                    continue
                if await self._issue_busy(known, number):
                    continue
                run = await self.start_issue_run(
                    repo.repo,
                    issue,
                    mode="quick" if trigger_label == QUICK_LABEL else "full",
                    trigger=trigger,
                )
                started.append(run.id)
            await self.store.update_repo(repo.id, last_polled_at=now, last_error=None)
        except GitHubError as exc:
            await self.store.update_repo(repo.id, last_polled_at=now, last_error=str(exc)[:500])
        finally:
            await client.aclose()
        return started

    @staticmethod
    async def _trigger(
        client: GitHubClient, owner: str, name: str, number: int
    ) -> tuple[str | None, str | None]:
        """The latest devcrew label event: a new event (label re-added) starts a new run."""
        events = [
            e
            for e in await client.label_events(owner, name, number)
            if (e.get("label") or {}).get("name") in (FULL_LABEL, QUICK_LABEL)
        ]
        if not events:
            return None, None
        latest = max(events, key=lambda e: int(e["id"]))
        return f"label:{latest['id']}", str(latest["label"]["name"])

    @staticmethod
    def _handled(known: Sequence[IssueRun], number: int, trigger: str) -> bool:
        return any(r.issue_number == number and r.trigger == trigger for r in known)

    async def _issue_busy(self, known: Sequence[IssueRun], number: int) -> bool:
        """An issue gets a new run only after its previous run ended."""
        for row in known:
            if row.issue_number != number:
                continue
            run = await self.manager.runs.get(row.run_id)
            if run is not None and not RunStatus(run.status).is_terminal:
                return True
        return False

    async def start_issue_run(
        self, repo: str, issue: dict[str, Any], *, mode: str, trigger: str
    ) -> Run:
        """Start a run for an issue (label trigger or manual import) and record it."""
        number = int(issue["number"])
        run = await self.manager.start(
            request=issue_request(issue, self.settings.max_request_chars),
            repo_target=repo,
            create_repo=False,
            target="existing",
            mode=mode,
            issue={
                "repo": repo,
                "number": number,
                "url": issue.get("html_url"),
                "title": issue.get("title"),
            },
        )
        await self.store.add_issue_run(
            repo=repo, issue_number=number, trigger=trigger, run_id=run.id
        )
        logger.info("started run %s for %s#%s (%s)", run.id, repo, number, mode)
        return run

    async def import_issue(self, repo: str, number: int, mode: str) -> Run:
        """Manual import from the UI: start a run for one issue now."""
        assert self.github is not None
        owner, _, name = repo.partition("/")
        client = self.github.client()
        try:
            issue = await client.get_issue(owner, name, number)
        finally:
            await client.aclose()
        if "pull_request" in issue:
            raise ValueError(f"{repo}#{number} is a pull request, not an issue")
        trigger = f"manual:{datetime.now(UTC).isoformat()}"
        return await self.start_issue_run(repo, issue, mode=mode, trigger=trigger)

    async def sync_issue_comments(self) -> None:
        """Create or edit the single DevCrew comment on each issue that has a run."""
        assert self.github is not None
        rows = await self.store.issue_runs()
        if not rows:
            return
        client = self.github.client()
        try:
            for row in rows:
                run = await self.manager.runs.get(row.run_id)
                if run is None:
                    continue
                state = await self.manager.state(run.id)
                text = comment_text(run, state)
                if text == row.comment_text:
                    continue
                owner, _, name = row.repo.partition("/")
                try:
                    if row.comment_id is None:
                        cid = await client.create_issue_comment(owner, name, row.issue_number, text)
                        await self.store.update_issue_run(row.id, comment_id=cid, comment_text=text)
                    else:
                        await client.update_issue_comment(owner, name, row.comment_id, text)
                        await self.store.update_issue_run(row.id, comment_text=text)
                except GitHubError as exc:
                    logger.warning(
                        "issue comment for %s#%s failed: %s", row.repo, row.issue_number, exc
                    )
        finally:
            await client.aclose()

    # ------------------------------------------------------------------------------ PRs
    async def follow_prs(self) -> None:
        for run in await self.manager.runs.list(500):
            if run.status != RunStatus.WATCHING.value or self.manager.is_busy(run.id):
                continue
            pending = [
                p
                for p in await self.manager.pending(run.id)
                if p.value.get("kind") == InterruptKind.WATCH
            ]
            if not pending:
                continue
            state = await self.manager.state(run.id)
            try:
                activity = await self.pr_activity(run.repo_target, state)
            except GitHubError as exc:
                logger.warning("PR follow-up for run %s failed: %s", run.id, exc)
                continue
            # the owner's chat messages start a round too (Phase 14)
            chat = await chat_items(self.manager.deps, state)
            if chat and (activity is None or activity.get("pr_state") == "open"):
                activity = activity or {"pr_state": "open", "items": []}
                activity["items"] = [*activity["items"], *chat]
            if activity is None:
                continue
            try:
                await self.manager.resume(
                    run.id,
                    ResumePayload(action=ResumeAction.UPDATE, artifact=activity),
                    pending[0].id,
                )
            except RunConflictError:
                continue

    async def _extra_reviewers(self, repo: str) -> set[str]:
        for row in await self.store.list_repos():
            if row.repo == repo:
                return {u.lower() for u in row.extra_reviewers}
        return set()

    async def pr_activity(self, repo: str, state: dict[str, Any]) -> dict[str, Any] | None:
        """New PR activity for a watching run, or None when there is nothing to do."""
        assert self.github is not None
        pr_url = state.get("pr_url")
        if not pr_url:
            return None
        owner, _, name = repo.partition("/")
        number = pr_number(str(pr_url))
        handled = set(followup_state(state)["handled"])
        extra = await self._extra_reviewers(repo)
        client = self.github.client()
        try:
            pr = await client.get_pull(owner, name, number)
            if pr.get("merged"):
                return {"pr_state": "merged", "items": []}
            if pr.get("state") == "closed":
                return {"pr_state": "closed", "items": []}
            roles: dict[str, bool] = {}

            async def allowed(user: str) -> bool:
                if user.lower() in extra:
                    return True
                if user not in roles:
                    roles[user] = await client.permission(owner, name, user) in WRITE_ROLES
                return roles[user]

            items: list[dict[str, Any]] = []

            async def add(kind: str, key: str, c: dict[str, Any], **fields: Any) -> None:
                body = str(c.get("body") or "")
                if key in handled or MARKER in body or not body.strip():
                    return
                user = str((c.get("user") or {}).get("login", ""))
                items.append(
                    {
                        "kind": kind if await allowed(user) else "ignored",
                        "key": key,
                        "user": user,
                        "body": body,
                        **fields,
                    }
                )

            for c in await client.review_comments(owner, name, number):
                await add(
                    "review",
                    f"review:{c['id']}",
                    c,
                    comment_id=c["id"],
                    thread_root=c.get("in_reply_to_id") or c["id"],
                    path=c.get("path"),
                    line=c.get("line"),
                )
            for c in await client.conversation_comments(owner, name, number):
                await add("comment", f"comment:{c['id']}", c, comment_id=c["id"])
            for r in await client.reviews(owner, name, number):
                if r.get("state") in ("CHANGES_REQUESTED", "COMMENTED"):
                    await add("review_body", f"review_body:{r['id']}", r, comment_id=None)

            sha = str((pr.get("head") or {}).get("sha") or "")
            if sha:
                runs = await client.check_runs(owner, name, sha)
                if runs and all(r.get("status") == "completed" for r in runs):
                    for r in runs:
                        key = f"ci:{sha}:{r['id']}"
                        if r.get("conclusion") not in FAILED_CONCLUSIONS or key in handled:
                            continue
                        log = ""
                        if (r.get("app") or {}).get("slug") == "github-actions":
                            try:
                                log = (await client.job_logs(owner, name, int(r["id"])))[-LOG_TAIL:]
                            except GitHubError as exc:
                                log = f"(log unavailable: {exc})"
                        items.append({"kind": "ci", "key": key, "name": r.get("name"), "log": log})
                if pr.get("mergeable") is False and f"conflict:{sha}" not in handled:
                    items.append({"kind": "conflict", "key": f"conflict:{sha}"})
        finally:
            await client.aclose()
        return {"pr_state": "open", "items": items} if items else None
