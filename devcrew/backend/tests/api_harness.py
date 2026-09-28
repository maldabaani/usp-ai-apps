"""In-memory API wiring: real FastAPI app + RunManager over the scripted fake models."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI

from app.config import Settings
from app.container import Container
from app.db.repository import InMemoryRunStore
from app.main import create_app
from tests.graph_harness import Harness, make_harness


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
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield Api(app, client, container, h)


async def wait_for_status(api: Api, run_id: str, status: str, limit_s: float = 5.0) -> None:
    async with asyncio.timeout(limit_s):
        while True:
            run = await api.container.runs.get(run_id)
            if run is not None and run.status == status:
                return
            await asyncio.sleep(0.01)
