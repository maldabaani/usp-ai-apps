"""Live preview (Phase 16, BL-110): API, manager lifecycle and cleanup (fake Docker backend)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.sandbox.preview import (
    BackendStatus,
    PreviewManager,
    PreviewSpec,
    forwarder_script,
    probe_script,
)
from tests.api_harness import Api, api
from tests.fakes import FakeRunner

REQUEST = {"request": "Build a TODO API with CRUD", "repo_target": "me/todo", "create_repo": False}
PREVIEW_YAML = "preview_cmd: uvicorn app.main:app --host 0.0.0.0 --port 8000\npreview_port: 8000\n"


class FakePreviewBackend:
    def __init__(self) -> None:
        self.started: list[PreviewSpec] = []
        self.stopped: list[str] = []
        self.live: BackendStatus = "running"

    async def start(self, spec: PreviewSpec) -> int:
        self.started.append(spec)
        return 5555

    async def status(self, run_id: str) -> BackendStatus:
        return self.live

    async def logs(self, run_id: str, tail: int = 200) -> str:
        return "INFO: Uvicorn running on http://0.0.0.0:8000"

    async def stop(self, run_id: str) -> None:
        self.stopped.append(run_id)

    async def stop_all(self) -> None:
        self.stopped.append("*")


def with_preview(a: Api) -> FakePreviewBackend:
    tpl = a.harness.deps.settings.templates_dir / "python" / "template.yaml"
    tpl.write_text(tpl.read_text() + PREVIEW_YAML)
    backend = FakePreviewBackend()
    deps = a.harness.deps
    assert deps.sandbox is not None
    deps.preview = PreviewManager(backend, deps.sandbox, deps.settings)
    return backend


async def launched(a: Api, run_id: str) -> None:
    manager = a.harness.deps.preview
    assert manager is not None
    task = manager._tasks.get(run_id)
    if task is not None:
        await asyncio.wait_for(task, 5)


async def test_preview_starts_after_scaffold_and_stops(tmp_path: Path) -> None:
    runner = FakeRunner()
    async with api(tmp_path, runner=runner) as a:
        backend = with_preview(a)
        run_id = (await a.client.post("/runs", json=REQUEST)).json()["id"]
        await a.settle(run_id)
        state = (await a.client.get(f"/runs/{run_id}/preview")).json()
        assert state["enabled"] and state["status"] == "off" and state["options"] == []
        resp = await a.client.post(f"/runs/{run_id}/preview", json={})
        assert resp.status_code == 409 and "not scaffolded" in resp.json()["detail"]

        await a.approve(run_id)
        await a.approve(run_id)  # scaffolded, tasks merged: waiting for final approval
        state = (await a.client.get(f"/runs/{run_id}/preview")).json()
        assert [o["stack"] for o in state["options"]] == ["python"]
        resp = await a.client.post(f"/runs/{run_id}/preview", json={})
        assert resp.status_code == 202, resp.text
        assert resp.json()["status"] == "installing"
        await launched(a, run_id)
        state = (await a.client.get(f"/runs/{run_id}/preview?logs=true")).json()
        assert state["status"] == "running" and state["url"] == "http://localhost:5555/"
        assert "Uvicorn running" in state["logs"]
        [spec] = backend.started
        assert spec.port == 8000 and spec.cwd == "." and spec.target.task_id == "preview"
        assert spec.command.startswith("uvicorn app.main:app")

        # a second start replaces the first
        await a.client.post(f"/runs/{run_id}/preview", json={"stack": "python"})
        await launched(a, run_id)
        assert backend.stopped == [run_id] and len(backend.started) == 2

        assert (await a.client.delete(f"/runs/{run_id}/preview")).status_code == 204
        assert backend.stopped == [run_id, run_id]
        state = (await a.client.get(f"/runs/{run_id}/preview")).json()
        assert state["status"] == "off"


async def test_run_end_stops_the_preview(tmp_path: Path) -> None:
    async with api(tmp_path, runner=FakeRunner()) as a:
        backend = with_preview(a)
        run_id = (await a.client.post("/runs", json=REQUEST)).json()["id"]
        await a.settle(run_id)
        await a.approve(run_id)
        await a.approve(run_id)
        await a.client.post(f"/runs/{run_id}/preview", json={})
        await launched(a, run_id)
        run = await a.approve(run_id)
        assert run["status"] == "completed"
        assert run_id in backend.stopped
        assert (await a.client.get(f"/runs/{run_id}/preview")).json()["status"] == "off"


async def test_an_app_that_exits_marks_the_preview_failed(tmp_path: Path) -> None:
    async with api(tmp_path, runner=FakeRunner()) as a:
        backend = with_preview(a)
        run_id = (await a.client.post("/runs", json=REQUEST)).json()["id"]
        await a.settle(run_id)
        await a.approve(run_id)
        await a.approve(run_id)
        backend.live = "exited"
        await a.client.post(f"/runs/{run_id}/preview", json={})
        await launched(a, run_id)
        state = (await a.client.get(f"/runs/{run_id}/preview")).json()
        assert state["status"] == "failed" and "stopped" in state["error"]
        resp = await a.client.post(f"/runs/{run_id}/preview", json={"stack": "java"})
        assert resp.status_code == 409 and "no preview for stack 'java'" in resp.json()["detail"]


async def test_projects_without_a_preview_command_and_disabled_preview(tmp_path: Path) -> None:
    async with api(tmp_path, runner=FakeRunner()) as a:
        run_id = (await a.client.post("/runs", json=REQUEST)).json()["id"]
        await a.settle(run_id)
        assert (await a.client.get(f"/runs/{run_id}/preview")).json()["enabled"] is False
        resp = await a.client.post(f"/runs/{run_id}/preview", json={})
        assert resp.status_code == 409 and "PREVIEW_ENABLED" in resp.json()["detail"]
        deps = a.harness.deps
        assert deps.sandbox is not None
        deps.preview = PreviewManager(FakePreviewBackend(), deps.sandbox, deps.settings)
        await a.approve(run_id)
        await a.approve(run_id)
        resp = await a.client.post(f"/runs/{run_id}/preview", json={})
        assert resp.status_code == 409 and "no preview command" in resp.json()["detail"]
        assert (await a.client.get("/runs/nope/preview")).status_code == 404


def test_proxy_scripts_compile() -> None:
    compile(forwarder_script("devcrew-abc-preview", 8000), "<proxy>", "exec")
    compile(probe_script("devcrew-abc-preview", 8000), "<probe>", "exec")


def test_devcrew_yaml_can_define_a_preview_for_an_existing_repo(tmp_path: Path) -> None:
    from app.repo.detect import detect_projects

    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / ".devcrew.yaml").write_text(
        "projects:\n  - stack: python\n    path: api\n"
        "    preview_cmd: uvicorn main:app --host 0.0.0.0 --port 9000\n    preview_port: 9000\n"
    )
    [project] = detect_projects(tmp_path).projects
    assert project.preview_cmd and project.preview_port == 9000
