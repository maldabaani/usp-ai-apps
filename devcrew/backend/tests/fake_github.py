"""A fake GitHub: REST API over httpx.MockTransport + bare git repositories on disk."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.github.client import GitHubClient
from app.github.delivery import GitHubDelivery

TOKEN = "ghp_secret_test_token"


@dataclass
class FakeGitHub:
    root: Path  # bare repositories live at root/<owner>/<repo>.git
    login: str = "octocat"
    requests: list[httpx.Request] = field(default_factory=list)
    pulls: list[dict[str, Any]] = field(default_factory=list)
    fail_status: int | None = None  # force every API call to fail with this status

    def bare(self, owner: str, repo: str) -> Path:
        return self.root / owner / f"{repo}.git"

    def make_repo(self, owner: str, repo: str) -> Path:
        path = self.bare(owner, repo)
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(path)], check=True)
        return path

    def heads(self, owner: str, repo: str) -> dict[str, str]:
        path = self.bare(owner, repo)
        if not path.exists():
            return {}
        out = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads"],
            cwd=path,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return dict(line.split(" ", 1) for line in out.splitlines())

    def log(self, owner: str, repo: str, ref: str) -> list[str]:
        out = subprocess.run(
            ["git", "log", "--format=%s", ref],
            cwd=self.bare(owner, repo),
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return out.splitlines()

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        if self.fail_status:
            return httpx.Response(self.fail_status, json={"message": "Bad credentials"})
        path, method = request.url.path, request.method
        parts = path.strip("/").split("/")
        if path == "/user":
            return httpx.Response(200, json={"login": self.login})
        if method == "GET" and parts[0] == "repos" and len(parts) == 3:
            if self.bare(parts[1], parts[2]).exists():
                return httpx.Response(200, json={"full_name": f"{parts[1]}/{parts[2]}"})
            return httpx.Response(404, json={"message": "Not Found"})
        if (
            (method == "POST"
            and path in ("/user/repos",))
            or (method == "POST" and parts[0] == "orgs")
        ):
            body = json.loads(request.content)
            owner = self.login if path == "/user/repos" else parts[1]
            self.make_repo(owner, body["name"])
            return httpx.Response(201, json={"full_name": f"{owner}/{body['name']}", **body})
        if parts[-1] == "pulls" and method == "GET":
            head = request.url.params["head"].split(":", 1)[1]
            found = [p for p in self.pulls if p["head"] == head and p["state"] == "open"]
            return httpx.Response(200, json=found)
        if parts[-1] == "pulls" and method == "POST":
            body = json.loads(request.content)
            owner, repo = parts[1], parts[2]
            if body["head"] not in self.heads(owner, repo):
                return httpx.Response(
                    422, json={"message": "Validation Failed", "errors": [{"field": "head"}]}
                )
            pr = {
                **body,
                "state": "open",
                "number": len(self.pulls) + 1,
                "html_url": f"https://github.com/{owner}/{repo}/pull/{len(self.pulls) + 1}",
            }
            self.pulls.append(pr)
            return httpx.Response(201, json=pr)
        return httpx.Response(404, json={"message": f"unhandled {method} {path}"})

    def client(self) -> GitHubClient:
        return GitHubClient(
            TOKEN, "https://api.github.test", transport=httpx.MockTransport(self.handler)
        )

    def delivery(self, token: str = TOKEN) -> GitHubDelivery:
        return GitHubDelivery(self.client, token=token, git_url=f"file://{self.root}")

    def calls(self) -> list[tuple[str, str]]:
        return [(r.method, r.url.path) for r in self.requests]
