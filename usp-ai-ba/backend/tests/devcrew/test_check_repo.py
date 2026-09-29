"""GET /check-repo/{owner}/{repo}: "Test Connection" before Send-to-DevCrew."""

from __future__ import annotations

from pathlib import Path

from tests.devcrew.api_harness import api
from tests.devcrew.fake_github import FakeGitHub


async def test_check_repo_exists_returns_metadata(tmp_path: Path) -> None:
    gh = FakeGitHub(tmp_path / "remotes")
    gh.make_repo("acme", "shop")
    async with api(tmp_path, github_delivery_enabled=True, github=gh.delivery()) as a:
        resp = await a.client.get("/check-repo/acme/shop")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["exists"] is True
        assert body["default_branch"] == "main"
        assert body["private"] is False


async def test_check_repo_missing_returns_404(tmp_path: Path) -> None:
    gh = FakeGitHub(tmp_path / "remotes")
    async with api(tmp_path, github_delivery_enabled=True, github=gh.delivery()) as a:
        resp = await a.client.get("/check-repo/acme/does-not-exist")
        assert resp.status_code == 404, resp.text
        assert "does not exist" in resp.json()["detail"]


async def test_check_repo_without_github_configured_returns_422(tmp_path: Path) -> None:
    async with api(tmp_path, github_delivery_enabled=False) as a:
        resp = await a.client.get("/check-repo/acme/shop")
        assert resp.status_code == 422, resp.text
        assert "GITHUB_TOKEN" in resp.json()["detail"]
