"""Persists extraction job snapshots to disk, one file per job.

Ported from com.jslogicextractor.orchestration.JobStore, but keyed under
JOBS_DIR/codemind_jobs/ (StoryForge's existing JOBS_DIR root) rather than
Java's ~/.js-logic-extractor/jobs -- there's now one unified jobs directory
for the whole merged backend, not a separate per-app location. File-per-job
(not one shared growing array, unlike api/job_registry.py's list-of-all-jobs
file) matters here specifically: a job's progress counters update on every
processed file, and a per-file-fan-out job can have thousands of files --
rewriting one growing shared array on every update would be a real
regression Java's original file-per-job design already avoided.
"""
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)


def _store_directory() -> Path:
    return Path(settings.JOBS_DIR) / "codemind_jobs"


def save(snapshot: dict) -> None:
    directory = _store_directory()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        file = directory / f"{snapshot['id']}.json"
        file.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning("Failed to persist job %s: %s", snapshot.get("id"), e)


def load_all() -> list[dict]:
    directory = _store_directory()
    if not directory.is_dir():
        return []
    snapshots = []
    for file in directory.glob("*.json"):
        snapshot = _load_snapshot(file)
        if snapshot is not None:
            snapshots.append(snapshot)
    return snapshots


def delete(job_id: uuid.UUID) -> None:
    file = _store_directory() / f"{job_id}.json"
    try:
        file.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("Failed to delete job file %s: %s", file.name, e)


def delete_all() -> None:
    directory = _store_directory()
    if not directory.is_dir():
        return
    for file in directory.glob("*.json"):
        try:
            file.unlink(missing_ok=True)
        except OSError as e:
            logger.warning("Failed to delete job file %s: %s", file.name, e)


def _load_snapshot(file: Path) -> dict | None:
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Failed to load job snapshot from %s: %s", file.name, e)
        return None
