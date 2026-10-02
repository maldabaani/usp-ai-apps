from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from devcrew.llm.client import CallScope, LLMGateway, _with_cache_control, llm_scope
from devcrew.llm.models_config import ModelsConfig, ModelSpec, Role


class FakeChat:
    """Records peak concurrency and every call's messages; Ollama is never contacted."""

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.specs: list[ModelSpec] = []
        self.received: list[list[BaseMessage]] = []

    async def ainvoke(self, messages: list[BaseMessage], **kwargs: Any) -> AIMessage:
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.received.append(messages)
        await asyncio.sleep(0.02)
        self.active -= 1
        return AIMessage(
            content="ok",
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )


def _gateway(fake: FakeChat, max_parallel: int) -> LLMGateway:
    def factory(spec: ModelSpec) -> BaseChatModel:
        fake.specs.append(spec)
        return cast(BaseChatModel, fake)

    models = ModelsConfig.model_validate(
        {"defaults": {"model": "m"}, "roles": {"planner": {"temperature": 0.3}}}
    )
    return LLMGateway(models, base_url="http://x", max_parallel=max_parallel, chat_factory=factory)


@pytest.mark.parametrize("max_parallel", [1, 2, 3])
async def test_semaphore_bounds_concurrency(max_parallel: int) -> None:
    fake = FakeChat()
    gw = _gateway(fake, max_parallel)
    await asyncio.gather(*(gw.ainvoke(Role.DEVELOPER, [HumanMessage("hi")]) for _ in range(8)))
    assert fake.peak == max_parallel


async def test_usage_is_tracked_per_role() -> None:
    fake = FakeChat()
    gw = _gateway(fake, 2)
    await gw.ainvoke(Role.PLANNER, [HumanMessage("a")])
    await gw.ainvoke(Role.QA, [HumanMessage("b")])
    await gw.ainvoke(Role.QA, [HumanMessage("c")])
    assert gw.usage.by_role[Role.QA].calls == 2
    total = gw.usage.total()
    assert (total.input_tokens, total.output_tokens, total.calls) == (30, 15, 3)


def test_chat_models_cached_per_spec() -> None:
    fake = FakeChat()
    gw = _gateway(fake, 2)
    gw.chat_model(Role.DEVELOPER)
    gw.chat_model(Role.QA)  # same resolved spec as developer
    gw.chat_model(Role.PLANNER)  # different temperature
    assert [s.temperature for s in fake.specs] == [0.1, 0.3]


def test_invalid_parallelism() -> None:
    with pytest.raises(ValueError):
        models = ModelsConfig.model_validate({"defaults": {"model": "m"}})
        LLMGateway(models, base_url="x", max_parallel=0)


def test_default_factory_builds_chat_ollama() -> None:
    models = {"defaults": {"model": "m", "num_ctx": 4096, "num_predict": 512}}
    gw = LLMGateway(
        ModelsConfig.model_validate(models),
        base_url="http://ollama:11434",
        max_parallel=1,
    )
    model: Any = gw.chat_model(Role.DEVELOPER)
    assert (model.model, model.num_ctx, model.num_predict, model.base_url) == (
        "m",
        4096,
        512,
        "http://ollama:11434",
    )


def _cloud_spec() -> ModelSpec:
    return ModelSpec(
        model="claude-sonnet-5",
        provider="anthropic",
        num_ctx=200_000,
        num_predict=8192,
        temperature=0.2,
        structured_format="none",
    )


def test_default_factory_builds_chat_anthropic_when_provider_is_anthropic() -> None:
    models = ModelsConfig.model_validate({"defaults": {"model": "m"}})
    gw = LLMGateway(models, base_url="http://ollama:11434", max_parallel=1, anthropic_api_key="k")
    model = gw._default_chat_factory(_cloud_spec())
    assert isinstance(model, ChatAnthropic)
    assert (model.model, model.max_tokens) == ("claude-sonnet-5", 8192)
    # Not sent at all: current-generation Claude models reject an explicit temperature outright.
    assert model.temperature is None


def test_spec_returns_cloud_spec_when_scope_engine_is_anthropic() -> None:
    models = ModelsConfig.model_validate({"defaults": {"model": "m"}})
    cloud = _cloud_spec()
    gw = LLMGateway(models, base_url="http://ollama:11434", max_parallel=1, cloud_spec=cloud)
    token = llm_scope.set(CallScope(run_id="r", node="developer", engine="anthropic"))
    try:
        for role in Role:
            assert gw.spec(role) == cloud
    finally:
        llm_scope.reset(token)


def test_spec_falls_back_to_ollama_when_no_cloud_spec_configured() -> None:
    # engine="anthropic" but this server has no DEVCREW_ANTHROPIC_API_KEY configured (cloud_spec
    # is None) -- a run already in flight when the key is removed degrades to Ollama instead of
    # crashing; a *new* dispatch is refused up front by the API layer (devcrew/api/runs.py's
    # _checked_engine), which this gateway-level fallback is not a substitute for.
    models = ModelsConfig.model_validate({"defaults": {"model": "m"}})
    gw = LLMGateway(models, base_url="http://ollama:11434", max_parallel=1)
    token = llm_scope.set(CallScope(run_id="r", node="developer", engine="anthropic"))
    try:
        assert gw.spec(Role.DEVELOPER).model == "m"
        assert gw.spec(Role.DEVELOPER).provider == "ollama"
    finally:
        llm_scope.reset(token)


def test_spec_ignores_engine_when_not_anthropic() -> None:
    models = ModelsConfig.model_validate({"defaults": {"model": "m"}})
    gw = LLMGateway(models, base_url="http://ollama:11434", max_parallel=1, cloud_spec=_cloud_spec())
    token = llm_scope.set(CallScope(run_id="r", node="developer", engine="ollama"))
    try:
        assert gw.spec(Role.DEVELOPER).model == "m"
    finally:
        llm_scope.reset(token)


def test_with_cache_control_marks_the_system_message() -> None:
    out = _with_cache_control([SystemMessage(content="instructions"), HumanMessage(content="hi")])
    assert out[0].content == [
        {"type": "text", "text": "instructions", "cache_control": {"type": "ephemeral"}}
    ]
    assert out[1].content == "hi"  # untouched


def test_with_cache_control_is_a_noop_without_a_leading_system_message() -> None:
    messages = [HumanMessage(content="hi")]
    assert _with_cache_control(messages) == messages


def test_with_cache_control_does_not_double_wrap_structured_content() -> None:
    already = SystemMessage(content=[{"type": "text", "text": "x", "cache_control": {}}])
    out = _with_cache_control([already, HumanMessage(content="hi")])
    assert out[0] is already


async def test_ainvoke_caches_the_system_message_only_for_anthropic_calls() -> None:
    fake = FakeChat()

    def factory(spec: ModelSpec) -> BaseChatModel:
        return cast(BaseChatModel, fake)

    models = ModelsConfig.model_validate({"defaults": {"model": "m"}})
    gw = LLMGateway(
        models,
        base_url="http://ollama:11434",
        max_parallel=1,
        chat_factory=factory,
        cloud_spec=_cloud_spec(),
    )
    messages = [SystemMessage(content="rules and instructions"), HumanMessage(content="go")]

    # Plain Ollama call: the system message is sent as-is.
    await gw.ainvoke(Role.DEVELOPER, messages)
    assert fake.received[-1][0].content == "rules and instructions"

    # Claude Cloud call (engine="anthropic" in scope): the gateway wraps it for caching.
    token = llm_scope.set(CallScope(run_id="r", node="developer", engine="anthropic"))
    try:
        await gw.ainvoke(Role.DEVELOPER, messages)
    finally:
        llm_scope.reset(token)
    sent_system = fake.received[-1][0].content
    assert isinstance(sent_system, list)
    assert sent_system[0]["cache_control"] == {"type": "ephemeral"}
    assert sent_system[0]["text"] == "rules and instructions"
    # The caller's own messages list is never mutated in place.
    assert messages[0].content == "rules and instructions"
