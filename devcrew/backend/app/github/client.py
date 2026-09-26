"""Minimal GitHub REST client (only the calls DevCrew needs)."""

from __future__ import annotations

from typing import Any

import httpx

API_VERSION = "2022-11-28"


class GitHubError(RuntimeError):
    """A GitHub failure with an actionable message (never contains the token)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


HINTS = {
    401: "GITHUB_TOKEN was rejected. Create a new token and update .env.",
    403: "GITHUB_TOKEN lacks permission (needs repo scope, or Contents + Pull requests "
    "read/write; Administration to create repositories), or you hit a rate limit.",
    404: "not found (or the token cannot see it)",
    422: "GitHub rejected the request",
}


class GitHubClient:
    def __init__(
        self,
        token: str,
        api_url: str = "https://api.github.com",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token = token
        self._http = httpx.AsyncClient(
            base_url=api_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "devcrew",
            },
            timeout=30,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, *, ok404: bool = False, **kwargs: Any) -> Any:
        try:
            resp = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise GitHubError(f"cannot reach GitHub ({type(exc).__name__}: {exc})") from exc
        if resp.status_code == 404 and ok404:
            return None
        if resp.status_code >= 400:
            detail = ""
            try:
                body = resp.json()
                detail = body.get("message", "")
                errors = body.get("errors")
                if errors:
                    detail += f" {errors}"
            except ValueError:
                detail = resp.text[:300]
            hint = HINTS.get(resp.status_code, "")
            message = f"GitHub {method} {path} failed ({resp.status_code}): {detail}. {hint}"
            raise GitHubError(message.replace(self._token, "***").strip(), resp.status_code)
        return resp.json() if resp.content else None

    async def login(self) -> str:
        user = await self._request("GET", "/user")
        return str(user["login"])

    async def get_repo(self, owner: str, repo: str) -> dict[str, Any] | None:
        result = await self._request("GET", f"/repos/{owner}/{repo}", ok404=True)
        return dict(result) if result is not None else None

    async def create_repo(self, owner: str, repo: str, *, description: str) -> dict[str, Any]:
        """Create a PRIVATE, EMPTY repository (no auto-init) for a user or an organization."""
        body = {"name": repo, "private": True, "auto_init": False, "description": description}
        if owner.lower() == (await self.login()).lower():
            result = await self._request("POST", "/user/repos", json=body)
        else:
            result = await self._request("POST", f"/orgs/{owner}/repos", json=body)
        return dict(result)

    async def find_open_pr(self, owner: str, repo: str, head: str, base: str) -> str | None:
        pulls = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls",
            params={"head": f"{owner}:{head}", "base": base, "state": "open"},
        )
        return str(pulls[0]["html_url"]) if pulls else None

    async def create_pr(
        self, owner: str, repo: str, *, title: str, head: str, base: str, body: str
    ) -> str:
        pull = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            json={"title": title, "head": head, "base": base, "body": body},
        )
        return str(pull["html_url"])

    # ------------------------------------------------------------------ Phase 12: automation
    async def labelled_issues(self, owner: str, repo: str, label: str) -> list[dict[str, Any]]:
        """Open issues (not pull requests) carrying `label`."""
        items = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/issues",
            params={"labels": label, "state": "open", "per_page": 50},
        )
        return [dict(i) for i in items or [] if "pull_request" not in i]

    async def get_issue(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        return dict(await self._request("GET", f"/repos/{owner}/{repo}/issues/{number}"))

    async def label_events(self, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
        events = await self._request(
            "GET", f"/repos/{owner}/{repo}/issues/{number}/events", params={"per_page": 100}
        )
        return [dict(e) for e in events or [] if e.get("event") == "labeled"]

    async def create_issue_comment(self, owner: str, repo: str, number: int, body: str) -> int:
        comment = await self._request(
            "POST", f"/repos/{owner}/{repo}/issues/{number}/comments", json={"body": body}
        )
        return int(comment["id"])

    async def update_issue_comment(self, owner: str, repo: str, comment_id: int, body: str) -> None:
        await self._request(
            "PATCH", f"/repos/{owner}/{repo}/issues/comments/{comment_id}", json={"body": body}
        )

    async def get_pull(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        return dict(await self._request("GET", f"/repos/{owner}/{repo}/pulls/{number}"))

    async def review_comments(self, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
        items = await self._request(
            "GET", f"/repos/{owner}/{repo}/pulls/{number}/comments", params={"per_page": 100}
        )
        return [dict(c) for c in items or []]

    async def conversation_comments(
        self, owner: str, repo: str, number: int
    ) -> list[dict[str, Any]]:
        items = await self._request(
            "GET", f"/repos/{owner}/{repo}/issues/{number}/comments", params={"per_page": 100}
        )
        return [dict(c) for c in items or []]

    async def reviews(self, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
        items = await self._request(
            "GET", f"/repos/{owner}/{repo}/pulls/{number}/reviews", params={"per_page": 100}
        )
        return [dict(r) for r in items or []]

    async def reply_to_review_comment(
        self, owner: str, repo: str, number: int, comment_id: int, body: str
    ) -> None:
        await self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls/{number}/comments/{comment_id}/replies",
            json={"body": body},
        )

    async def permission(self, owner: str, repo: str, user: str) -> str:
        """admin | maintain | write | triage | read | none."""
        result = await self._request(
            "GET", f"/repos/{owner}/{repo}/collaborators/{user}/permission", ok404=True
        )
        if result is None:
            return "none"
        return str(result.get("role_name") or result.get("permission") or "none")

    async def check_runs(self, owner: str, repo: str, sha: str) -> list[dict[str, Any]]:
        result = await self._request(
            "GET", f"/repos/{owner}/{repo}/commits/{sha}/check-runs", params={"per_page": 100}
        )
        return [dict(c) for c in (result or {}).get("check_runs", [])]

    async def job_logs(self, owner: str, repo: str, job_id: int) -> str:
        """A GitHub Actions job log (the API redirects to a short-lived download URL)."""
        try:
            resp = await self._http.get(
                f"/repos/{owner}/{repo}/actions/jobs/{job_id}/logs", follow_redirects=True
            )
        except httpx.HTTPError as exc:
            raise GitHubError(f"cannot download job logs ({type(exc).__name__})") from exc
        if resp.status_code >= 400:
            raise GitHubError(
                f"job logs unavailable ({resp.status_code}); the token needs Actions: read",
                resp.status_code,
            )
        return resp.text

    def _graphql_url(self) -> str:
        base = str(self._http.base_url).rstrip("/")
        return base[: -len("/v3")] + "/graphql" if base.endswith("/api/v3") else base + "/graphql"

    async def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        result = await self._request(
            "POST", self._graphql_url(), json={"query": query, "variables": variables}
        )
        if result.get("errors"):
            raise GitHubError(f"GitHub GraphQL error: {result['errors']}")
        return dict(result.get("data") or {})

    async def review_threads(self, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
        """Review threads with their id, resolved flag and the first comment's REST id."""
        data = await self.graphql(
            """query($owner: String!, $repo: String!, $number: Int!) {
              repository(owner: $owner, name: $repo) { pullRequest(number: $number) {
                reviewThreads(first: 100) { nodes {
                  id isResolved comments(first: 1) { nodes { databaseId } } } } } } }""",
            {"owner": owner, "repo": repo, "number": number},
        )
        nodes = data["repository"]["pullRequest"]["reviewThreads"]["nodes"]
        return [
            {
                "id": n["id"],
                "resolved": bool(n["isResolved"]),
                "comment_id": (n["comments"]["nodes"] or [{}])[0].get("databaseId"),
            }
            for n in nodes
        ]

    async def resolve_thread(self, thread_id: str) -> None:
        await self.graphql(
            """mutation($id: ID!) { resolveReviewThread(input: {threadId: $id}) {
              thread { isResolved } } }""",
            {"id": thread_id},
        )
