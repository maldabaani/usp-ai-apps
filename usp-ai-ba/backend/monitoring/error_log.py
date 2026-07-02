"""Ring-buffered store of captured error records, persisted as JSON in
settings.JOBS_DIR -- same _load()/_save()/module-level-cache pattern as
api/job_registry.py, so entries survive a backend restart."""
from __future__ import annotations

import json
import os
import time

from config import settings

_ERROR_LOG_PATH = os.path.join(settings.JOBS_DIR, "error_log.json")
_MAX_ENTRIES = 500

_errors: list[dict] | None = None


def _load() -> list[dict]:
    global _errors
    if _errors is not None:
        return _errors
    if os.path.exists(_ERROR_LOG_PATH):
        with open(_ERROR_LOG_PATH) as f:
            _errors = json.load(f)
    else:
        _errors = []
    return _errors


def _save() -> None:
    os.makedirs(settings.JOBS_DIR, exist_ok=True)
    with open(_ERROR_LOG_PATH, "w") as f:
        json.dump(_errors, f)


def record_error(logger_name: str, level: str, message: str, traceback: str | None) -> None:
    errors = _load()
    errors.append(
        {
            # Epoch milliseconds -- matches CodeMind's Instant.toEpochMilli(),
            # so the Angular monitoring page can merge/sort both apps'
            # records by timestamp directly, without unit conversion.
            "timestamp": int(time.time() * 1000),
            "logger": logger_name,
            "level": level,
            "message": message,
            "traceback": traceback,
        }
    )
    del errors[:-_MAX_ENTRIES]
    _save()


def list_errors() -> list[dict]:
    return list(reversed(_load()))
