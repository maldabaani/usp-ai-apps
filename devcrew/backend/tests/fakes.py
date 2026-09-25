"""Scripted stand-in for ChatOllama (Ollama is never contacted in tests)."""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from app.llm.client import LLMGateway
from app.llm.models_config import ModelsConfig, ModelSpec

_ids = itertools.count(1)


def tool_call(name: str, **args: Any) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": f"call{next(_ids)}", "type": "tool_call"}],
    )


def final(content: str | dict[str, Any]) -> AIMessage:
    text = content if isinstance(content, str) else json.dumps(content)
    return AIMessage(
        content=text, usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
    )


def role_of(messages: Sequence[BaseMessage]) -> str:
    first = messages[0]
    assert isinstance(first, SystemMessage), "first message must be the role's system prompt"
    header = str(first.content).splitlines()[0]
    role = header.removeprefix("# Role: ").strip().lower()
    if "summarize a test run" in str(first.content):
        return "qa_report"
    return role


@dataclass
class Call:
    role: str
    messages: list[BaseMessage]
    has_tools: bool
    kwargs: dict[str, Any]
    tool_names: tuple[str, ...] = ()

    @property
    def last(self) -> BaseMessage:
        return self.messages[-1]

    @property
    def fresh(self) -> bool:
        """True on the first call of an agent turn (only system + context so far)."""
        return len(self.messages) == 2 and isinstance(self.messages[1], HumanMessage)

    def text(self) -> str:
        return "\n".join(str(m.content) for m in self.messages)


Responder = Callable[[Call], AIMessage]


@dataclass
class Brain:
    """Per-role responders; tests override the ones they care about."""

    responders: dict[str, Responder] = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)

    def calls_for(self, role: str) -> list[Call]:
        return [c for c in self.calls if c.role == role]

    def respond(self, call: Call) -> AIMessage:
        self.calls.append(call)
        responder = self.responders.get(call.role)
        if responder is None:
            raise AssertionError(f"no responder for role {call.role}")
        return responder(call)


class ScriptedChat:
    def __init__(self, brain: Brain, tools: list[Any] | None = None) -> None:
        self.brain = brain
        self.tools = tools

    def bind_tools(self, tools: list[Any]) -> ScriptedChat:
        return ScriptedChat(self.brain, tools)

    async def ainvoke(self, messages: list[BaseMessage], **kwargs: Any) -> AIMessage:
        names = tuple(sorted(t["function"]["name"] for t in self.tools or []))
        call = Call(role_of(messages), list(messages), bool(self.tools), kwargs, names)
        return self.brain.respond(call)


def gateway(
    brain: Brain, models: dict[str, Any] | None = None, max_parallel: int = 2
) -> LLMGateway:
    def factory(spec: ModelSpec) -> BaseChatModel:
        return cast(BaseChatModel, ScriptedChat(brain))

    config = ModelsConfig.model_validate(models or {"defaults": {"model": "fake"}})
    return LLMGateway(
        config, base_url="http://fake", max_parallel=max_parallel, chat_factory=factory
    )


def tool_results(call: Call) -> list[ToolMessage]:
    return [m for m in call.messages if isinstance(m, ToolMessage)]
