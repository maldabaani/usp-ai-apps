"""Registry of submitted assessment jobs, used to back the dashboard's job list.

Persisted as JSON in settings.JOBS_DIR so the list (and therefore the ability
to find a job_id again) survives a backend restart -- the job's actual
pipeline state lives separately in the LangGraph checkpoint DB
(pipeline/graph.py), this registry only exists so the dashboard has something
to list before you know a job_id to look up directly.
"""
from __future__ import annotations

import json
import os
import time

from config import settings

_REGISTRY_PATH = os.path.join(settings.JOBS_DIR, "assess_jobs.json")
_jobs: list[dict] | None = None


def _load() -> list[dict]:
    global _jobs
    if _jobs is not None:
        return _jobs
    if os.path.exists(_REGISTRY_PATH):
        with open(_REGISTRY_PATH) as f:
            _jobs = json.load(f)
    else:
        _jobs = []
    return _jobs


def _save() -> None:
    os.makedirs(settings.JOBS_DIR, exist_ok=True)
    with open(_REGISTRY_PATH, "w") as f:
        json.dump(_jobs, f)


def register_assess_job(
    job_id: str, ppm_number: str, ppm_name: str, system_name: str, output_mode: str
) -> None:
    jobs = _load()
    jobs.append(
        {
            "job_id": job_id,
            "ppm_number": ppm_number,
            "ppm_name": ppm_name,
            "system_name": system_name,
            "output_mode": output_mode,
            "created_at": time.time(),
        }
    )
    _save()


def list_assess_jobs() -> list[dict]:
    return list(reversed(_load()))
