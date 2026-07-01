"""In-memory registry of submitted assessment jobs, used to back the dashboard's job list."""
from __future__ import annotations

import time

_jobs: list[dict] = []


def register_assess_job(
    job_id: str, ppm_number: str, ppm_name: str, system_name: str
) -> None:
    _jobs.append(
        {
            "job_id": job_id,
            "ppm_number": ppm_number,
            "ppm_name": ppm_name,
            "system_name": system_name,
            "created_at": time.time(),
        }
    )


def list_assess_jobs() -> list[dict]:
    return list(reversed(_jobs))
