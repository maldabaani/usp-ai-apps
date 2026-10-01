"""Gateway for all chat/embedding model access.

Every chat call goes through a single process-wide asyncio.Semaphore sized by
MAX_PARALLEL_DEVS, so concurrent Developer instances never exceed what Ollama
can serve in parallel (OLLAMA_NUM_PARALLEL must be >= MAX_PARALLEL_DEVS).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_ollama import ChatOllama, OllamaEmbeddings

from devcrew.llm.models_config import ModelsConfig, ModelSpec, Role

logger = logging.getLogger(__name__)

ChatFactory = Callable[[ModelSpec], BaseChatModel]
ModelLister = Callable[[], Awaitable[set[str]]]


@dataclass(frozen=True)
class CallScope:
    """Which run / graph node / task an LLM call belongs to (set by node instrumentation)."""

    run_id: str
    node: str
    task_id: str | None = None
    # the run's own model per role (Phase 16), as (role, model) pairs
    models: tuple[tuple[str, str], ...] = ()
    # the task's ts.iterations at node entry + 1 (which developer attempt this call belongs
    # to); None for run-level nodes with no task_id (planner, architect, run coordinator)
    iteration: int | None = None


llm_scope: ContextVar[CallScope | None] = ContextVar("llm_scope", default=None)


@dataclass(frozen=True)
class CallUsage:
    scope: CallScope
    role: Role
    model: str
    input_tokens: int
    output_tokens: int
    duration_ms: int


UsageHook = Callable[[CallUsage], Awaitable[None]]


@dataclass(frozen=True)
class CallPrompt:
    """Full request/response of one LLM call (opt-in, DEVCREW_LOG_PROMPTS)."""

    scope: CallScope
    role: Role
    model: str
    messages: Sequence[BaseMessage]
    reply: AIMessage
    input_tokens: int
    output_tokens: int
    duration_ms: int


PromptHook = Callable[[CallPrompt], Awaitable[None]]


def token_counts(message: AIMessage) -> tuple[int, int]:
    """(input, output) tokens of a model answer. Ollama leaves out `prompt_eval_count` when the
    whole prompt came from its cache, and langchain-ollama then drops the usage metadata
    altogether; read Ollama's own counts in that case (a missing count is 0)."""
    meta = message.usage_metadata
    if meta:
        return meta["input_tokens"], meta["output_tokens"]
    raw = message.response_metadata or {}
    return int(raw.get("prompt_eval_count") or 0), int(raw.get("eval_count") or 0)


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
        tokens_in, tokens_out = token_counts(message)
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
        self.on_usage: UsageHook | None = None  # per-call usage (run usage and budgets)
        self.on_prompt: PromptHook | None = None  # opt-in full prompt/reply capture
        # the models installed in Ollama (the per-run model picker); None: unknown
        self.model_lister: ModelLister | None = None if chat_factory else self._ollama_models

    async def _ollama_models(self) -> set[str]:
        async with httpx.AsyncClient(timeout=5.0) as http:
            resp = await http.get(f"{self.base_url}/api/tags")
            resp.raise_for_status()
            return {m["name"] for m in resp.json().get("models", [])}

    async def installed_models(self) -> set[str] | None:
        """Models installed in Ollama, or None when unknown (Ollama unreachable)."""
        if self.model_lister is None:
            return None
        try:
            return await self.model_lister()
        except Exception as exc:  # the picker falls back to free text
            logger.warning("could not list Ollama models: %s", exc)
            return None

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
        """The role's model; a run can choose its own model per role (other settings kept)."""
        spec = self.models.for_role(role)
        scope = llm_scope.get()
        if scope is not None and scope.models:
            chosen = dict(scope.models).get(role.value)
            if chosen and chosen != spec.model:
                return spec.model_copy(update={"model": chosen})
        return spec

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
        output_format: str | dict[str, Any] | None = None,
    ) -> AIMessage:
        """Invoke the role's chat model, bounded by the global LLM semaphore.

        `output_format` is passed to Ollama's `format` ("json" or a JSON schema).
        """
        model: Any = self.chat_model(role)
        if tools:
            model = model.bind_tools(list(tools))
        kwargs: dict[str, Any] = {}
        if output_format is not None:
            kwargs["format"] = output_format
        async with self._semaphore:
            started = time.monotonic()
            result = await model.ainvoke(list(messages), **kwargs)
            duration_ms = int((time.monotonic() - started) * 1000)
        if not isinstance(result, AIMessage):
            raise TypeError(f"Expected AIMessage from chat model, got {type(result).__name__}")
        self.usage.record(role, result)
        scope = llm_scope.get()
        if (self.on_usage is not None or self.on_prompt is not None) and scope is not None:
            tokens_in, tokens_out = token_counts(result)
            model = self.spec(role).model
            if self.on_usage is not None:
                await self.on_usage(
                    CallUsage(
                        scope=scope,
                        role=role,
                        model=model,
                        input_tokens=tokens_in,
                        output_tokens=tokens_out,
                        duration_ms=duration_ms,
                    )
                )
            if self.on_prompt is not None:
                await self.on_prompt(
                    CallPrompt(
                        scope=scope,
                        role=role,
                        model=model,
                        messages=list(messages),
                        reply=result,
                        input_tokens=tokens_in,
                        output_tokens=tokens_out,
                        duration_ms=duration_ms,
                    )
                )
        return result
