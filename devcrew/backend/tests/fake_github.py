"""A fake GitHub: REST API over httpx.MockTransport + bare git repositories on disk."""

from __future__ import annotations

import hashlib
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
    # Phase 12: issues, comments, reviews, permissions, checks, review threads.
    issues: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)
    events: dict[tuple[str, str, int], list[dict[str, Any]]] = field(default_factory=dict)
    issue_comments: dict[tuple[str, str, int], list[dict[str, Any]]] = field(default_factory=dict)
    review_comments_: dict[tuple[str, str, int], list[dict[str, Any]]] = field(default_factory=dict)
    reviews_: dict[tuple[str, str, int], list[dict[str, Any]]] = field(default_factory=dict)
    permissions: dict[str, str] = field(default_factory=dict)
    checks: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    job_logs: dict[int, str] = field(default_factory=dict)
    threads: dict[str, dict[str, Any]] = field(default_factory=dict)
    statuses: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    etags: bool = True  # answer If-None-Match with 304 like GitHub
    not_modified: int = 0
    _ids: int = 1000
    _clock: int = 0

    # --------------------------------------------------------------------------- helpers
    def next_id(self) -> int:
        self._ids += 1
        return self._ids

    def add_issue(
        self, owner: str, repo: str, title: str, body: str, labels: list[str]
    ) -> dict[str, Any]:
        items = self.issues.setdefault((owner, repo), [])
        number = len(items) + 1
        issue = {
            "number": number,
            "title": title,
            "body": body,
            "state": "open",
            "labels": [],
            "html_url": f"https://github.com/{owner}/{repo}/issues/{number}",
        }
        items.append(issue)
        for label in labels:
            self.label(owner, repo, number, label)
        return issue

    def label(self, owner: str, repo: str, number: int, label: str) -> None:
        issue = self.issues[(owner, repo)][number - 1]
        if label not in [x["name"] for x in issue["labels"]]:
            issue["labels"].append({"name": label})
        self.events.setdefault((owner, repo, number), []).append(
            {"id": self.next_id(), "event": "labeled", "label": {"name": label}}
        )

    def unlabel(self, owner: str, repo: str, number: int, label: str) -> None:
        issue = self.issues[(owner, repo)][number - 1]
        issue["labels"] = [x for x in issue["labels"] if x["name"] != label]

    def pr(self, number: int) -> dict[str, Any]:
        return self.pulls[number - 1]

    def add_review_comment(
        self, owner: str, repo: str, number: int, user: str, body: str, path: str = "app/x.py"
    ) -> dict[str, Any]:
        stamp = self.stamp()
        comment = {
            "id": self.next_id(),
            "user": {"login": user},
            "body": body,
            "path": path,
            "line": 1,
            "in_reply_to_id": None,
            "created_at": stamp,
            "updated_at": stamp,
        }
        self.review_comments_.setdefault((owner, repo, number), []).append(comment)
        self.threads[f"T_{comment['id']}"] = {"resolved": False, "comment_id": comment["id"]}
        return comment

    def add_conversation_comment(
        self, owner: str, repo: str, number: int, user: str, body: str
    ) -> dict[str, Any]:
        comment = {"id": self.next_id(), "user": {"login": user}, "body": body}
        self.issue_comments.setdefault((owner, repo, number), []).append(comment)
        return comment

    def stamp(self) -> str:
        self._clock += 1
        return f"2026-09-01T10:{self._clock // 60:02d}:{self._clock % 60:02d}Z"

    def edit_review_comment(self, comment_id: int, body: str) -> None:
        for comments in self.review_comments_.values():
            for c in comments:
                if c["id"] == comment_id:
                    c["body"] = body
                    c["updated_at"] = self.stamp()

    def set_status(self, sha: str, *statuses: tuple[str, str]) -> None:
        """Legacy commit statuses: (context, state) with state pending/success/failure/error."""
        self.statuses[sha] = [
            {
                "id": self.next_id(),
                "context": context,
                "state": state,
                "description": f"{context}: {state}",
                "target_url": f"https://ci.example.test/{context}",
            }
            for context, state in statuses
        ]

    def set_checks(self, sha: str, *checks: tuple[str, str, str]) -> None:
        """(name, status, conclusion) per check; a job log is registered for each."""
        runs = []
        for name, status, conclusion in checks:
            job = self.next_id()
            self.job_logs[job] = f"{name} log\nFAILED tests/test_x.py::test_y - assert 1 == 2\n"
            runs.append(
                {
                    "id": job,
                    "name": name,
                    "status": status,
                    "conclusion": conclusion,
                    "app": {"slug": "github-actions"},
                    "html_url": f"https://github.com/x/y/actions/runs/1/job/{job}",
                }
            )
        self.checks[sha] = runs

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
        response = self._handle(request)
        if not (self.etags and request.method == "GET" and response.status_code == 200):
            return response
        etag = '"' + hashlib.sha1(response.content).hexdigest() + '"'
        if request.headers.get("if-none-match") == etag:
            self.not_modified += 1
            return httpx.Response(304, headers={"ETag": etag})
        return httpx.Response(
            200,
            content=response.content,
            headers={"ETag": etag, "content-type": response.headers.get("content-type", "")},
        )

    def _handle(self, request: httpx.Request) -> httpx.Response:
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
                head = subprocess.run(
                    ["git", "symbolic-ref", "--short", "HEAD"],
                    cwd=self.bare(parts[1], parts[2]),
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                return httpx.Response(
                    200,
                    json={"full_name": f"{parts[1]}/{parts[2]}", "default_branch": head or "main"},
                )
            return httpx.Response(404, json={"message": "Not Found"})
        if (method == "POST" and path in ("/user/repos",)) or (
            method == "POST" and parts[0] == "orgs"
        ):
            body = json.loads(request.content)
            owner = self.login if path == "/user/repos" else parts[1]
            self.make_repo(owner, body["name"])
            return httpx.Response(201, json={"full_name": f"{owner}/{body['name']}", **body})
        extra = self._automation(request, method, path, parts)
        if extra is not None:
            return extra
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
                "merged": False,
                "mergeable": True,
                "number": len(self.pulls) + 1,
                "html_url": f"https://github.com/{owner}/{repo}/pull/{len(self.pulls) + 1}",
            }
            self.pulls.append(pr)
            return httpx.Response(201, json=pr)
        return httpx.Response(404, json={"message": f"unhandled {method} {path}"})

    def _automation(
        self, request: httpx.Request, method: str, path: str, parts: list[str]
    ) -> httpx.Response | None:
        """Phase 12 endpoints (issues, comments, reviews, checks, GraphQL)."""
        if path == "/graphql":
            body = json.loads(request.content)
            if "resolveReviewThread" in body["query"]:
                self.threads[body["variables"]["id"]]["resolved"] = True
                return httpx.Response(200, json={"data": {"resolveReviewThread": {}}})
            nodes = [
                {
                    "id": tid,
                    "isResolved": t["resolved"],
                    "comments": {"nodes": [{"databaseId": t["comment_id"]}]},
                }
                for tid, t in self.threads.items()
            ]
            data = {"repository": {"pullRequest": {"reviewThreads": {"nodes": nodes}}}}
            return httpx.Response(200, json={"data": data})
        if len(parts) < 4 or parts[0] != "repos":
            return None
        owner, repo, rest = parts[1], parts[2], parts[3:]
        if rest == ["issues"] and method == "GET":
            label = request.url.params.get("labels")
            found = [
                i
                for i in self.issues.get((owner, repo), [])
                if i["state"] == "open" and label in [x["name"] for x in i["labels"]]
            ]
            return httpx.Response(200, json=found)
        if len(rest) == 2 and rest[0] == "issues" and rest[1].isdigit() and method == "GET":
            items = self.issues.get((owner, repo), [])
            if not 0 < int(rest[1]) <= len(items):
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json=items[int(rest[1]) - 1])
        if len(rest) == 3 and rest[0] == "issues" and rest[2] == "events":
            return httpx.Response(200, json=self.events.get((owner, repo, int(rest[1])), []))
        if len(rest) == 3 and rest[0] == "issues" and rest[2] == "comments":
            key = (owner, repo, int(rest[1]))
            if method == "POST":
                body = json.loads(request.content)
                comment = {"id": self.next_id(), "user": {"login": self.login}, **body}
                self.issue_comments.setdefault(key, []).append(comment)
                return httpx.Response(201, json=comment)
            return httpx.Response(200, json=self.issue_comments.get(key, []))
        if rest[:2] == ["issues", "comments"] and method == "PATCH":
            body = json.loads(request.content)
            for comments in self.issue_comments.values():
                for c in comments:
                    if c["id"] == int(rest[2]):
                        c["body"] = body["body"]
                        return httpx.Response(200, json=c)
            return httpx.Response(404, json={"message": "Not Found"})
        if len(rest) == 2 and rest[0] == "pulls" and rest[1].isdigit() and method == "GET":
            pr = self.pr(int(rest[1]))
            head_sha = self.heads(owner, repo).get(pr["head"], "")
            return httpx.Response(
                200,
                json={
                    **pr,
                    "head": {"ref": pr["head"], "sha": head_sha},
                    "base": {"ref": pr["base"]},
                },
            )
        if len(rest) >= 3 and rest[0] == "pulls" and rest[2] == "comments":
            key = (owner, repo, int(rest[1]))
            if len(rest) == 5 and rest[4] == "replies" and method == "POST":
                body = json.loads(request.content)
                reply = {
                    "id": self.next_id(),
                    "user": {"login": self.login},
                    "body": body["body"],
                    "in_reply_to_id": int(rest[3]),
                    "path": "",
                    "line": None,
                }
                self.review_comments_.setdefault(key, []).append(reply)
                return httpx.Response(201, json=reply)
            return httpx.Response(200, json=self.review_comments_.get(key, []))
        if len(rest) == 3 and rest[0] == "pulls" and rest[2] == "reviews":
            return httpx.Response(200, json=self.reviews_.get((owner, repo, int(rest[1])), []))
        if len(rest) == 3 and rest[0] == "collaborators" and rest[2] == "permission":
            role = self.permissions.get(rest[1], "admin" if rest[1] == self.login else None)
            if role is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"permission": role, "role_name": role})
        if len(rest) == 3 and rest[0] == "commits" and rest[2] == "status":
            found = self.statuses.get(rest[1], [])
            state = "pending" if any(s["state"] == "pending" for s in found) else "success"
            return httpx.Response(200, json={"state": state, "statuses": found})
        if len(rest) == 2 and rest[0] == "pulls" and method == "PATCH":
            pr = self.pr(int(rest[1]))
            pr.update(json.loads(request.content))
            return httpx.Response(200, json=pr)
        if len(rest) == 3 and rest[0] == "commits" and rest[2] == "check-runs":
            runs = self.checks.get(rest[1], [])
            return httpx.Response(200, json={"total_count": len(runs), "check_runs": runs})
        if rest[:2] == ["actions", "jobs"] and rest[-1] == "logs":
            return httpx.Response(200, text=self.job_logs.get(int(rest[2]), ""))
        return None

    def client(self) -> GitHubClient:
        return GitHubClient(
            TOKEN, "https://api.github.test", transport=httpx.MockTransport(self.handler)
        )

    def delivery(self, token: str = TOKEN) -> GitHubDelivery:
        return GitHubDelivery(self.client, token=token, git_url=f"file://{self.root}")

    def calls(self) -> list[tuple[str, str]]:
        return [(r.method, r.url.path) for r in self.requests]
