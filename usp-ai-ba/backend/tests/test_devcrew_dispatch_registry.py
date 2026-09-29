"""Covers the merge plan's Phase 5: api/devcrew_dispatch_registry.py -- the
thin, JSON-file-persisted, one-directional cross-reference from a
StoryForge assessment (job_id, epic_index) to a DevCrew run_id. Follows
tests/test_assess_router.py's isolated-JOBS_DIR fixture pattern since this
registry, like api/job_registry.py, caches its loaded list at module level
and resolves its file path from settings.JOBS_DIR at import time.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    # _REGISTRY_PATH is computed once from settings.JOBS_DIR at module import
    # time (same design as api/job_registry.py) -- monkeypatching
    # settings.JOBS_DIR alone has no effect on it after that. Patch the
    # already-computed path directly instead, for genuine per-test isolation.
    import api.devcrew_dispatch_registry as registry

    monkeypatch.setattr(registry, "_REGISTRY_PATH", os.path.join(str(tmp_path), "devcrew_dispatches.json"))
    registry._dispatches = None
    yield
    registry._dispatches = None


def test_record_and_list_round_trips():
    from api.devcrew_dispatch_registry import list_dispatches_for_job, record_dispatch

    record_dispatch("job-1", 0, "Epic One", "run-a")

    entries = list_dispatches_for_job("job-1")

    assert len(entries) == 1
    assert entries[0]["job_id"] == "job-1"
    assert entries[0]["epic_index"] == 0
    assert entries[0]["epic_title"] == "Epic One"
    assert entries[0]["run_id"] == "run-a"
    assert "created_at" in entries[0]


def test_list_is_newest_first_and_scoped_to_the_job():
    from api.devcrew_dispatch_registry import list_dispatches_for_job, record_dispatch

    record_dispatch("job-1", 0, "Epic One", "run-a")
    record_dispatch("job-1", 1, "Epic Two", "run-b")
    record_dispatch("job-2", 0, "Other Job's Epic", "run-c")

    entries = list_dispatches_for_job("job-1")

    assert [e["run_id"] for e in entries] == ["run-b", "run-a"]  # newest first
    assert all(e["job_id"] == "job-1" for e in entries)


def test_list_empty_for_a_job_never_dispatched():
    from api.devcrew_dispatch_registry import list_dispatches_for_job

    assert list_dispatches_for_job("never-sent") == []


def test_latest_dispatch_for_epic_returns_the_most_recent_one():
    from api.devcrew_dispatch_registry import latest_dispatch_for_epic, record_dispatch

    record_dispatch("job-1", 0, "Epic One", "run-a")
    record_dispatch("job-1", 0, "Epic One", "run-a-retry")  # re-dispatched

    latest = latest_dispatch_for_epic("job-1", 0)

    assert latest is not None
    assert latest["run_id"] == "run-a-retry"


def test_latest_dispatch_for_epic_none_when_not_dispatched():
    from api.devcrew_dispatch_registry import latest_dispatch_for_epic, record_dispatch

    record_dispatch("job-1", 0, "Epic One", "run-a")

    assert latest_dispatch_for_epic("job-1", 1) is None  # different epic, same job
    assert latest_dispatch_for_epic("job-2", 0) is None  # different job


def test_persists_across_a_fresh_load():
    import api.devcrew_dispatch_registry as registry

    registry.record_dispatch("job-1", 0, "Epic One", "run-a")

    registry._dispatches = None  # simulate a fresh process reading the JSON file back
    entries = registry.list_dispatches_for_job("job-1")

    assert len(entries) == 1
    assert entries[0]["run_id"] == "run-a"
