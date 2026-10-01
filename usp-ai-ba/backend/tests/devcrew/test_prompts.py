"""PROMPT event capture (DEVCREW_LOG_PROMPTS): the LLMGateway.on_prompt hook, GraphDeps
wiring/gating, iteration numbering, store/bus filtering, and the GET /runs/{id}/prompts API."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from devcrew.events.store import InMemoryEventStore
from devcrew.events.types import EventType
from devcrew.llm.client import CallPrompt, CallScope, LLMGateway, llm_scope
from devcrew.llm.models_config import ModelsConfig, ModelSpec, Role
from tests.devcrew.api_harness import api
from tests.devcrew.fakes import FakeRunner, final
from tests.devcrew.graph_harness import make_harness
from tests.devcrew.test_sandbox_graph import PYTEST, RUN as SANDBOX_RUN, run_to_final

RUN = "run-prompts-test000000000"


class FakeChat:
    async def ainvoke(self, messages: list[BaseMessage], **kwargs: Any) -> AIMessage:
        return AIMessage(
            content="ok", usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        )


def gateway() -> LLMGateway:
    def factory(spec: ModelSpec) -> BaseChatModel:
        return cast(BaseChatModel, FakeChat())

    models = ModelsConfig.model_validate({"defaults": {"model": "m"}})
    return LLMGateway(models, base_url="http://x", max_parallel=2, chat_factory=factory)


# ------------------------------------------------------------------------- LLMGateway unit


async def test_on_prompt_fires_with_the_full_exchange() -> None:
    gw = gateway()
    captured: list[CallPrompt] = []

    async def on_prompt(p: CallPrompt) -> None:
        captured.append(p)

    gw.on_prompt = on_prompt
    scope = llm_scope.set(CallScope(run_id=RUN, node="developer", task_id="T1", iteration=1))
    try:
        messages = [SystemMessage(content="sys"), HumanMessage(content="hi")]
        reply = await gw.ainvoke(Role.DEVELOPER, messages)
    finally:
        llm_scope.reset(scope)

    assert len(captured) == 1
    p = captured[0]
    assert p.scope.task_id == "T1" and p.scope.iteration == 1
    assert p.role is Role.DEVELOPER and p.model == "m"
    assert [m.content for m in p.messages] == ["sys", "hi"]
    assert p.reply is reply
    assert (p.input_tokens, p.output_tokens) == (10, 5)


async def test_on_prompt_does_not_fire_without_a_scope() -> None:
    gw = gateway()
    captured: list[CallPrompt] = []
    gw.on_prompt = lambda p: captured.append(p)  # type: ignore[arg-type,return-value]

    await gw.ainvoke(Role.DEVELOPER, [HumanMessage("hi")])  # no llm_scope set

    assert captured == []


async def test_on_prompt_none_is_a_no_op() -> None:
    gw = gateway()  # on_prompt left at its default None
    scope = llm_scope.set(CallScope(run_id=RUN, node="developer"))
    try:
        await gw.ainvoke(Role.DEVELOPER, [HumanMessage("hi")])  # must not raise
    finally:
        llm_scope.reset(scope)


# ------------------------------------------------------------------------- graph integration


async def test_prompts_capture_the_right_iteration_across_a_retry(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.script(PYTEST, (1, "FAILED tests/test_t1.py::test_x - assert 1 == 2\n1 failed"))
    h = make_harness(tmp_path, runner=runner, log_prompts=True)
    h.brain.responders["qa_report"] = lambda c: final({"failed": [], "summary": "x"})

    await run_to_final(h)

    events = [e for e in await h.events(SANDBOX_RUN) if e.type == EventType.PROMPT]
    t1_developer = [e for e in events if e.node == "developer" and e.task_id == "T1"]
    assert t1_developer, "expected at least one developer PROMPT event for T1"
    iterations = {e.payload["iteration"] for e in t1_developer}
    assert iterations == {1, 2}, f"expected attempts 1 and 2, got {iterations}"
    # a captured entry carries the full exchange, not just metadata
    sample = t1_developer[0]
    assert sample.payload["messages"] and sample.payload["reply"]
    assert sample.payload["role"] == "developer"


async def test_log_prompts_off_by_default_emits_no_prompt_events(tmp_path: Path) -> None:
    h = make_harness(tmp_path)  # log_prompts defaults to False
    from tests.devcrew.graph_harness import APPROVE

    await h.driver.start(RUN, "Build a TODO API", "me/todo", False)
    await h.driver.resume(RUN, APPROVE)
    await h.driver.resume(RUN, APPROVE)

    events = await h.events(RUN)
    assert not any(e.type == EventType.PROMPT for e in events)
    assert any(e.type == EventType.LLM_USAGE for e in events)  # usage tracking is unaffected


# ------------------------------------------------------------------------- store / bus filtering


async def test_in_memory_store_filters_by_type_and_task() -> None:
    from devcrew.events.types import EventIn

    store = InMemoryEventStore()
    await store.append(EventIn(run_id=RUN, type=EventType.PROMPT, task_id="T1", payload={}))
    await store.append(EventIn(run_id=RUN, type=EventType.PROMPT, task_id="T2", payload={}))
    await store.append(EventIn(run_id=RUN, type=EventType.LLM_USAGE, task_id="T1", payload={}))

    by_type = await store.list_after(RUN, 0, 100, event_type=EventType.PROMPT)
    assert {e.task_id for e in by_type} == {"T1", "T2"}

    by_both = await store.list_after(RUN, 0, 100, event_type=EventType.PROMPT, task_id="T1")
    assert len(by_both) == 1 and by_both[0].task_id == "T1"


async def test_event_bus_list_after_is_a_pass_through(tmp_path: Path) -> None:
    h = make_harness(tmp_path, log_prompts=True)
    from tests.devcrew.graph_harness import APPROVE

    await h.driver.start(RUN, "Build a TODO API", "me/todo", False)
    await h.driver.resume(RUN, APPROVE)
    await h.driver.resume(RUN, APPROVE)

    from_bus = await h.deps.events.list_after(RUN, 0, 500, event_type=EventType.PROMPT)
    from_store = await h.store.list_after(RUN, 0, 500, event_type=EventType.PROMPT)
    assert [e.id for e in from_bus] == [e.id for e in from_store]


# ------------------------------------------------------------------------------------- API


async def test_get_prompts_endpoint(tmp_path: Path) -> None:
    async with api(tmp_path, log_prompts=True) as a:
        resp = await a.client.post(
            "/runs", json={"request": "Build a tiny TODO API please", "repo_target": "me/todo"}
        )
        assert resp.status_code == 201, resp.text
        run_id = resp.json()["id"]
        await a.settle(run_id)
        await a.approve(run_id)
        await a.approve(run_id)

        resp = await a.client.get(f"/runs/{run_id}/prompts")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["enabled"] is True
        assert body["entries"], "expected at least one captured prompt"
        entry = body["entries"][0]
        assert "messages" in entry and "reply" in entry and "role" in entry

        task_ids = {e["task_id"] for e in body["entries"] if e["task_id"]}
        if task_ids:
            one = next(iter(task_ids))
            scoped = await a.client.get(f"/runs/{run_id}/prompts", params={"task_id": one})
            assert scoped.status_code == 200
            assert all(e["task_id"] == one for e in scoped.json()["entries"])


async def test_get_prompts_disabled_by_default(tmp_path: Path) -> None:
    async with api(tmp_path) as a:  # log_prompts not set -> False
        resp = await a.client.post(
            "/runs", json={"request": "Build a tiny TODO API please", "repo_target": "me/todo"}
        )
        run_id = resp.json()["id"]
        await a.settle(run_id)

        resp = await a.client.get(f"/runs/{run_id}/prompts")
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is False
        assert body["entries"] == []


async def test_get_prompts_404_on_unknown_run(tmp_path: Path) -> None:
    async with api(tmp_path, log_prompts=True) as a:
        resp = await a.client.get("/runs/does-not-exist/prompts")
        assert resp.status_code == 404
