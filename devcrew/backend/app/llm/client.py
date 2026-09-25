"""Gateway for all chat/embedding model access.

Every chat call goes through a single process-wide asyncio.Semaphore sized by
MAX_PARALLEL_DEVS, so concurrent Developer instances never exceed what Ollama
can serve in parallel (OLLAMA_NUM_PARALLEL must be >= MAX_PARALLEL_DEVS).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_ollama import ChatOllama, OllamaEmbeddings

from app.llm.models_config import ModelsConfig, ModelSpec, Role

logger = logging.getLogger(__name__)

ChatFactory = Callable[[ModelSpec], BaseChatModel]


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.calls += 1


@dataclass
class UsageTracker:
    """Token counts per role (used by the benchmark and the run summary)."""

    by_role: dict[Role, TokenUsage] = field(default_factory=dict)

    def record(self, role: Role, message: AIMessage) -> None:
        meta = message.usage_metadata
        tokens_in = meta["input_tokens"] if meta else 0
        tokens_out = meta["output_tokens"] if meta else 0
        self.by_role.setdefault(role, TokenUsage()).add(tokens_in, tokens_out)

    def total(self) -> TokenUsage:
        out = TokenUsage()
        for usage in self.by_role.values():
            out.input_tokens += usage.input_tokens
            out.output_tokens += usage.output_tokens
            out.calls += usage.calls
        return out


class LLMGateway:
    def __init__(
        self,
        models: ModelsConfig,
        *,
        base_url: str,
        max_parallel: int,
        request_timeout_s: float = 600.0,
        chat_factory: ChatFactory | None = None,
    ) -> None:
        if max_parallel < 1:
            raise ValueError("max_parallel must be >= 1")
        self.models = models
        self.base_url = base_url
        self.max_parallel = max_parallel
        self._timeout = request_timeout_s
        self._semaphore = asyncio.Semaphore(max_parallel)
        self._chat_factory = chat_factory or self._default_chat_factory
        self._chat_cache: dict[ModelSpec, BaseChatModel] = {}
        self.usage = UsageTracker()

    def _default_chat_factory(self, spec: ModelSpec) -> BaseChatModel:
        return ChatOllama(
            model=spec.model,
            base_url=self.base_url,
            num_ctx=spec.num_ctx,
            num_predict=spec.num_predict,
            temperature=spec.temperature,
            client_kwargs={"timeout": self._timeout},
        )

    def spec(self, role: Role) -> ModelSpec:
        return self.models.for_role(role)

    def chat_model(self, role: Role) -> BaseChatModel:
        spec = self.spec(role)
        model = self._chat_cache.get(spec)
        if model is None:
            model = self._chat_factory(spec)
            self._chat_cache[spec] = model
        return model

    def embeddings(self) -> OllamaEmbeddings:
        return OllamaEmbeddings(model=self.models.embeddings.model, base_url=self.base_url)

    async def ainvoke(
        self,
        role: Role,
        messages: Sequence[BaseMessage],
        *,
        tools: Sequence[Any] | None = None,
    ) -> AIMessage:
        """Invoke the role's chat model, bounded by the global LLM semaphore."""
        model: Any = self.chat_model(role)
        if tools:
            model = model.bind_tools(list(tools))
        async with self._semaphore:
            result = await model.ainvoke(list(messages))
        if not isinstance(result, AIMessage):
            raise TypeError(f"Expected AIMessage from chat model, got {type(result).__name__}")
        self.usage.record(role, result)
        return result
