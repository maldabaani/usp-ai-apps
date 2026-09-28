"""Docker backend of the live preview: app on an internal network, proxy on 127.0.0.1."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shlex
from typing import Any

from docker.errors import APIError, NotFound
from docker.models.containers import Container

from app.sandbox.docker_runner import LABEL_RUN, DockerSandboxRunner, run_key
from app.sandbox.preview import (
    LOG_TAIL_LINES,
    PREVIEW_TASK,
    BackendStatus,
    PreviewSpec,
    forwarder_script,
    probe_script,
)

logger = logging.getLogger(__name__)

LABEL_PREVIEW = "devcrew.preview"
PROXY_IMAGE_STACK = "python"  # the proxy is a few lines of Python


def app_name(run_id: str) -> str:
    return f"devcrew-{run_key(run_id)}-{PREVIEW_TASK}"


def proxy_name(run_id: str) -> str:
    return f"{app_name(run_id)}-proxy"


def network_name(run_id: str) -> str:
    return f"devcrew-pv-{run_key(run_id)}"


class DockerPreviewBackend:
    def __init__(self, runner: DockerSandboxRunner) -> None:
        self.runner = runner

    @property
    def client(self) -> Any:
        return self.runner.client

    def _labels(self, run_id: str) -> dict[str, str]:
        return {LABEL_RUN: run_id, LABEL_PREVIEW: "1"}

    def _start(self, spec: PreviewSpec) -> int:
        self._stop(spec.run_id)
        client = self.client
        network = client.networks.create(
            network_name(spec.run_id),
            driver="bridge",
            internal=True,  # no route out of the host: the app cannot reach the internet
            labels=self._labels(spec.run_id),
        )
        self.runner._make_mount_points(spec.target)
        self.runner._prepare_volumes(spec.target)
        common = self.runner._common(spec.target)
        common["labels"] = {**common["labels"], **self._labels(spec.run_id)}
        script = f"cd {shlex.quote(spec.cwd)} && exec sh -c {shlex.quote(spec.command)}"
        client.containers.run(
            name=app_name(spec.run_id),
            command=["sh", "-c", script],
            detach=True,
            network=network.name,
            environment={
                "CI": "true",
                "MAVEN_ARGS": "-B -o",
                "NG_CLI_ANALYTICS": "false",
                "PORT": str(spec.port),
            },
            **common,
        )
        proxy: Container = client.containers.run(
            name=proxy_name(spec.run_id),
            image=self.runner.image(PROXY_IMAGE_STACK),
            command=["python", "-c", forwarder_script(app_name(spec.run_id), spec.port)],
            detach=True,
            network="bridge",
            ports={f"{spec.port}/tcp": ("127.0.0.1", None)},  # this machine only, any free port
            labels=self._labels(spec.run_id),
            user=common["user"],
            mem_limit="128m",
            pids_limit=64,
            security_opt=["no-new-privileges"],
            cap_drop=["ALL"],
            read_only=True,
            init=True,
        )
        network.connect(proxy)
        proxy.reload()
        bindings = (proxy.ports or {}).get(f"{spec.port}/tcp") or []
        if not bindings:
            raise RuntimeError("the preview proxy got no host port")
        return int(bindings[0]["HostPort"])

    async def start(self, spec: PreviewSpec) -> int:
        return await asyncio.to_thread(self._start, spec)

    def _status(self, run_id: str) -> BackendStatus:
        try:
            app = self.client.containers.get(app_name(run_id))
            proxy = self.client.containers.get(proxy_name(run_id))
        except NotFound:
            return "exited"
        if app.status != "running" or proxy.status != "running":
            return "exited"
        code, _ = proxy.exec_run(
            ["python", "-c", probe_script(app_name(run_id), self._port(proxy))]
        )
        return "running" if code == 0 else "starting"

    @staticmethod
    def _port(proxy: Container) -> int:
        exposed = next(iter((proxy.attrs.get("Config") or {}).get("ExposedPorts") or {}), "0/tcp")
        return int(str(exposed).split("/")[0])

    async def status(self, run_id: str) -> BackendStatus:
        return await asyncio.to_thread(self._status, run_id)

    def _logs(self, run_id: str, tail: int) -> str:
        try:
            app = self.client.containers.get(app_name(run_id))
        except NotFound:
            return ""
        return bytes(app.logs(stdout=True, stderr=True, tail=tail)).decode(
            "utf-8", errors="replace"
        )

    async def logs(self, run_id: str, tail: int = LOG_TAIL_LINES) -> str:
        return await asyncio.to_thread(self._logs, run_id, tail)

    def _remove(self, filters: dict[str, Any]) -> None:
        for container in self.client.containers.list(all=True, filters=filters):
            with contextlib.suppress(NotFound, APIError):
                container.remove(force=True)
        for network in self.client.networks.list(filters=filters):
            try:
                network.remove()
            except (NotFound, APIError) as exc:
                logger.warning("could not remove network %s: %s", network.name, exc)

    def _stop(self, run_id: str) -> None:
        self._remove({"label": [f"{LABEL_RUN}={run_id}", f"{LABEL_PREVIEW}=1"]})

    async def stop(self, run_id: str) -> None:
        await asyncio.to_thread(self._stop, run_id)

    async def stop_all(self) -> None:
        await asyncio.to_thread(self._remove, {"label": f"{LABEL_PREVIEW}=1"})
