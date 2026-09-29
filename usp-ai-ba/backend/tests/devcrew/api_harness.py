"""In-memory API wiring: real FastAPI app + RunManager over the scripted fake models."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import jwt
from fastapi import FastAPI

from config import settings as storyforge_settings
from devcrew.config import Settings
from devcrew.container import Container
from devcrew.db.repository import InMemoryRunStore
from devcrew.main import create_app
from tests.devcrew.graph_harness import Harness, make_harness


def _test_jwt(username: str = "devcrew_test_user", role: str = "admin") -> str:
    """DevCrew's routes are gated by StoryForge's require_auth/require_admin
    since the merge (Phase 3) -- every test client needs a valid JWT, same
    minting pattern as StoryForge's own tests (see tests/test_ask_router.py's
    _token()). Defaults to "admin" so DevCrew's own tests (which aren't
    testing StoryForge's user/admin distinction, just DevCrew's own graph/
    API logic) aren't newly blocked by the admin-only watched-repos routes."""
    payload = {"sub": username, "role": role, "exp": time.time() + 3600}
    return jwt.encode(payload, storyforge_settings.JWT_SECRET, algorithm=storyforge_settings.JWT_ALGORITHM)


@dataclass
class Api:
    app: FastAPI
    client: httpx.AsyncClient
    container: Container
    harness: Harness

    async def settle(self, run_id: str) -> dict[str, Any]:
        """Wait for the background drive, then return the run detail."""
        await self.container.manager.wait(run_id)
        resp = await self.client.get(f"/runs/{run_id}")
        assert resp.status_code == 200, resp.text
        return dict(resp.json())

    async def approve(self, run_id: str, **extra: Any) -> dict[str, Any]:
        resp = await self.client.post(f"/runs/{run_id}/resume", json={"action": "approve", **extra})
        assert resp.status_code == 202, resp.text
        return await self.settle(run_id)


def in_memory_container(h: Harness, runs: InMemoryRunStore | None = None) -> Container:
    return Container.assemble(
        h.deps.settings, deps=h.deps, runs=runs or InMemoryRunStore(), checkpointer=h.checkpointer
    )


@asynccontextmanager
async def api(
    tmp_path: Path,
    container: Container | None = None,
    harness: Harness | None = None,
    **settings: Any,
) -> AsyncIterator[Api]:
    h = harness or make_harness(tmp_path, **settings)
    container = container or in_memory_container(h)

    @asynccontextmanager
    async def factory(_: Settings) -> AsyncIterator[Container]:
        try:
            yield container
        finally:
            await container.manager.shutdown()

    app = create_app(h.deps.settings, factory)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        headers = {"Authorization": f"Bearer {_test_jwt()}"}
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", headers=headers
        ) as client:
            yield Api(app, client, container, h)


async def wait_for_status(api: Api, run_id: str, status: str, limit_s: float = 5.0) -> None:
    async with asyncio.timeout(limit_s):
        while True:
            run = await api.container.runs.get(run_id)
            if run is not None and run.status == status:
                return
            await asyncio.sleep(0.01)
