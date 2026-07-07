"""Covers ingestion/enrichment/agents/selector.py, moved from codemind/agents/selector.py (originally ported from
com.jslogicextractor.agent.AgentSelector)."""
import pytest

from config import settings

from ingestion.enrichment.agents.selector import AgentSelector, build_agents


class _StubAgent:
    def __init__(self, agent_name: str) -> None:
        self._name = agent_name

    def name(self) -> str:
        return self._name

    async def extract(self, file):
        raise NotImplementedError("not used in this test")


def test_round_robins_across_multiple_agents():
    a = _StubAgent("a")
    b = _StubAgent("b")
    selector = AgentSelector([a, b])

    assert selector.next().name() == "a"
    assert selector.next().name() == "b"
    assert selector.next().name() == "a"


def test_single_agent_always_returns_itself():
    only = _StubAgent("only")
    selector = AgentSelector([only])

    assert selector.next() is only
    assert selector.next() is only


def test_rejects_empty_agent_list():
    with pytest.raises(ValueError):
        AgentSelector([])


def test_build_agents_passes_custom_build_messages_to_both_agent_types(monkeypatch):
    """Plan file section Q: enrich_documents.py calls build_agents(build_messages=...)
    to point both agent classes at the document-oriented prompt builder instead
    of their code-oriented default -- verify the override actually reaches
    both constructors, not just one."""
    original_anthropic_key = settings.ANTHROPIC_API_KEY
    original_ollama_enabled = settings.INGEST_OLLAMA_ENABLED
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
        settings.apply_updates({"ANTHROPIC_API_KEY": "test-key", "INGEST_OLLAMA_ENABLED": True})
        build_agents(build_messages=fake_build_messages)
    finally:
        settings.apply_updates(
            {"ANTHROPIC_API_KEY": original_anthropic_key, "INGEST_OLLAMA_ENABLED": original_ollama_enabled}
        )

    assert captured["claude"] is fake_build_messages
    assert captured["ollama"] is fake_build_messages


def test_build_agents_uses_default_build_messages_when_not_given(monkeypatch):
    original_anthropic_key = settings.ANTHROPIC_API_KEY
    try:
        settings.apply_updates({"ANTHROPIC_API_KEY": "test-key"})
        agents = build_agents()
    finally:
        settings.apply_updates({"ANTHROPIC_API_KEY": original_anthropic_key})

    assert len(agents) >= 1
