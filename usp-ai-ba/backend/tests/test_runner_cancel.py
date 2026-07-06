"""Covers pipeline/runner.py's run_tracked()/cancel_job() -- the "Stop
Assessment" feature. pipeline.runner.get_graph() is monkeypatched to a fake
graph exposing only aget_state()/aupdate_state() (the two calls cancel_job
actually makes), matching this codebase's mocked-ChatClient testing
convention rather than exercising a real LangGraph/SQLite checkpointer.
"""
import asyncio

import pytest

from pipeline import runner


class _Snapshot:
    def __init__(self, values):
        self.values = values


class _FakeGraph:
    def __init__(self, state: dict | None):
        self._state = state
        self.update_calls: list[dict] = []

    async def aget_state(self, config):
        return _Snapshot(dict(self._state) if self._state is not None else None)

    async def aupdate_state(self, config, values, as_node=None):
        self.update_calls.append(values)
        self._state.update(values)


@pytest.fixture(autouse=True)
def _clear_active_tasks():
    runner._active_tasks.clear()
    yield
    runner._active_tasks.clear()


def _patch_graph(monkeypatch, fake_graph: _FakeGraph) -> None:
    async def fake_get_graph():
        return fake_graph

    monkeypatch.setattr(runner, "get_graph", fake_get_graph)


def test_cancel_job_cancels_in_flight_task_and_marks_status(monkeypatch):
    fake_graph = _FakeGraph({"status": "generating"})
    _patch_graph(monkeypatch, fake_graph)

    async def _body():
        cancelled = False

        async def _long_running():
            nonlocal cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled = True
                raise

        job_id = "job-in-flight"
        runner.run_tracked(job_id, _long_running())
        assert job_id in runner._active_tasks
        # Give the task an actual scheduling tick so it reaches its own
        # await point before being cancelled -- in production, cancel_job()
        # is only ever reached via a separate later HTTP request, long after
        # the tracked task has had many such ticks; the fake graph below
        # never performs real I/O, so nothing would otherwise yield control
        # back to the loop between run_tracked() and cancel_job().
        await asyncio.sleep(0)

        result = await runner.cancel_job(job_id)

        assert cancelled
        assert result["status"] == "cancelled"
        assert job_id not in runner._active_tasks

    asyncio.run(_body())


def test_cancel_job_marks_status_when_no_task_is_running(monkeypatch):
    # e.g. "clarifying" with a human-in-the-loop pause -- there's no asyncio
    # Task in flight at all, just a checkpoint waiting for an answer that
    # will now never come. Cancelling must still succeed.
    fake_graph = _FakeGraph({"status": "clarifying"})
    _patch_graph(monkeypatch, fake_graph)

    result = asyncio.run(runner.cancel_job("job-waiting"))

    assert result["status"] == "cancelled"


def test_cancel_job_raises_for_unknown_job(monkeypatch):
    fake_graph = _FakeGraph(None)
    _patch_graph(monkeypatch, fake_graph)

    with pytest.raises(ValueError, match="No job found"):
        asyncio.run(runner.cancel_job("does-not-exist"))


@pytest.mark.parametrize("status", ["done", "error", "cancelled"])
def test_cancel_job_raises_for_already_terminal_job(monkeypatch, status):
    fake_graph = _FakeGraph({"status": status})
    _patch_graph(monkeypatch, fake_graph)

    with pytest.raises(ValueError, match="already"):
        asyncio.run(runner.cancel_job("job-terminal"))


def test_run_tracked_removes_task_from_registry_once_it_finishes_on_its_own():
    async def _body():
        job_id = "job-finishes-quickly"

        async def _quick():
            return "done"

        task = runner.run_tracked(job_id, _quick())
        await task

        # The done-callback runs on the next loop iteration, not synchronously
        # the instant the task completes -- yield once so it's had a chance.
        await asyncio.sleep(0)
        assert job_id not in runner._active_tasks

    asyncio.run(_body())
