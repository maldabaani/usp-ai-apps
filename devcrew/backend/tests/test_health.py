from __future__ import annotations

from typing import Any, cast

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from app import health
from app.config import Settings
from app.health import format_failures, missing_models, run_health_checks

REQUIRED = {"qwen2.5-coder:14b", "nomic-embed-text"}


def _transport(ollama_models: list[str] | None, github_status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "ollama.test":
            if ollama_models is None:
                raise httpx.ConnectError("connection refused")
            return httpx.Response(200, json={"models": [{"name": n} for n in ollama_models]})
        if request.url.host == "github.test":
            return httpx.Response(github_status, json={"login": "me"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _stub_infra(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ok(*_: Any) -> str:
        return "ok"

    monkeypatch.setattr(health, "check_database", ok)
    monkeypatch.setattr(health, "check_chroma", ok)
    monkeypatch.setattr(health, "check_docker", ok)


async def _report(settings: Settings, transport: httpx.MockTransport) -> health.HealthReport:
    async with httpx.AsyncClient(transport=transport) as http:
        return await run_health_checks(settings, cast(AsyncEngine, None), REQUIRED, http=http)


def _by_name(report: health.HealthReport) -> dict[str, health.CheckResult]:
    return {c.name: c for c in report.checks}


def test_missing_models_normalizes_latest_tag() -> None:
    assert missing_models(REQUIRED, {"nomic-embed-text:latest", "qwen2.5-coder:14b"}) == []
    missing = missing_models(REQUIRED, {"qwen2.5-coder:7b"})
    assert missing == ["nomic-embed-text", "qwen2.5-coder:14b"]


async def test_all_green(settings: Settings) -> None:
    settings.github_token = SecretStr("t")
    report = await _report(settings, _transport(["qwen2.5-coder:14b", "nomic-embed-text:latest"]))
    assert report.ok, report
    assert _by_name(report)["github_token"].detail == "authenticated as me"


async def test_missing_model_is_actionable(settings: Settings) -> None:
    report = await _report(settings, _transport(["qwen2.5-coder:14b"]))
    models = _by_name(report)["ollama_models"]
    assert not models.ok and models.critical
    assert "ollama pull nomic-embed-text" in models.detail
    assert "ollama pull nomic-embed-text" in format_failures(report)


async def test_ollama_down_skips_model_check(settings: Settings) -> None:
    report = await _report(settings, _transport(None))
    checks = _by_name(report)
    assert not checks["ollama"].ok and "ollama serve" in checks["ollama"].detail
    assert not checks["ollama_models"].ok
    assert {c.name for c in report.critical_failures} == {"ollama", "ollama_models"}


async def test_github_token_is_not_critical(settings: Settings) -> None:
    report = await _report(settings, _transport(list(REQUIRED)))
    gh = _by_name(report)["github_token"]
    assert not gh.ok and not gh.critical and "GITHUB_TOKEN is not set" in gh.detail
    assert not report.ok
    assert report.critical_failures == []


async def test_github_token_rejected(settings: Settings) -> None:
    settings.github_token = SecretStr("bad")
    report = await _report(settings, _transport(list(REQUIRED), github_status=401))
    assert "rejected" in _by_name(report)["github_token"].detail


async def test_check_timeout(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    async def hang(*_: Any) -> str:
        await asyncio.sleep(10)
        return "never"

    monkeypatch.setattr(health, "check_docker", hang)
    monkeypatch.setattr(health, "CHECK_TIMEOUT_S", 0.05)
    report = await _report(settings, _transport(list(REQUIRED)))
    docker = _by_name(report)["docker"]
    assert not docker.ok and "timed out" in docker.detail
