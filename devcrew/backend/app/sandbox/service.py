"""Graph-facing sandbox facade: policy checks + automatic dependency install step.

Dependencies are installed (with network) by the template's fixed `install_cmd`, never by an
agent-chosen command. It runs at scaffold time and again whenever a dependency manifest
(pyproject.toml, pom.xml, package.json, ...) changes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from app.graph.layout import LayoutEntry
from app.sandbox.policy import check_command, check_relative_cwd
from app.sandbox.runner import CommandResult, CommandRunner, SandboxTarget

logger = logging.getLogger(__name__)

MANIFESTS: dict[str, tuple[str, ...]] = {
    "python": ("pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.cfg"),
    "java": ("pom.xml",),
    # package-lock.json is rewritten by `npm install`, so it must not trigger reinstalls.
    "angular": ("package.json",),
}


@dataclass(frozen=True)
class InstallOutcome:
    stack: str
    skipped: bool
    result: CommandResult | None = None


def manifest_hash(target: SandboxTarget, entry: LayoutEntry) -> str:
    digest = hashlib.sha256()
    base = target.workdir / entry.path
    for name in MANIFESTS.get(entry.stack, ()):
        path = base / name
        if path.is_file():
            digest.update(name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


class Sandbox:
    def __init__(self, runner: CommandRunner, state_dir: Path | None = None) -> None:
        self.runner = runner
        # (run_id, stack) -> manifest hash of the last SUCCESSFUL install. Dependency volumes are
        # per run, so one install serves every task of the run. With `state_dir` the hashes are
        # kept in <state_dir>/<run_id>.json and survive a backend restart (no reinstall).
        self._installed: dict[tuple[str, str], str] = {}
        self._state_dir = state_dir
        self._loaded: set[str] = set()
        # Parallel tasks share the run's dependency volumes: one install per (run, stack) at a time.
        self._install_locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def ensure_dependencies(
        self, target: SandboxTarget, layout: Mapping[str, LayoutEntry]
    ) -> list[InstallOutcome]:
        outcomes = []
        for entry in layout.values():
            key = (target.run_id, entry.stack)
            self._load(target.run_id)
            digest = manifest_hash(target, entry)
            lock = self._install_locks.setdefault(key, asyncio.Lock())
            async with lock:
                if self._installed.get(key) == digest:
                    outcomes.append(InstallOutcome(entry.stack, skipped=True))
                    continue
                result = await self.runner.run(
                    target, entry.template.install_cmd, cwd=entry.path, network=True
                )
                if result.ok:
                    self._installed[key] = digest
                    self._save(target.run_id)
            if not result.ok:
                logger.warning(
                    "dependency install failed for %s: %s", entry.stack, result.output[-500:]
                )
            outcomes.append(InstallOutcome(entry.stack, skipped=False, result=result))
        return outcomes

    async def exec(
        self,
        target: SandboxTarget,
        layout: Mapping[str, LayoutEntry],
        command: str,
        *,
        cwd: str = ".",
        timeout_s: int | None = None,
    ) -> CommandResult:
        """Run an (agent or test) command offline, installing changed dependencies first."""
        check_command(command)
        cwd = check_relative_cwd(cwd)
        failed = [
            o
            for o in await self.ensure_dependencies(target, layout)
            if o.result is not None and not o.result.ok
        ]
        result = await self.runner.run(target, command, cwd=cwd, timeout_s=timeout_s)
        if failed:
            notes = "\n".join(
                f"[dependency install for {o.stack} failed (exit {o.result.exit_code}):\n"
                f"{o.result.output[-1500:]}]"
                for o in failed
                if o.result is not None
            )
            result = result.model_copy(update={"output": f"{notes}\n{result.output}"})
        return result

    async def release(self, run_id: str, task_id: str | None) -> None:
        await self.runner.release(run_id, task_id)

    def _state_file(self, run_id: str) -> Path | None:
        return self._state_dir / f"{run_id}.json" if self._state_dir is not None else None

    def _load(self, run_id: str) -> None:
        path = self._state_file(run_id)
        if path is None or run_id in self._loaded:
            return
        self._loaded.add(run_id)
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for stack, digest in saved.items():
            self._installed.setdefault((run_id, str(stack)), str(digest))

    def _save(self, run_id: str) -> None:
        path = self._state_file(run_id)
        if path is None:
            return
        saved = {stack: d for (rid, stack), d in self._installed.items() if rid == run_id}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(saved), encoding="utf-8")
        except OSError as exc:
            logger.warning("could not save the install state of %s: %s", run_id, exc)

    async def cleanup_run(self, run_id: str) -> None:
        for key in [k for k in self._installed if k[0] == run_id]:
            del self._installed[key]
        path = self._state_file(run_id)
        if path is not None:
            path.unlink(missing_ok=True)
        await self.runner.cleanup_run(run_id)
