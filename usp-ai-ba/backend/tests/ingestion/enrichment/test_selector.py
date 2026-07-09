"""Covers ingestion/enrichment/agents/selector.py: build_agents() picking a
single primary agent per settings.INGEST_LLM_MODEL (Claude always paired
with an unconditional Ollama fallback; Ollama alone gated by
INGEST_OLLAMA_ENABLED), and AgentRouter's sticky circuit-breaker (once the
primary fails once, every subsequent call goes straight to the fallback,
never retrying the primary again within the same router instance) --
replacing the old round-robin that kept retrying a confirmed-broken Claude
account on every Nth file/part."""
from __future__ import annotations

import asyncio

import pytest

from config import settings

from ingestion.enrichment.agents.base import failure_result, success_result
from ingestion.enrichment.agents.selector import AgentRouter, build_agents
from ingestion.enrichment.models import SourceFile


def _file(name: str = "a.py") -> SourceFile:
    return SourceFile(absolute_path=name, relative_path=name, content="x", size_bytes=1)


class _StubAgent:
    def __init__(self, agent_name: str, responses: list):
        self._name = agent_name
        self._responses = list(responses)
        self.calls: list[str] = []

    def name(self) -> str:
        return self._name

    async def extract(self, file):
        self.calls.append(file.relative_path)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _success(agent_name: str) -> object:
    return success_result(_file(), agent_name, "a summary", 0, None, None)


def _failure(agent_name: str, message: str = "boom") -> object:
    return failure_result(_file(), agent_name, message, 0)


def test_single_agent_is_always_used_even_after_it_fails():
    only = _StubAgent("only", [_failure("only"), _success("only")])
    router = AgentRouter([only])

    asyncio.run(router.extract(_file()))
    asyncio.run(router.extract(_file()))

    # No fallback configured, so every call still goes to the one agent
    # regardless of whether an earlier call failed.
    assert len(only.calls) == 2


def test_rejects_empty_agent_list():
    with pytest.raises(ValueError):
        AgentRouter([])


def test_falls_back_after_primary_fails_once(caplog):
    primary = _StubAgent("claude-logic-extractor", [_failure("claude-logic-extractor", "credit balance too low")])
    fallback = _StubAgent("ollama-logic-extractor", [_success("ollama-logic-extractor"), _success("ollama-logic-extractor")])
    router = AgentRouter([primary, fallback])

    first = asyncio.run(router.extract(_file("a.py")))
    second = asyncio.run(router.extract(_file("b.py")))

    assert first.success is False
    assert second.success is True
    assert second.agent_name == "ollama-logic-extractor"
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1
    assert any("falling back to ollama-logic-extractor" in message for message in caplog.messages)


def test_never_retries_primary_once_it_has_failed():
    primary = _StubAgent("claude-logic-extractor", [_failure("claude-logic-extractor")])
    fallback = _StubAgent(
        "ollama-logic-extractor",
        [_success("ollama-logic-extractor") for _ in range(5)],
    )
    router = AgentRouter([primary, fallback])

    for i in range(6):
        asyncio.run(router.extract(_file(f"file{i}.py")))

    # Primary attempted exactly once (the failure that tripped the breaker);
    # every one of the remaining 5 calls went straight to the fallback.
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 5


def test_fallback_is_not_used_when_primary_succeeds():
    primary = _StubAgent("claude-logic-extractor", [_success("claude-logic-extractor")] * 3)
    fallback = _StubAgent("ollama-logic-extractor", [])
    router = AgentRouter([primary, fallback])

    for _ in range(3):
        result = asyncio.run(router.extract(_file()))
        assert result.success is True

    assert len(fallback.calls) == 0


def test_build_agents_claude_primary_always_pairs_with_ollama_fallback(monkeypatch):
    original_anthropic_key = settings.ANTHROPIC_API_KEY
    original_model = settings.INGEST_LLM_MODEL
    original_ollama_enabled = settings.INGEST_OLLAMA_ENABLED
    try:
        # Ollama fallback is unconditional when Claude is primary -- even
        # with INGEST_OLLAMA_ENABLED off, it must still be included.
        settings.apply_updates(
            {"ANTHROPIC_API_KEY": "test-key", "INGEST_LLM_MODEL": "claude", "INGEST_OLLAMA_ENABLED": False}
        )
        agents = build_agents()
    finally:
        settings.apply_updates(
            {
                "ANTHROPIC_API_KEY": original_anthropic_key,
                "INGEST_LLM_MODEL": original_model,
                "INGEST_OLLAMA_ENABLED": original_ollama_enabled,
            }
        )

    assert len(agents) == 2
    assert agents[0].name() == "claude-logic-extractor"
    assert agents[1].name() == "ollama-logic-extractor"


def test_build_agents_ollama_primary_returns_only_ollama_when_enabled():
    original_model = settings.INGEST_LLM_MODEL
    original_ollama_enabled = settings.INGEST_OLLAMA_ENABLED
    try:
        settings.apply_updates({"INGEST_LLM_MODEL": "ollama", "INGEST_OLLAMA_ENABLED": True})
        agents = build_agents()
    finally:
        settings.apply_updates({"INGEST_LLM_MODEL": original_model, "INGEST_OLLAMA_ENABLED": original_ollama_enabled})

    assert len(agents) == 1
    assert agents[0].name() == "ollama-logic-extractor"


def test_build_agents_returns_empty_when_ollama_primary_but_disabled():
    original_model = settings.INGEST_LLM_MODEL
    original_ollama_enabled = settings.INGEST_OLLAMA_ENABLED
    try:
        settings.apply_updates({"INGEST_LLM_MODEL": "ollama", "INGEST_OLLAMA_ENABLED": False})
        agents = build_agents()
    finally:
        settings.apply_updates({"INGEST_LLM_MODEL": original_model, "INGEST_OLLAMA_ENABLED": original_ollama_enabled})

    assert agents == []


def test_build_agents_passes_custom_build_messages_to_both_agent_types(monkeypatch):
    """Plan file section Q: enrich_documents.py calls build_agents(build_messages=...)
    to point both agent classes at the document-oriented prompt builder instead
    of their code-oriented default -- verify the override actually reaches
    both constructors, not just one."""
    original_anthropic_key = settings.ANTHROPIC_API_KEY
    original_model = settings.INGEST_LLM_MODEL
    captured: dict = {}

    def fake_build_messages(file):
        return "sys", "user"

    class _FakeClaudeAgent:
        def __init__(self, build_messages=None):
            captured["claude"] = build_messages

    class _FakeOllamaAgent:
        def __init__(self, build_messages=None):
            captured["ollama"] = build_messages

    import ingestion.enrichment.agents.selector as selector_module

    monkeypatch.setattr(selector_module, "ClaudeLogicExtractionAgent", _FakeClaudeAgent)
    monkeypatch.setattr(selector_module, "OllamaLogicExtractionAgent", _FakeOllamaAgent)
    try:
        settings.apply_updates({"ANTHROPIC_API_KEY": "test-key", "INGEST_LLM_MODEL": "claude"})
        build_agents(build_messages=fake_build_messages)
    finally:
        settings.apply_updates({"ANTHROPIC_API_KEY": original_anthropic_key, "INGEST_LLM_MODEL": original_model})

    assert captured["claude"] is fake_build_messages
    assert captured["ollama"] is fake_build_messages


def test_build_agents_uses_default_build_messages_when_not_given():
    original_anthropic_key = settings.ANTHROPIC_API_KEY
    original_model = settings.INGEST_LLM_MODEL
    try:
        settings.apply_updates({"ANTHROPIC_API_KEY": "test-key", "INGEST_LLM_MODEL": "claude"})
        agents = build_agents()
    finally:
        settings.apply_updates({"ANTHROPIC_API_KEY": original_anthropic_key, "INGEST_LLM_MODEL": original_model})

    assert len(agents) >= 1
