"""Deliver an approved run: (create repo), push, open a pull request.

Rules (from the spec):
- Only called after final approval (the graph gate enforces it; this module re-checks).
- New projects: `main` is pushed ONLY when the remote repository is empty, and then only the
  template scaffold commit. Otherwise `main` is never touched (no force, no update).
- Existing repositories (Phase 11): the base branch is never pushed; the run branch is based
  on it and the PR targets it.
- The run's branch `devcrew/<run_id>-<slug>` is pushed and a PR is opened against the base.
  Re-delivery is idempotent: an already pushed branch and an open PR are reused.
- The token never appears in URLs, git config or process arguments: git receives it as an
  HTTP header through environment-based config (scoped to the GitHub host).
"""

from __future__ import annotations

import base64
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.github.client import EtagCache, GitHubClient, GitHubError
from app.github.pr_body import pr_body, pr_title
from app.tools.git import GitError, GitRepo

BASE_BRANCH = "main"

Progress = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class DeliveryResult:
    pr_url: str
    repo_created: bool
    pushed_main: bool
    branch: str


class DeliveryError(RuntimeError):
    pass


def git_auth_env(git_url: str, token: str) -> dict[str, str]:
    """Header-based auth for git over HTTPS, via GIT_CONFIG_* environment variables."""
    parsed = urlparse(git_url)
    if parsed.scheme not in ("http", "https"):
        return {}  # e.g. file:// remotes in tests
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"http.{parsed.scheme}://{parsed.netloc}/.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
    }


class GitHubDelivery:
    def __init__(
        self,
        client_factory: Callable[[], GitHubClient],
        *,
        token: str,
        git_url: str = "https://github.com",
    ) -> None:
        self._client_factory = client_factory
        self._token = token
        self.cache = EtagCache()
        self._git_url = git_url.rstrip("/")

    def remote_url(self, owner: str, repo: str) -> str:
        return f"{self._git_url}/{owner}/{repo}.git"

    async def _git(self, repo: GitRepo, *args: str) -> str:
        try:
            return await repo.run(*args, extra_env=git_auth_env(self._git_url, self._token))
        except GitError as exc:
            raise DeliveryError(str(exc).replace(self._token, "***")) from exc

    async def _remote_heads(self, repo: GitRepo, url: str) -> dict[str, str]:
        out = await self._git(repo, "ls-remote", "--heads", url)
        heads = {}
        for line in out.splitlines():
            sha, _, ref = line.partition("\t")
            if ref.startswith("refs/heads/"):
                heads[ref.removeprefix("refs/heads/")] = sha
        return heads

    def client(self) -> GitHubClient:
        """A new API client (the caller closes it); all clients share one ETag cache."""
        client = self._client_factory()
        if client.cache is None:
            client.cache = self.cache
        return client

    async def fetch_base(self, repo: GitRepo, owner: str, name: str, base: str) -> str:
        """Fetch the PR base branch into a local ref and return that ref."""
        ref = f"refs/devcrew/base/{base}"
        await self._git(repo, "fetch", "--no-tags", self.remote_url(owner, name), f"+{base}:{ref}")
        return ref

    async def default_branch(self, owner: str, name: str) -> str:
        client = self.client()
        try:
            repo = await client.get_repo(owner, name)
        except GitHubError as exc:
            raise DeliveryError(str(exc)) from exc
        finally:
            await client.aclose()
        if repo is None:
            raise DeliveryError(
                f"repository {owner}/{name} does not exist or the token cannot read it"
            )
        return str(repo.get("default_branch") or BASE_BRANCH)

    async def clone(self, owner: str, name: str, branch: str, dest: Path) -> None:
        """Clone `branch` of an existing repository into `dest` (read access only)."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        await self._git(
            GitRepo(dest.parent),
            "clone",
            "--no-tags",
            "--branch",
            branch,
            self.remote_url(owner, name),
            str(dest),
        )

    async def deliver(
        self, state: Mapping[str, Any], *, progress: Progress | None = None
    ) -> DeliveryResult:
        async def say(message: str) -> None:
            if progress is not None:
                await progress(message)

        if not state.get("final_approved"):
            raise DeliveryError("refusing to push: the final result has not been approved")
        if not self._token:
            raise DeliveryError("GITHUB_TOKEN is not set: add it to .env and restart the backend")
        owner, _, name = str(state["repo_target"]).partition("/")
        branch = str(state["integration_branch"])
        repo = GitRepo(Path(state["workspace"]))
        if state.get("target") == "existing":
            return await self._deliver_existing(state, owner, name, branch, repo, say)
        client = self.client()
        try:
            created = False
            if await client.get_repo(owner, name) is None:
                if not state.get("create_repo"):
                    raise DeliveryError(
                        f"repository {owner}/{name} does not exist (or the token cannot see it); "
                        "start the run with 'create repo if missing', create it on GitHub, or "
                        "fix the token and retry"
                    )
                await say(f"creating private repository {owner}/{name}")
                await client.create_repo(
                    owner, name, description=pr_title(str(state.get("request", "")))
                )
                created = True

            url = self.remote_url(owner, name)
            heads = await self._remote_heads(repo, url)
            local_main = (await repo.run("rev-parse", f"refs/heads/{BASE_BRANCH}")).strip()
            pushed_main = False
            if not heads:
                # Empty repository: the scaffold commit becomes the base branch.
                await say(f"pushing the template scaffold to {BASE_BRANCH} (empty repository)")
                await self._git(
                    repo, "push", url, f"refs/heads/{BASE_BRANCH}:refs/heads/{BASE_BRANCH}"
                )
                pushed_main = True
            elif heads.get(BASE_BRANCH) != local_main:
                raise DeliveryError(
                    f"{owner}/{name} is not empty and its {BASE_BRANCH} branch is not this run's "
                    "scaffold. DevCrew builds greenfield projects: deliver to an empty or new "
                    "repository."
                )

            await say(f"pushing {branch}")
            await self._git(repo, "push", url, f"refs/heads/{branch}:refs/heads/{branch}")

            pr_url = await client.find_open_pr(owner, name, branch, BASE_BRANCH)
            if pr_url is not None:
                await self._refresh_pr(client, owner, name, pr_url, state, say)
            else:
                await say("opening the pull request")
                pr_url = await client.create_pr(
                    owner,
                    name,
                    title=pr_title(str(state.get("request", ""))),
                    head=branch,
                    base=BASE_BRANCH,
                    body=pr_body(state),
                )
            return DeliveryResult(
                pr_url=pr_url, repo_created=created, pushed_main=pushed_main, branch=branch
            )
        except GitHubError as exc:
            raise DeliveryError(str(exc)) from exc
        finally:
            await client.aclose()

    @staticmethod
    async def _refresh_pr(
        client: GitHubClient,
        owner: str,
        name: str,
        pr_url: str,
        state: Mapping[str, Any],
        say: Callable[[str], Awaitable[None]],
    ) -> None:
        """Re-delivery (retry, final-approval follow-up, PR round): keep the description in
        step with the run instead of leaving the first version."""
        match = re.search(r"/pull/(\d+)", pr_url)
        if match is None:
            return
        await say("updating the pull request description")
        await client.update_pr(
            owner,
            name,
            int(match.group(1)),
            title=pr_title(str(state.get("request", ""))),
            body=pr_body(state),
        )

    async def _deliver_existing(
        self,
        state: Mapping[str, Any],
        owner: str,
        name: str,
        branch: str,
        repo: GitRepo,
        say: Callable[[str], Awaitable[None]],
    ) -> DeliveryResult:
        base = str(state.get("base_branch") or BASE_BRANCH)
        if branch == base:
            raise DeliveryError(f"refusing to push the base branch {base}")
        url = self.remote_url(owner, name)
        client = self.client()
        try:
            heads = await self._remote_heads(repo, url)
            if base not in heads:
                raise DeliveryError(f"{owner}/{name} has no branch {base} to open the PR against")
            await say(f"pushing {branch}")
            await self._git(repo, "push", url, f"refs/heads/{branch}:refs/heads/{branch}")
            pr_url = await client.find_open_pr(owner, name, branch, base)
            if pr_url is not None:
                await self._refresh_pr(client, owner, name, pr_url, state, say)
            else:
                await say(f"opening the pull request against {base}")
                pr_url = await client.create_pr(
                    owner,
                    name,
                    title=pr_title(str(state.get("request", ""))),
                    head=branch,
                    base=base,
                    body=pr_body(state),
                )
            return DeliveryResult(
                pr_url=pr_url, repo_created=False, pushed_main=False, branch=branch
            )
        except GitHubError as exc:
            raise DeliveryError(str(exc)) from exc
        finally:
            await client.aclose()
