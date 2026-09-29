"""GitHub automation: watched repositories, issue runs and manual issue import (Phase 12)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator

from api.deps import require_admin, require_auth
from devcrew.api.deps import ContainerDep
from devcrew.api.schemas import REPO_RE, CheckRepoResponse, RunSummary
from devcrew.db.models import IssueRun, WatchedRepo
from devcrew.github.client import GitHubError

# Base gate: any authenticated user. Watched-repo mutations (they change
# which repos auto-trigger runs for everyone) are additionally gated by
# require_admin on those specific routes below -- same admin-only-writes
# convention as StoryForge's own ingestion watch-targets router.
router = APIRouter(tags=["github"], dependencies=[Depends(require_auth)])

USER_RE = r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$"
MIN_POLL_S = 60
MAX_POLL_S = 86_400


def _reviewers(values: list[str]) -> list[str]:
    cleaned = sorted({v.strip().lstrip("@") for v in values if v.strip()}, key=str.lower)
    bad = [v for v in cleaned if not re.match(USER_RE, v)]
    if bad:
        raise ValueError(f"not GitHub user names: {', '.join(bad)}")
    return cleaned


class WatchedRepoOut(BaseModel):
    id: int
    repo: str
    enabled: bool
    poll_interval_s: int
    extra_reviewers: list[str]
    last_polled_at: datetime | None
    last_error: str | None

    @classmethod
    def of(cls, row: WatchedRepo) -> WatchedRepoOut:
        return cls(
            id=row.id,
            repo=row.repo,
            enabled=row.enabled,
            poll_interval_s=row.poll_interval_s,
            extra_reviewers=list(row.extra_reviewers or []),
            last_polled_at=row.last_polled_at,
            last_error=row.last_error,
        )


class WatchedRepoCreate(BaseModel):
    repo: str = Field(pattern=REPO_RE, description="owner/repo on GitHub")
    poll_interval_s: int | None = Field(default=None, ge=MIN_POLL_S, le=MAX_POLL_S)
    extra_reviewers: list[str] = Field(default_factory=list)

    @field_validator("extra_reviewers")
    @classmethod
    def _check(cls, v: list[str]) -> list[str]:
        return _reviewers(v)


class WatchedRepoUpdate(BaseModel):
    enabled: bool | None = None
    poll_interval_s: int | None = Field(default=None, ge=MIN_POLL_S, le=MAX_POLL_S)
    extra_reviewers: list[str] | None = None

    @field_validator("extra_reviewers")
    @classmethod
    def _check(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _reviewers(v)


class IssueRunOut(BaseModel):
    id: int
    repo: str
    issue_number: int
    trigger: str
    run_id: str
    run_status: str | None
    comment_id: int | None
    created_at: datetime | None


class ImportIssueRequest(BaseModel):
    repo: str = Field(pattern=REPO_RE)
    number: int = Field(ge=1)
    mode: Literal["full", "quick"] = "full"


def _needs_github(container: ContainerDep) -> None:
    if container.deps.github is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "GitHub automation needs GitHub access: set GITHUB_TOKEN and "
            "GITHUB_DELIVERY_ENABLED=true",
        )


@router.get("/watched-repos", response_model=list[WatchedRepoOut])
async def list_watched(container: ContainerDep) -> list[WatchedRepoOut]:
    return [WatchedRepoOut.of(r) for r in await container.watch.list_repos()]


@router.post(
    "/watched-repos",
    response_model=WatchedRepoOut,
    status_code=201,
    dependencies=[Depends(require_admin)],
)
async def add_watched(body: WatchedRepoCreate, container: ContainerDep) -> WatchedRepoOut:
    if any(r.repo.lower() == body.repo.lower() for r in await container.watch.list_repos()):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{body.repo} is already watched")
    row = await container.watch.add_repo(
        body.repo,
        poll_interval_s=body.poll_interval_s or container.settings.default_poll_interval_s,
        extra_reviewers=body.extra_reviewers,
    )
    return WatchedRepoOut.of(row)


@router.patch(
    "/watched-repos/{repo_id}",
    response_model=WatchedRepoOut,
    dependencies=[Depends(require_admin)],
)
async def update_watched(
    repo_id: int, body: WatchedRepoUpdate, container: ContainerDep
) -> WatchedRepoOut:
    values = body.model_dump(exclude_none=True)
    row = (
        await container.watch.update_repo(repo_id, **values)
        if values
        else await container.watch.get_repo(repo_id)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"watched repository {repo_id} not found")
    return WatchedRepoOut.of(row)


@router.delete("/watched-repos/{repo_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_watched(repo_id: int, container: ContainerDep) -> Response:
    if not await container.watch.delete_repo(repo_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"watched repository {repo_id} not found")
    return Response(status_code=204)


@router.get("/issue-runs", response_model=list[IssueRunOut])
async def list_issue_runs(container: ContainerDep, repo: str | None = None) -> list[IssueRunOut]:
    out = []
    for row in await container.watch.issue_runs(repo):
        run = await container.manager.runs.get(row.run_id)
        out.append(_issue_run(row, run.status if run else None))
    return out


def _issue_run(row: IssueRun, run_status: str | None) -> IssueRunOut:
    return IssueRunOut(
        id=row.id,
        repo=row.repo,
        issue_number=row.issue_number,
        trigger=row.trigger,
        run_id=row.run_id,
        run_status=run_status,
        comment_id=row.comment_id,
        created_at=row.created_at,
    )


@router.post("/issues/import", response_model=RunSummary, status_code=201)
async def import_issue(body: ImportIssueRequest, container: ContainerDep) -> RunSummary:
    """Start a run for one GitHub issue now (without the label)."""
    _needs_github(container)
    try:
        run = await container.watcher.import_issue(body.repo, body.number, body.mode)
    except GitHubError as exc:
        code = status.HTTP_404_NOT_FOUND if exc.status == 404 else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(code, f"could not read {body.repo}#{body.number}: {exc}") from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return RunSummary.of(run, busy=True)


@router.get("/check-repo/{owner}/{repo}", response_model=CheckRepoResponse)
async def check_repo(owner: str, repo: str, container: ContainerDep) -> CheckRepoResponse:
    """Verify a repo exists and our GitHub token can read it, before a real
    DevCrew run is dispatched against it (the Status page's "Test
    Connection" button, ahead of "Send to DevCrew")."""
    _needs_github(container)
    client = container.deps.github.client()
    try:
        repo_data = await client.get_repo(owner, repo)
    except GitHubError as exc:
        code = status.HTTP_404_NOT_FOUND if exc.status == 404 else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(code, str(exc)) from None
    finally:
        await client.aclose()
    if repo_data is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"repository {owner}/{repo} does not exist or the token cannot read it",
        )
    return CheckRepoResponse(
        exists=True,
        default_branch=str(repo_data.get("default_branch") or ""),
        private=bool(repo_data.get("private", False)),
    )
