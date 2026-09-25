from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.llm.client import LLMGateway
from app.llm.models_config import ModelsConfig, ModelSpec, Role


class FakeChat:
    """Records peak concurrency; Ollama is never contacted."""

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.specs: list[ModelSpec] = []

    async def ainvoke(self, messages: list[BaseMessage]) -> AIMessage:
        self.active += 1
        self.peak = max(self.peak, self.active)
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
