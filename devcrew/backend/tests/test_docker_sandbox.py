from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import pytest

from app.config import DEVCREW_DIR, Settings
from app.graph.layout import LayoutEntry
from app.sandbox.docker_runner import (
    OUTPUT_LIMIT_CHARS,
    DockerSandboxRunner,
    _Tail,
    build_script,
    container_name,
    dependency_volumes,
)
from app.sandbox.policy import PolicyViolation
from app.sandbox.runner import SandboxTarget
from app.sandbox.service import Sandbox, manifest_hash
from app.tools.catalog import TemplatesCatalog

RUN = "abcdef0123456789abcdef0123456789"


def target(tmp: Path, stacks: dict[str, str], task: str | None = "T1") -> SandboxTarget:
    return SandboxTarget(RUN, task, tmp, "mixed" if len(stacks) > 1 else next(iter(stacks)), stacks)


def test_container_naming(tmp_path: Path) -> None:
    assert container_name(target(tmp_path, {"python": "."})) == "devcrew-abcdef012345-t1"
    assert container_name(target(tmp_path, {"python": "."}, None)) == "devcrew-abcdef012345-main"


def test_dependency_volumes_per_stack(tmp_path: Path) -> None:
    assert dependency_volumes(target(tmp_path, {"python": "."})) == {
        "devcrew-abcdef012345-venv": "/deps/venv"
    }
    assert dependency_volumes(target(tmp_path, {"java": "."})) == {"devcrew-m2": "/cache/m2"}
    mixed = dependency_volumes(target(tmp_path, {"python": "api", "angular": "web/ui"}))
    assert mixed == {
        "devcrew-abcdef012345-venv": "/deps/venv",
        "devcrew-abcdef012345-nm-web-ui": "/workspace/web/ui/node_modules",
        "devcrew-npm-cache": "/cache/npm",
    }


def test_build_script_quotes_everything() -> None:
    script = build_script("echo 'a b' && pytest -q", "my dir", 30)
    assert (
        script
        == "cd 'my dir' && exec timeout -s KILL 30 sh -c 'echo '\"'\"'a b'\"'\"' && pytest -q'"
    )


def test_output_tail_is_bounded() -> None:
    tail = _Tail()
    for _ in range(50):
        tail.add(b"x" * 10_000)
    text = tail.text()
    assert text.startswith("[... output truncated]") and len(text) <= OUTPUT_LIMIT_CHARS + 30


async def test_runner_enforces_policy_before_docker(tmp_path: Path) -> None:
    runner = DockerSandboxRunner(Settings(_env_file=None, workspaces_dir=tmp_path / "ws"))
    (tmp_path / "ws" / "r").mkdir(parents=True)
    with pytest.raises(PolicyViolation, match="docker"):
        await runner.run(target(tmp_path / "ws" / "r", {"python": "."}), "docker ps")
    with pytest.raises(PolicyViolation, match="outside the workspaces"):
        await runner.run(target(tmp_path, {"python": "."}), "ls")
    with pytest.raises(PolicyViolation, match="relative"):
        await runner.run(target(tmp_path / "ws" / "r", {"python": "."}), "ls", cwd="/etc")


def test_manifest_hash_tracks_dependency_files(tmp_path: Path) -> None:
    tpl = TemplatesCatalog(DEVCREW_DIR / "templates").get("angular-standalone")
    entry = LayoutEntry("angular", ".", tpl)
    tgt = target(tmp_path, {"angular": "."})
    empty = manifest_hash(tgt, entry)
    (tmp_path / "package.json").write_text("{}")
    first = manifest_hash(tgt, entry)
    (tmp_path / "package-lock.json").write_text("{}")  # rewritten by npm: must not count
    assert manifest_hash(tgt, entry) == first != empty


# --------------------------------------------------------------------------------------------
# Real Docker (opt-in): DEVCREW_DOCKER_TESTS=1 and the python sandbox image built.
# --------------------------------------------------------------------------------------------
def _docker_ready() -> bool:
    if os.environ.get("DEVCREW_DOCKER_TESTS") != "1":
        return False
    try:
        import docker

        docker.from_env().images.get("devcrew-sandbox-python:latest")
        return True
    except Exception:
        return False


docker_only = pytest.mark.skipif(not _docker_ready(), reason="set DEVCREW_DOCKER_TESTS=1 + image")


@docker_only
async def test_real_python_template_in_docker(tmp_path: Path) -> None:
    import docker

    run_id = uuid.uuid4().hex
    ws_root = tmp_path / "ws"
    project = ws_root / run_id / "repo"
    shutil.copytree(DEVCREW_DIR / "templates" / "python", project)
    settings = Settings(_env_file=None, workspaces_dir=ws_root, sandbox_command_timeout_s=120)
    runner = DockerSandboxRunner(settings)
    sandbox = Sandbox(runner)
    tpl = TemplatesCatalog(DEVCREW_DIR / "templates").get("python-fastapi")
    layout = {"python": LayoutEntry("python", ".", tpl)}
    tgt = SandboxTarget(run_id, "T1", project, "python", {"python": "."})
    try:
        ok = await sandbox.exec(tgt, layout, tpl.test_cmd)
        assert ok.ok, ok.output
        assert "1 passed" in ok.output

        (project / "tests" / "test_fail.py").write_text("def test_fail():\n    assert 1 == 2\n")
        failing = await sandbox.exec(tgt, layout, tpl.test_cmd)
        assert failing.exit_code == 1 and "1 failed" in failing.output

        offline = await runner.run(
            tgt, "python -c \"import socket; socket.create_connection(('pypi.org', 443), 3)\""
        )
        assert offline.exit_code != 0

        slow = await runner.run(tgt, "sleep 20", timeout_s=2)
        assert slow.timed_out and slow.exit_code == 137

        ro = await runner.run(tgt, "touch /etc/x")
        assert ro.exit_code != 0 and "Read-only" in ro.output
    finally:
        await sandbox.cleanup_run(run_id)
    client = docker.from_env()
    assert not client.containers.list(all=True, filters={"label": f"devcrew.run={run_id}"})
    assert not [v for v in client.volumes.list() if v.name.startswith(f"devcrew-{run_id[:12]}")]
