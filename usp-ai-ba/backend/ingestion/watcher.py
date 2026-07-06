"""Local filesystem watcher for auto re-ingestion (Phase L-C): watches each
enabled ingestion/watch_registry.py target directory and triggers a full
ingest_documents()/ingest_code() run (via ingestion/runner_jobs.py's shared
wrappers) whenever a file is created, modified, or deleted underneath it.

One watchdog Observer per active target (not one process-wide observer) so
starting/stopping one target doesn't disturb any other. Deviates from the
retired codemind/watch.py (see `git show 9f8497b^:usp-ai-ba/backend/codemind/watch.py`)
in two ways: recursive=True (both ingestion entry points already walk
recursively via rglob, so a non-recursive watch would create a confusing
"subfolder changes never auto-trigger" mismatch), and on_deleted is handled
(the retired module silently ignored deletions).

Debouncing is per-target, not per-file: a burst of edits under one watched
folder should trigger one full re-run of that target, not one per touched
file, since ingest_documents()/ingest_code() always walk the whole folder
anyway (there's no "just this one file" ingestion path -- purge-on-delete
depends on seeing the whole current file set each run).
"""
from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from api import ingest_jobs
from ingestion import runner, runner_jobs, watch_registry
from ingestion.enrichment.enrich import DEFAULT_MAX_CONCURRENCY

logger = logging.getLogger(__name__)

QUIET_PERIOD_SECONDS = 5.0

# Normalized watched path -> job_id of the most recently launched run for
# that path, shared between the watcher and api/routers/ingest.py's manual
# trigger endpoints so a manual click for a path that's also watched can't
# race a watcher-triggered run of the same path (and vice versa).
_active_paths: dict[str, str] = {}


def normalize_path(path: str) -> str:
    return str(Path(path).expanduser().resolve())


def is_path_active(path: str) -> bool:
    normalized = normalize_path(path)
    job_id = _active_paths.get(normalized)
    if job_id is None:
        return False
    job = ingest_jobs.get_ingest_job(job_id)
    # An unknown job_id (registry cleared, e.g. a test reset or a process
    # restart losing the in-memory dict) must not be treated as "still
    # active forever" -- only a job that's genuinely running blocks a path.
    if job is None or job["status"] in ingest_jobs.TERMINAL_STATUSES:
        _active_paths.pop(normalized, None)
        return False
    return True


def mark_path_active(path: str, job_id: str) -> None:
    _active_paths[normalize_path(path)] = job_id


class WatcherManager:
    def __init__(self) -> None:
        self._observers: dict[str, Observer] = {}
        self._pending_checks: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start_all(self) -> None:
        self._loop = asyncio.get_event_loop()
        for target in watch_registry.list_targets():
            if target["enabled"]:
                self._start_target(target)

    def stop_all(self) -> None:
        with self._lock:
            for timer in self._pending_checks.values():
                timer.cancel()
            self._pending_checks.clear()
        for observer in self._observers.values():
            observer.stop()
            observer.join(timeout=5)
        self._observers.clear()

    def start_target(self, target: dict) -> None:
        """Starts watching immediately -- used by POST /watch/targets so a
        newly-added target doesn't need a backend restart to take effect."""
        if self._loop is None:
            self._loop = asyncio.get_event_loop()
        self._start_target(target)

    def stop_target(self, target_id: str) -> None:
        observer = self._observers.pop(target_id, None)
        if observer is not None:
            observer.stop()
            observer.join(timeout=5)
        with self._lock:
            timer = self._pending_checks.pop(target_id, None)
            if timer is not None:
                timer.cancel()

    def _start_target(self, target: dict) -> None:
        directory = Path(target["path"]).expanduser().resolve()
        if not directory.is_dir():
            logger.warning("Watch target %s path is not a directory, skipping: %s", target["id"], directory)
            return
        observer = Observer()
        observer.schedule(_Handler(self, target["id"]), str(directory), recursive=True)
        observer.start()
        self._observers[target["id"]] = observer
        logger.info("Watching %s (target %s, kind=%s) for changes", directory, target["id"], target["kind"])

    def _schedule_check(self, target_id: str) -> None:
        with self._lock:
            existing = self._pending_checks.get(target_id)
            if existing is not None:
                existing.cancel()
            timer = threading.Timer(QUIET_PERIOD_SECONDS, self._trigger_if_still_enabled, args=(target_id,))
            timer.daemon = True
            self._pending_checks[target_id] = timer
            timer.start()

    def _trigger_if_still_enabled(self, target_id: str) -> None:
        with self._lock:
            self._pending_checks.pop(target_id, None)
        target = watch_registry.get_target(target_id)
        if target is None or not target["enabled"]:
            return
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._trigger(target), self._loop)

    async def _trigger(self, target: dict) -> None:
        path = target["path"]
        if is_path_active(path):
            logger.info("Skipping auto-triggered ingestion for %s -- a run is already active", path)
            return

        job_id = str(uuid.uuid4())
        ingest_jobs.register_job(job_id, kind=target["kind"])
        mark_path_active(path, job_id)
        if target["kind"] == "code":
            coro = runner_jobs.run_code_ingestion(job_id, path, None, DEFAULT_MAX_CONCURRENCY)
        else:
            coro = runner_jobs.run_document_ingestion(job_id, path)
        runner.run_tracked(job_id, coro)
        logger.info("Auto-triggered %s ingestion job %s for watched path %s", target["kind"], job_id, path)


class _Handler(FileSystemEventHandler):
    def __init__(self, manager: WatcherManager, target_id: str) -> None:
        self._manager = manager
        self._target_id = target_id

    def on_created(self, event) -> None:
        self._manager._schedule_check(self._target_id)

    def on_modified(self, event) -> None:
        self._manager._schedule_check(self._target_id)

    def on_deleted(self, event) -> None:
        self._manager._schedule_check(self._target_id)


watcher = WatcherManager()
