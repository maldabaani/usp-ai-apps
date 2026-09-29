"""Records which DevCrew run(s), if any, a StoryForge assessment's epic was
sent to -- see the merge plan's Phase 5 and Phase 7 ("Send to DevCrew").

Deliberately NOT a shared table with DevCrew's own Run/RunEvent/
WatchedRepo/IssueRun/RunMessage (devcrew/db/models.py) or with StoryForge's
own job_registry.py -- matches this repo's existing, documented precedent
(see CLAUDE.md's "Job registries" section) that shape-similar registries
tracking genuinely different lifecycles stay separate, independent
implementations rather than being forced into one shared concept. This is
a thin, one-directional cross-reference only: StoryForge (job_id, epic
index) -> DevCrew run_id, persisted the same JSON-file way
api/job_registry.py already is, so the Review page can show "sent to
DevCrew, run <id>, status X" without StoryForge's own pipeline state
(pipeline/state.py's StoryForgeState) needing any new field for it.
"""
from __future__ import annotations

import json
import os
import time

from config import settings

_REGISTRY_PATH = os.path.join(settings.JOBS_DIR, "devcrew_dispatches.json")
_dispatches: list[dict] | None = None


def _load() -> list[dict]:
    global _dispatches
    if _dispatches is not None:
        return _dispatches
    if os.path.exists(_REGISTRY_PATH):
        with open(_REGISTRY_PATH) as f:
            _dispatches = json.load(f)
    else:
        _dispatches = []
    return _dispatches


def _save() -> None:
    os.makedirs(settings.JOBS_DIR, exist_ok=True)
    with open(_REGISTRY_PATH, "w") as f:
        json.dump(_dispatches, f)


def record_dispatch(job_id: str, epic_index: int, epic_title: str, run_id: str) -> None:
    dispatches = _load()
    dispatches.append(
        {
            "job_id": job_id,
            "epic_index": epic_index,
            "epic_title": epic_title,
            "run_id": run_id,
            "created_at": time.time(),
        }
    )
    _save()


def list_dispatches_for_job(job_id: str) -> list[dict]:
    """All dispatches for this assessment job, newest first -- an epic can
    have more than one entry if it was sent to DevCrew more than once."""
    return [d for d in reversed(_load()) if d["job_id"] == job_id]


def latest_dispatch_for_epic(job_id: str, epic_index: int) -> dict | None:
    for dispatch in reversed(_load()):
        if dispatch["job_id"] == job_id and dispatch["epic_index"] == epic_index:
            return dispatch
    return None
