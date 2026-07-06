"""Persisted set of filesystem paths to auto-watch for changes, triggering a
full re-ingestion run on create/modify/delete. Mirrors api/job_registry.py's
exact _load()/_save() module-cache idiom, keyed by a server-generated target
id rather than a job_id.
"""
from __future__ import annotations

import json
import os
import time
import uuid

from config import settings

_entries: list[dict] | None = None


def _registry_path() -> str:
    return os.path.join(settings.JOBS_DIR, "watch_targets.json")


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


def add_target(path: str, kind: str) -> dict:
    """kind picks which of ingest_documents()/ingest_code() a target's
    changes re-trigger -- no auto-detection from the path itself."""
    entries = _load()
    target = {
        "id": str(uuid.uuid4()),
        "path": path,
        "kind": kind,
        "enabled": True,
        "created_at": time.time(),
    }
    entries.append(target)
    _save()
    return target


def list_targets() -> list[dict]:
    return list(_load())


def get_target(target_id: str) -> dict | None:
    return next((t for t in _load() if t["id"] == target_id), None)


def set_enabled(target_id: str, enabled: bool) -> dict | None:
    target = get_target(target_id)
    if target is None:
        return None
    target["enabled"] = enabled
    _save()
    return target


def remove_target(target_id: str) -> bool:
    entries = _load()
    remaining = [t for t in entries if t["id"] != target_id]
    if len(remaining) == len(entries):
        return False
    global _entries
    _entries = remaining
    _save()
    return True
