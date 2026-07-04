"""Watches a directory (non-recursively) and auto-starts one extraction job
per file that appears in it -- a dropped subfolder is ignored, since the
watch unit is an individual file, not a directory. Off by default
(CODEMIND_WATCH_ENABLED); enabling it activates no other behavior.

Ported from com.jslogicextractor.watch.InputDirectoryWatcher. Each
create/modify event for a path (re)schedules a debounced check after
quiet_period_seconds of inactivity on that path so a file still being
written/copied into the directory isn't picked up mid-write -- via
threading.Timer rather than Java's ScheduledExecutorService, since
watchdog's Observer runs its callbacks on its own background thread, not the
asyncio event loop. The debounced action hops back onto the event loop
captured at start() time via run_coroutine_threadsafe, since starting a job
means awaiting orchestrator.run() (a coroutine). Files present before the
watcher starts are not retroactively picked up -- only files that arrive
while it's running.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from codemind import job_registry
from codemind.agents.selector import get_agent_selector
from codemind.orchestrator import run as run_job

logger = logging.getLogger(__name__)


class InputDirectoryWatcher:
    def __init__(self, directory: Path, quiet_period_seconds: float) -> None:
        self._directory = directory
        self._quiet_period_seconds = quiet_period_seconds
        self._pending_checks: dict[Path, threading.Timer] = {}
        self._lock = threading.Lock()
        self._observer: Observer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False

    def start(self) -> None:
        self._directory = self._directory.expanduser().resolve()
        self._directory.mkdir(parents=True, exist_ok=True)
        self._loop = asyncio.get_event_loop()
        self._observer = Observer()
        self._observer.schedule(_Handler(self), str(self._directory), recursive=False)
        self._observer.start()
        self._running = True
        logger.info(
            "Watching %s for dropped files; each becomes its own extraction job", self._directory
        )

    def stop(self) -> None:
        self._running = False
        with self._lock:
            for timer in self._pending_checks.values():
                timer.cancel()
            self._pending_checks.clear()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)

    def is_running(self) -> bool:
        return self._running

    def _schedule_check(self, file: Path) -> None:
        with self._lock:
            existing = self._pending_checks.get(file)
            if existing is not None:
                existing.cancel()
            timer = threading.Timer(self._quiet_period_seconds, self._start_job_if_still_present, args=(file,))
            timer.daemon = True
            self._pending_checks[file] = timer
            timer.start()

    def _start_job_if_still_present(self, file: Path) -> None:
        with self._lock:
            self._pending_checks.pop(file, None)
        if not file.is_file():
            # Directory dropped directly into the watched folder, or the file
            # was already moved/deleted before the quiet period elapsed --
            # neither starts a job.
            return
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._start_job(file), self._loop)

    async def _start_job(self, file: Path) -> None:
        resolved = file.expanduser().resolve()
        job = job_registry.register(resolved, None, None, None)
        logger.info("Auto-started job %s for dropped file %s", job.id, resolved)
        try:
            await run_job(job, get_agent_selector())
        except Exception as exc:  # noqa: BLE001 - one bad drop must not kill the watcher
            logger.exception("Auto-started job %s for dropped file %s crashed", job.id, file)
            job.mark_failed(str(exc))
        finally:
            job_registry.persist(job)


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher: InputDirectoryWatcher) -> None:
        self._watcher = watcher

    def on_created(self, event) -> None:
        self._watcher._schedule_check(Path(event.src_path))

    def on_modified(self, event) -> None:
        self._watcher._schedule_check(Path(event.src_path))
