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
