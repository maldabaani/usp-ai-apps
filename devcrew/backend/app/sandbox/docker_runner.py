"""Docker implementation of the sandbox.

- One long-lived container per task worktree (`sleep infinity`), commands run via `docker exec`.
- Network is disabled (`network_mode=none`); the ONLY network access is the explicit dependency
  install step, which runs in a separate short-lived container on the bridge network.
- Per-command timeout (coreutils `timeout -s KILL` inside, plus an outer watchdog), CPU/memory/
  pids limits, all capabilities dropped, no-new-privileges, read-only root filesystem.
- Commands run as the backend's uid:gid so files in the workspace keep sane ownership.
- Dependencies live in volumes: per-run Python venv and node_modules, shared Maven/npm caches.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shlex
import threading
import time
import uuid
from collections import deque
from typing import Any

import docker
from docker.errors import APIError, NotFound
from docker.models.containers import Container

from app.config import Settings
from app.sandbox.policy import check_command, check_relative_cwd, check_workdir
from app.sandbox.runner import CommandResult, SandboxTarget

logger = logging.getLogger(__name__)

LABEL_RUN = "devcrew.run"
LABEL_TASK = "devcrew.task"
IMAGE_FAMILY = {"python": "python", "java": "java", "angular": "node", "mixed": "mixed"}
REQUIRED_IMAGE_FAMILIES = ("python", "java", "node")
OUTPUT_LIMIT_CHARS = 200_000
WATCHDOG_GRACE_S = 60
KILLED_EXIT_CODE = 137

M2_VOLUME = "devcrew-m2"
NPM_CACHE_VOLUME = "devcrew-npm-cache"


def run_key(run_id: str) -> str:
    return run_id[:12].lower()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "root"


def container_name(target: SandboxTarget) -> str:
    return _container_name(target.run_id, target.task_id)


def _container_name(run_id: str, task_id: str | None) -> str:
    return f"devcrew-{run_key(run_id)}-{_slug(task_id or 'main')}"


def dependency_volumes(target: SandboxTarget) -> dict[str, str]:
    """volume name -> mount point, for the stacks this target builds."""
    key = run_key(target.run_id)
    stacks = set(target.project_paths)
    volumes: dict[str, str] = {}
    if "python" in stacks:
        volumes[f"devcrew-{key}-venv"] = "/deps/venv"
    if "java" in stacks:
        volumes[M2_VOLUME] = "/cache/m2"
    if "angular" in stacks:
        path = target.project_paths["angular"].strip("/") or "."
        mount = "/workspace/node_modules" if path == "." else f"/workspace/{path}/node_modules"
        volumes[f"devcrew-{key}-nm-{_slug(path)}"] = mount
        volumes[NPM_CACHE_VOLUME] = "/cache/npm"
    return volumes


def build_script(command: str, cwd: str, timeout_s: int) -> str:
    return f"cd {shlex.quote(cwd)} && exec timeout -s KILL {timeout_s} sh -c {shlex.quote(command)}"


class _Tail:
    """Keeps only the last OUTPUT_LIMIT_CHARS of a stream."""

    def __init__(self) -> None:
        self._chunks: deque[str] = deque()
        self._size = 0
        self.truncated = False

    def add(self, chunk: bytes) -> None:
        text = chunk.decode("utf-8", errors="replace")
        self._chunks.append(text)
        self._size += len(text)
        while self._size > OUTPUT_LIMIT_CHARS and len(self._chunks) > 1:
            self._size -= len(self._chunks.popleft())
            self.truncated = True

    def text(self) -> str:
        out = "".join(self._chunks)[-OUTPUT_LIMIT_CHARS:]
        return ("[... output truncated]\n" + out) if self.truncated else out


class DockerSandboxRunner:
    def __init__(self, settings: Settings, client: docker.DockerClient | None = None) -> None:
        self._settings = settings
        self._client = client
        self._client_lock = threading.Lock()
        self._create_lock = threading.Lock()
        self._user = f"{os.getuid()}:{os.getgid()}"

    # ------------------------------------------------------------------------------ plumbing
    @property
    def client(self) -> docker.DockerClient:
        with self._client_lock:
            if self._client is None:
                self._client = docker.from_env(timeout=60)
            return self._client

    def image(self, image_stack: str) -> str:
        return f"{self._settings.sandbox_image_prefix}-{IMAGE_FAMILY[image_stack]}:latest"

    def missing_images(self) -> list[str]:
        missing = []
        for family in (*REQUIRED_IMAGE_FAMILIES, "mixed"):
            name = f"{self._settings.sandbox_image_prefix}-{family}:latest"
            try:
                self.client.images.get(name)
            except NotFound:
                missing.append(name)
        return missing

    def _env(self, network: bool) -> dict[str, str]:
        return {"CI": "true", "MAVEN_ARGS": "-B" if network else "-B -o"}

    def _common(self, target: SandboxTarget) -> dict[str, Any]:
        volumes: dict[str, dict[str, str]] = {
            str(target.workdir): {"bind": "/workspace", "mode": "rw"}
        }
        for name, mount in dependency_volumes(target).items():
            volumes[name] = {"bind": mount, "mode": "rw"}
        return {
            "image": self.image(target.image_stack),
            "user": self._user,
            "working_dir": "/workspace",
            "volumes": volumes,
            "labels": {LABEL_RUN: target.run_id, LABEL_TASK: target.task_id or ""},
            "nano_cpus": int(self._settings.sandbox_cpus * 1e9),
            "mem_limit": self._settings.sandbox_memory,
            "pids_limit": self._settings.sandbox_pids_limit,
            "security_opt": ["no-new-privileges"],
            "cap_drop": ["ALL"],
            "read_only": True,
            "tmpfs": {"/tmp": "rw,exec,size=1g", "/home/sandbox": "rw,exec,size=512m"},
            "init": True,
        }

    def _prepare_volumes(self, target: SandboxTarget) -> None:
        """New empty volumes mounted outside the image's dirs are root-owned; open them up."""
        mounts = [m for m in dependency_volumes(target).values() if m.endswith("node_modules")]
        if not mounts:
            return
        common = self._common(target)
        common.update(user="0:0", read_only=False, network_mode="none")
        self.client.containers.run(
            command=["chmod", "1777", *mounts], remove=True, detach=False, **common
        )

    def _ensure_container(self, target: SandboxTarget) -> Container:
        name = container_name(target)
        with self._create_lock:
            try:
                container = self.client.containers.get(name)
                tags = container.image.tags if container.image is not None else []
                if self.image(target.image_stack) not in tags:
                    container.remove(force=True)  # stale container from another image family
                else:
                    if container.status != "running":
                        container.start()
                    return container
            except NotFound:
                pass
            self._prepare_volumes(target)
            return self.client.containers.run(
                name=name,
                command=["sleep", "infinity"],
                detach=True,
                network_mode="none",
                environment=self._env(network=False),
                **self._common(target),
            )

    # ------------------------------------------------------------------------------ execution
    def _exec(self, target: SandboxTarget, script: str) -> tuple[int, str]:
        container = self._ensure_container(target)
        api = self.client.api
        exec_id = api.exec_create(
            container.id,
            ["sh", "-c", script],
            environment=self._env(network=False),
            user=self._user,
            workdir="/workspace",
        )["Id"]
        tail = _Tail()
        for chunk in api.exec_start(exec_id, stream=True):
            tail.add(chunk)
        return int(api.exec_inspect(exec_id)["ExitCode"]), tail.text()

    def _oneoff(self, target: SandboxTarget, script: str, timeout_s: int) -> tuple[int, str]:
        """Short-lived container with network access (dependency install step only)."""
        self._prepare_volumes(target)
        name = f"{container_name(target)}-install-{uuid.uuid4().hex[:6]}"
        container = self.client.containers.run(
            name=name,
            command=["sh", "-c", script],
            detach=True,
            network_mode="bridge",
            environment=self._env(network=True),
            **self._common(target),
        )
        try:
            try:
                status = container.wait(timeout=timeout_s + WATCHDOG_GRACE_S)
                code = int(status.get("StatusCode", 1))
            except Exception:  # requests timeout: the watchdog fired
                container.kill()
                code = KILLED_EXIT_CODE
            tail = _Tail()
            tail.add(container.logs(stdout=True, stderr=True))
            return code, tail.text()
        finally:
            container.remove(force=True)

    async def run(
        self,
        target: SandboxTarget,
        command: str,
        *,
        cwd: str = ".",
        timeout_s: int | None = None,
        network: bool = False,
    ) -> CommandResult:
        check_command(command)
        cwd = check_relative_cwd(cwd)
        check_workdir(target.workdir, self._settings.workspaces_dir)
        default = (
            self._settings.sandbox_install_timeout_s
            if network
            else self._settings.sandbox_command_timeout_s
        )
        timeout = min(timeout_s or default, default)
        script = build_script(command, cwd, timeout)

        started = time.monotonic()
        call = self._oneoff if network else self._exec
        args = (target, script, timeout) if network else (target, script)
        try:
            code, output = await asyncio.wait_for(
                asyncio.to_thread(call, *args),
                timeout=timeout + WATCHDOG_GRACE_S,
            )
        except TimeoutError:
            # The in-container timeout should have fired first; recycle the container.
            await self.release(target.run_id, target.task_id)
            code, output = KILLED_EXIT_CODE, "sandbox watchdog killed the command"
        duration = time.monotonic() - started
        timed_out = code == KILLED_EXIT_CODE and duration >= timeout - 1
        if timed_out:
            output += f"\n[killed after {timeout}s timeout]"
        return CommandResult(
            exit_code=code, output=output, timed_out=timed_out, duration_s=round(duration, 2)
        )

    # ------------------------------------------------------------------------------ cleanup
    def _remove_containers(self, filters: dict[str, Any]) -> None:
        for container in self.client.containers.list(all=True, filters=filters):
            try:
                container.remove(force=True)
            except (NotFound, APIError) as exc:
                logger.warning("could not remove container %s: %s", container.name, exc)

    async def release(self, run_id: str, task_id: str | None) -> None:
        """Remove a task's container (after merge/failure)."""
        name = _container_name(run_id, task_id)

        def _remove() -> None:
            with contextlib.suppress(NotFound):
                self.client.containers.get(name).remove(force=True)

        await asyncio.to_thread(_remove)

    async def cleanup_run(self, run_id: str) -> None:
        """Remove every container and per-run volume of a run (shared caches are kept)."""

        def _cleanup() -> None:
            self._remove_containers({"label": f"{LABEL_RUN}={run_id}"})
            prefix = f"devcrew-{run_key(run_id)}-"
            for volume in self.client.volumes.list():
                if volume.name.startswith(prefix):
                    try:
                        volume.remove(force=True)
                    except APIError as exc:
                        logger.warning("could not remove volume %s: %s", volume.name, exc)

        await asyncio.to_thread(_cleanup)
