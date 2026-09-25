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
    if "summarize a test run" in str(first.content).lower():
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


class FakeRunner:
    """In-memory CommandRunner: records calls, returns scripted results (default: success)."""

    def __init__(self) -> None:
        from app.sandbox.runner import CommandResult

        self._result = CommandResult
        self.calls: list[tuple[str | None, str, str, bool, str]] = []
        self.scripts: dict[str, list[tuple[int, str]]] = {}
        self.released: list[str | None] = []
        self.cleaned: list[str] = []

    def script(self, needle: str, *results: tuple[int, str]) -> None:
        self.scripts.setdefault(needle, []).extend(results)

    async def run(
        self,
        target: Any,
        command: str,
        *,
        cwd: str = ".",
        timeout_s: int | None = None,
        network: bool = False,
    ) -> Any:
        self.calls.append((target.task_id, command, cwd, network, target.image_stack))
        for needle, queue in self.scripts.items():
            if needle in command and queue:
                code, output = queue.pop(0)
                return self._result(exit_code=code, output=output)
        return self._result(exit_code=0, output=f"ok: {command}")

    async def release(self, run_id: str, task_id: str | None) -> None:
        self.released.append(task_id)

    async def cleanup_run(self, run_id: str) -> None:
        self.cleaned.append(run_id)

    def commands(self, *, network: bool | None = None) -> list[tuple[str | None, str, str]]:
        return [
            (task, cmd, cwd)
            for task, cmd, cwd, net, _ in self.calls
            if network is None or net == network
        ]


class HashEmbeddingModel:
    """Deterministic bag-of-words embeddings: related text -> similar vectors, no Ollama."""

    DIM = 256

    def __init__(self) -> None:
        self.documents_embedded = 0

    def _vec(self, text: str) -> list[float]:
        import hashlib
        import math
        import re

        vec = [0.0] * self.DIM
        for token in re.findall(r"[a-z][a-z0-9]+", text.lower()):
            if token in ("search_document", "search_query"):
                continue
            h = int(hashlib.md5(token.encode()).hexdigest(), 16)
            vec[h % self.DIM] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        self.documents_embedded += len(texts)
        return [self._vec(t) for t in texts]

    async def aembed_query(self, text: str) -> list[float]:
        return self._vec(text)


def _where_match(meta: dict[str, Any], where: dict[str, Any] | None) -> bool:
    if not where:
        return True
    if "$and" in where:
        return all(_where_match(meta, w) for w in where["$and"])
    for key, cond in where.items():
        if isinstance(cond, dict):
            if "$in" in cond and meta.get(key) not in cond["$in"]:
                return False
            if "$eq" in cond and meta.get(key) != cond["$eq"]:
                return False
        elif meta.get(key) != cond:
            return False
    return True


class FakeCollection:
    """In-memory subset of the Chroma collection API used by CodeIndex (cosine space)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.rows: dict[str, tuple[list[float], str, dict[str, Any]]] = {}

    def upsert(
        self,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        for i, e, d, m in zip(ids, embeddings, documents, metadatas, strict=True):
            assert all(v is not None for v in m.values()), "chroma rejects None metadata"
            self.rows[i] = (e, d, m)

    def delete(self, *, where: dict[str, Any] | None = None) -> None:
        for key in [k for k, (_, _, m) in self.rows.items() if _where_match(m, where)]:
            del self.rows[key]

    def get(self, *, where: dict[str, Any] | None = None, include: Any = None) -> dict[str, Any]:
        rows = [(k, m) for k, (_, _, m) in self.rows.items() if _where_match(m, where)]
        return {"ids": [k for k, _ in rows], "metadatas": [m for _, m in rows]}

    def count(self) -> int:
        return len(self.rows)

    def query(
        self, *, query_embeddings: list[list[float]], n_results: int, include: Any = None
    ) -> dict[str, Any]:
        q = query_embeddings[0]
        scored = sorted(
            (
                (1.0 - sum(a * b for a, b in zip(q, e, strict=True)), d, m)
                for e, d, m in self.rows.values()
            ),
            key=lambda t: t[0],
        )[:n_results]
        return {
            "documents": [[d for _, d, _ in scored]],
            "metadatas": [[m for _, _, m in scored]],
            "distances": [[s for s, _, _ in scored]],
        }


class FakeChroma:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def get_or_create_collection(self, name: str, **kwargs: Any) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection(name))

    def delete_collection(self, name: str) -> None:
        del self.collections[name]

    def list_collections(self) -> list[FakeCollection]:
        return list(self.collections.values())
