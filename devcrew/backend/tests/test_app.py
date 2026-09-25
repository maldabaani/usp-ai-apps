from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app import main
from app.api import health as health_api
from app.config import Settings
from app.container import Container
from app.health import CheckResult, HealthReport
from tests.api_harness import api


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


@pytest.mark.parametrize(("ok", "code"), [(True, 200), (False, 503)])
async def test_health_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ok: bool, code: int
) -> None:
    _patch(monkeypatch, ok=ok)
    async with api(tmp_path) as a:
        a.container.engine = cast(AsyncEngine, object())  # health checks run against "the DB"
        resp = await a.client.get("/health")
    assert resp.status_code == code
    assert resp.json()["ok"] is ok


async def test_strict_startup_fails_fast_before_touching_the_db(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(monkeypatch, ok=False)

    def must_not_open(*_: Any) -> Any:
        raise AssertionError("container must not be opened when health fails")

    monkeypatch.setattr(Container, "open", must_not_open)
    app = main.create_app(settings)
    with pytest.raises(main.StartupHealthError, match="ollama pull x"):
        async with app.router.lifespan_context(app):
            pass


async def test_non_strict_startup_continues(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(monkeypatch, ok=False)
    settings.startup_health_strict = False
    opened: list[bool] = []

    class FakeManager:
        async def recover(self) -> list[str]:
            return []

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_open(*_: Any) -> Any:
        opened.append(True)
        yield type("C", (), {"manager": FakeManager()})()

    monkeypatch.setattr(Container, "open", fake_open)
    app = main.create_app(settings)
    async with app.router.lifespan_context(app):
        pass
    assert opened == [True]
