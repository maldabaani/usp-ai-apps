from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import main
from app.api import health as health_api
from app.config import Settings
from app.health import CheckResult, HealthReport


def _report(ok: bool) -> HealthReport:
    checks = [
        CheckResult(name="database", ok=True, critical=True, detail="connected"),
        CheckResult(
            name="ollama_models",
            ok=ok,
            critical=True,
            detail="all pulled" if ok else "missing models. Run: ollama pull x",
        ),
    ]
    return HealthReport(ok=ok, checks=checks)


def _patch(monkeypatch: pytest.MonkeyPatch, ok: bool) -> None:
    async def fake(*_: Any, **__: Any) -> HealthReport:
        return _report(ok)

    monkeypatch.setattr(main, "run_health_checks", fake)
    monkeypatch.setattr(health_api, "run_health_checks", fake)


def test_health_endpoint_green(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, ok=True)
    with TestClient(main.create_app(settings)) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_strict_startup_fails_fast(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, ok=False)
    with (
        pytest.raises(main.StartupHealthError, match="ollama pull x"),
        TestClient(main.create_app(settings)),
    ):
        pass


def test_non_strict_startup_reports_503(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(monkeypatch, ok=False)
    settings.startup_health_strict = False
    with TestClient(main.create_app(settings)) as client:
        resp = client.get("/health")
    assert resp.status_code == 503
