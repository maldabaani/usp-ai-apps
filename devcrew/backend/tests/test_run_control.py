"""Phase 14: run usage and budgets, tests after every wave, retrying failed runs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.events.types import Event, EventType
from app.graph.nodes import finish
from app.services.usage import RunUsage, over_budget, summarize
from tests.api_harness import Api, api
from tests.fakes import FakeRunner
from tests.graph_harness import make_harness

REQUEST = {"request": "Build a TODO API with CRUD", "repo_target": "o/r"}
T0 = datetime(2026, 9, 1, tzinfo=UTC)


async def create(a: Api, **extra: Any) -> str:
    resp = await a.client.post("/runs", json={**REQUEST, **extra})
    assert resp.status_code == 201, resp.text
    run_id = str(resp.json()["id"])
    await a.settle(run_id)
    return run_id


def ev(i: int, type_: EventType, seconds: float, **payload: Any) -> Event:
    return Event(
        id=i,
        run_id="r",
        type=type_,
        node="n",
        task_id=payload.pop("task_id", None),
        payload=payload,
        created_at=T0 + timedelta(seconds=seconds),
    )


# ------------------------------------------------------------------------------ usage
def test_usage_summary_counts_tokens_and_excludes_waiting_time() -> None:
    events = [
        ev(1, EventType.NODE_STARTED, 0),
        ev(
            2,
            EventType.LLM_USAGE,
            10,
            role="planner",
            input_tokens=100,
            output_tokens=20,
            duration_ms=4000,
        ),
        ev(3, EventType.AWAITING_INPUT, 20),
        ev(4, EventType.NODE_STARTED, 320),  # the human answered after 5 minutes
        ev(
            5,
            EventType.LLM_USAGE,
            330,
            role="developer",
            task_id="T1",
            input_tokens=50,
            output_tokens=5,
            duration_ms=1500,
        ),
        ev(6, EventType.NODE_FINISHED, 340),
    ]
    usage = summarize(events, finished=True)
    assert (usage.calls, usage.input_tokens, usage.output_tokens, usage.total_tokens) == (
        2,
        150,
        25,
        175,
    )
    assert usage.model_seconds == 5.5
    assert (usage.elapsed_s, usage.waiting_s, usage.active_s) == (340, 300, 40)
    assert [line.key for line in usage.by_role] == ["planner", "developer"]
    assert [(line.key, line.total_tokens) for line in usage.by_task] == [("T1", 55)]
    # still waiting: the open wait counts up to now
    live = summarize(events[:3], finished=False, now=T0 + timedelta(seconds=80))
    assert live.waiting_s == 60 and live.active_s == 20


def test_over_budget() -> None:
    usage = RunUsage(total_tokens=5000, active_s=600)
    assert over_budget(usage, None) == []
    assert over_budget(usage, {"tokens": 0, "minutes": 0}) == []
    assert over_budget(usage, {"tokens": 6000, "minutes": 11}) == []
    reasons = over_budget(usage, {"tokens": 5000, "minutes": 10})
    assert reasons == ["5,000 tokens used (budget 5,000)", "10.0 working minutes (budget 10 min)"]


async def test_usage_endpoint_per_role_and_task(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        run_id = await create(a)
        await a.approve(run_id)
        await a.approve(run_id)
        usage = (await a.client.get(f"/runs/{run_id}/usage")).json()
        roles = {line["key"]: line for line in usage["by_role"]}
        assert {"planner", "architect", "developer", "reviewer", "qa"} <= set(roles)
        assert roles["planner"]["input_tokens"] >= 100
        assert usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"] > 0
        assert {line["key"] for line in usage["by_task"]} == {"T1", "T2"}
        assert usage["budget"] is None


# ------------------------------------------------------------------------------ budgets
async def test_token_budget_asks_before_the_next_wave(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        run_id = await create(a, token_budget=200)  # planner + architect already use more
        await a.approve(run_id)
        run = await a.approve(run_id)
        assert run["status"] == "needs_human"
        [pending] = run["pending"]
        assert pending["kind"] == "budget"
        assert "tokens used (budget 200)" in pending["data"]["reasons"][0]
        assert all(t["status"] == "pending" for t in run["tasks"].values())
        wf = (await a.client.get(f"/runs/{run_id}/workflow")).json()
        assert wf["attention"] == []  # shown by the run page, not on a node

        run = await a.approve(run_id)  # continue: the limit grows by the budget again
        assert run["status"] in ("needs_human", "awaiting_final_approval")
        usage = (await a.client.get(f"/runs/{run_id}/usage")).json()
        assert usage["budget"]["tokens"] > 200


async def test_stopping_at_the_budget_ends_the_run(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        run_id = await create(a, token_budget=200)
        await a.approve(run_id)
        await a.approve(run_id)
        resp = await a.client.post(
            f"/runs/{run_id}/resume", json={"action": "reject", "feedback": "too expensive"}
        )
        assert resp.status_code == 202
        run = await a.settle(run_id)
        assert run["status"] == "cancelled"
        assert run["error"].startswith("stopped at the budget")


async def test_settings_budget_applies_when_the_run_sets_none(tmp_path: Path) -> None:
    async with api(tmp_path, run_token_budget=200) as a:
        run_id = await create(a)
        await a.approve(run_id)
        run = await a.approve(run_id)
        assert run["pending"][0]["kind"] == "budget"
        other = await create(a, token_budget=0)  # a run can switch the default off
        await a.approve(other)
        assert (await a.approve(other))["status"] == "awaiting_final_approval"


# ------------------------------------------------------------------------------ wave tests
class BreaksAfterFirstWave(FakeRunner):
    """The integration branch's tests fail once (after the first wave)."""

    def __init__(self) -> None:
        super().__init__()
        self.failures = 1
        self.branch_runs = 0

    async def run(self, target: Any, command: str, **kwargs: Any) -> Any:
        if target.task_id is None and "pytest" in command:
            self.branch_runs += 1
            if self.failures:
                self.failures -= 1
                self.calls.append((None, command, ".", False, target.image_stack))
                return self._result(exit_code=1, output="FAILED tests/test_t1.py::test_x")
        return await super().run(target, command, **kwargs)


async def test_tests_after_a_wave_add_a_fix_task_first(tmp_path: Path) -> None:
    runner = BreaksAfterFirstWave()
    h = make_harness(tmp_path, runner=runner)
    async with api(tmp_path, harness=h) as a:
        run_id = await create(a)
        await a.approve(run_id)
        run = await a.approve(run_id)
        assert run["status"] == "awaiting_final_approval"
        tasks = run["tasks"]
        assert tasks["WAVEFIX1"]["status"] == "merged"
        assert tasks["WAVEFIX1"]["wave"] == 2 and tasks["T2"]["wave"] == 3
        plan = {t["id"]: t for t in run["plan"]["tasks"]}
        assert "WAVEFIX1" in plan["T2"]["depends_on"]
        assert "FAILED tests/test_t1.py::test_x" in plan["WAVEFIX1"]["description"]
        # after wave 1 (fails), after wave 2 (passes), then the integration step
        assert runner.branch_runs == 3


async def test_wave_tests_can_be_switched_off(tmp_path: Path) -> None:
    runner = BreaksAfterFirstWave()
    h = make_harness(tmp_path, runner=runner, wave_tests_enabled=False)
    async with api(tmp_path, harness=h) as a:
        run_id = await create(a)
        await a.approve(run_id)
        run = await a.approve(run_id)
        assert "WAVEFIX1" not in run["tasks"]
        assert runner.branch_runs == 1  # only the integration step (which fails)


# ------------------------------------------------------------------------------ retry
async def test_retry_continues_a_failed_run_from_its_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = finish.run_project_tests
    calls = {"n": 0}

    async def flaky(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("sandbox went away")
        return await real(*args, **kwargs)

    monkeypatch.setattr(finish, "run_project_tests", flaky)
    async with api(tmp_path) as a:
        run_id = await create(a)
        await a.approve(run_id)
        run = await a.approve(run_id)
        assert run["status"] == "failed" and "sandbox went away" in run["error"]
        merged_before = {t: s["commit"] for t, s in run["tasks"].items()}

        resp = await a.client.post(f"/runs/{run_id}/retry")
        assert resp.status_code == 200, resp.text
        run = await a.settle(run_id)
        assert run["status"] == "awaiting_final_approval" and run["error"] is None
        # the tasks were not redone: only the failed step ran again
        assert {t: s["commit"] for t, s in run["tasks"].items()} == merged_before

        again = await a.client.post(f"/runs/{run_id}/retry")
        assert again.status_code == 409
