"""Persisted history of completed/failed/cancelled ingestion runs, used to
back a jobs-history UI and to answer "has ingestion ever run" reliably
across backend restarts -- api/ingest_jobs.py's own tracker is pure
in-memory and loses everything on restart. Mirrors api/job_registry.py's
exact _load()/_save() idiom, keyed by job_id instead of StoryForge's
assessment jobs.

Only terminal transitions are appended here (in-flight progress stays in
api/ingest_jobs.py's in-memory dict, matching that module's existing
acceptable-to-lose-on-restart behavior for progress specifically -- only the
*fact that a run happened, and how it ended* needs to survive a restart).
"""
from __future__ import annotations

import json
import os
import time

from config import settings

_entries: list[dict] | None = None


def _registry_path() -> str:
    # Resolved fresh on every call (not cached at module-import time) so
    # tests that monkeypatch settings.JOBS_DIR per-test actually get an
    # isolated file, rather than every test in a run silently sharing
    # whatever path was current the first time this module was imported.
    return os.path.join(settings.JOBS_DIR, "ingest_jobs.json")


def _load() -> list[dict]:
    global _entries
    if _entries is not None:
        return _entries
    path = _registry_path()
    if os.path.exists(path):
        with open(path) as f:
            _entries = json.load(f)
    else:
        _entries = []
    return _entries


def _save() -> None:
    os.makedirs(settings.JOBS_DIR, exist_ok=True)
    with open(_registry_path(), "w") as f:
        json.dump(_entries, f)


def record_completed_job(
    job_id: str, kind: str, status: str, result: dict | None, errors: list[str], source_path: str = ""
) -> None:
    entries = _load()
    entries.append(
        {
            "job_id": job_id,
            "kind": kind,
            "status": status,
            "result": result,
            "errors": errors,
            "finished_at": time.time(),
            "source_path": source_path,
        }
    )
    _save()


def list_history() -> list[dict]:
    return list(reversed(_load()))


def clear_history() -> int:
    """Wipes every persisted history entry. Irreversible -- no soft-delete or
    audit trail, matching this codebase's other hard-delete precedents (e.g.
    api/routers/corpus.py's delete-source endpoint)."""
    global _entries
    count = len(_load())
    _entries = []
    _save()
    return count
