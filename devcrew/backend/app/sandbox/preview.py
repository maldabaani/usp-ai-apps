"""Live preview (Phase 16, BL-110): serve the generated app so the human can click through it.

The app runs in the sandbox image with the usual hardening, on an INTERNAL Docker network (no
route out). A small proxy container (DevCrew's own fixed forwarder, not generated code) joins
that network and publishes one port on 127.0.0.1 of the host, so the preview is reachable from
this machine only. One preview per run; it stops on request, on a new start and at run cleanup.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from app.config import Settings
from app.graph.layout import LayoutEntry
from app.sandbox.policy import check_command, check_relative_cwd
from app.sandbox.runner import SandboxTarget
from app.sandbox.service import Sandbox

logger = logging.getLogger(__name__)

PREVIEW_TASK = "preview"
LOG_TAIL_LINES = 200

PreviewStatus = Literal["off", "installing", "starting", "running", "failed"]
BackendStatus = Literal["starting", "running", "exited"]


class PreviewError(ValueError):
    """The preview cannot be started (no preview command, no workspace, disabled)."""


@dataclass(frozen=True)
class PreviewSpec:
    run_id: str
    stack: str
    target: SandboxTarget
    command: str
    port: int
    cwd: str


class PreviewBackend(Protocol):
    async def start(self, spec: PreviewSpec) -> int:
        """Start the app and its proxy; return the host port (bound to 127.0.0.1)."""
        ...

    async def status(self, run_id: str) -> BackendStatus: ...

    async def logs(self, run_id: str, tail: int = LOG_TAIL_LINES) -> str: ...

    async def stop(self, run_id: str) -> None: ...

    async def stop_all(self) -> None:
        """Remove every preview left over from an earlier backend process."""
        ...


class PreviewOption(BaseModel):
    stack: str
    path: str
    command: str
    port: int


class PreviewState(BaseModel):
    enabled: bool = True
    status: PreviewStatus = "off"
    stack: str | None = None
    command: str | None = None
    url: str | None = None
    started_at: datetime | None = None
    error: str | None = None
    options: list[PreviewOption] = Field(default_factory=list)
    logs: str | None = None


def preview_options(layout: Mapping[str, LayoutEntry]) -> list[PreviewOption]:
    return [
        PreviewOption(
            stack=e.stack, path=e.path, command=e.template.preview_cmd, port=e.template.preview_port
        )
        for e in layout.values()
        if e.template.preview_cmd and e.template.preview_port
    ]


class PreviewManager:
    def __init__(self, backend: PreviewBackend, sandbox: Sandbox, settings: Settings) -> None:
        self.backend = backend
        self.sandbox = sandbox
        self.settings = settings
        self._states: dict[str, PreviewState] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, run_id: str) -> asyncio.Lock:
        return self._locks.setdefault(run_id, asyncio.Lock())

    async def start(
        self,
        run_id: str,
        target: SandboxTarget,
        layout: Mapping[str, LayoutEntry],
        stack: str | None = None,
    ) -> PreviewState:
        options = preview_options(layout)
        if not options:
            raise PreviewError(
                "this project has no preview command (templates define one; for an existing "
                "repository add preview_cmd and preview_port to .devcrew.yaml)"
            )
        chosen = next((o for o in options if o.stack == stack), None) if stack else options[0]
        if chosen is None:
            raise PreviewError(f"no preview for stack '{stack}'")
        check_command(chosen.command)
        cwd = check_relative_cwd(chosen.path)
        async with self._lock(run_id):
            await self._stop_locked(run_id)
            state = PreviewState(
                status="installing",
                stack=chosen.stack,
                command=chosen.command,
                started_at=datetime.now(UTC),
                options=options,
            )
            self._states[run_id] = state
            spec = PreviewSpec(
                run_id=run_id,
                stack=chosen.stack,
                target=target,
                command=chosen.command,
                port=chosen.port,
                cwd=cwd,
            )
            entry = {chosen.stack: layout[chosen.stack]}
            self._tasks[run_id] = asyncio.create_task(self._launch(spec, entry))
        return state.model_copy()

    async def _launch(self, spec: PreviewSpec, layout: Mapping[str, LayoutEntry]) -> None:
        state = self._states[spec.run_id]
        try:
            for outcome in await self.sandbox.ensure_dependencies(spec.target, layout):
                if outcome.result is not None and not outcome.result.ok:
                    state.status = "failed"
                    state.error = (
                        f"dependency install failed (exit {outcome.result.exit_code}):\n"
                        + outcome.result.output[-1500:]
                    )
                    return
            state.status = "starting"
            host_port = await self.backend.start(spec)
            state.url = f"http://{self.settings.preview_host}:{host_port}/"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("preview of %s failed to start: %s", spec.run_id, exc)
            state.status = "failed"
            state.error = str(exc)[:2000]

    async def state(
        self, run_id: str, *, options: list[PreviewOption] | None = None, logs: bool = False
    ) -> PreviewState:
        state = self._states.get(run_id)
        if state is None:
            return PreviewState(options=options or [])
        if state.status in ("starting", "running") and state.url is not None:
            live = await self.backend.status(run_id)
            if live == "running":
                state.status = "running"
            elif live == "exited":
                state.status = "failed"
                state.error = "the app stopped (see the log)"
        out = state.model_copy()
        if options is not None:
            out.options = options
        if logs and state.url is not None:
            with contextlib.suppress(Exception):
                out.logs = await self.backend.logs(run_id)
        return out

    async def stop(self, run_id: str) -> None:
        async with self._lock(run_id):
            await self._stop_locked(run_id)
            self._states.pop(run_id, None)

    async def _stop_locked(self, run_id: str) -> None:
        task = self._tasks.pop(run_id, None)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if run_id in self._states:
            try:
                await self.backend.stop(run_id)
            except Exception as exc:
                logger.warning("could not stop the preview of %s: %s", run_id, exc)

    def active(self, run_id: str) -> bool:
        return run_id in self._states

    async def shutdown(self) -> None:
        for run_id in list(self._states):
            await self.stop(run_id)


def forwarder_script(target_host: str, port: int) -> str:
    """The proxy's program: forward 0.0.0.0:<port> to <target_host>:<port> (plain TCP, so
    websockets and dev-server live reload work too)."""
    return f"""
import asyncio

async def pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()

async def handle(client_r, client_w):
    try:
        app_r, app_w = await asyncio.open_connection({target_host!r}, {port})
    except OSError:
        client_w.close()
        return
    await asyncio.gather(pipe(client_r, app_w), pipe(app_r, client_w))

async def main():
    server = await asyncio.start_server(handle, "0.0.0.0", {port})
    async with server:
        await server.serve_forever()

asyncio.run(main())
"""


def probe_script(target_host: str, port: int) -> str:
    return (
        "import socket,sys\n"
        "try:\n"
        f"    socket.create_connection(({target_host!r}, {port}), timeout=2).close()\n"
        "except OSError:\n"
        "    sys.exit(1)\n"
    )
