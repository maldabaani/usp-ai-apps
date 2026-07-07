"""Covers ingestion/enrichment/agents/claude_agent.py, moved from codemind/agents/claude_agent.py (originally ported from
com.jslogicextractor.agent.ClaudeLogicExtractionAgent -- verifies the
settings-screen hot-reload path (settings.settings_generation -> the agent
rebuilding its ChatAnthropic client) without a real network call, mirroring
the Java test's reflection-based rebuildIfNeeded() drive (Python needs no
reflection since the fields aren't private)."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

from langchain_anthropic import ChatAnthropic

from config import settings

from ingestion.enrichment.agents.claude_agent import ClaudeLogicExtractionAgent
from ingestion.enrichment.models import SourceFile


def _source_file() -> SourceFile:
    return SourceFile(Path("/repo/src/index.js"), "src/index.js", "console.log('hi');", 19)


def test_uses_custom_build_messages_when_given(monkeypatch):
    """Plan file section Q: enrich_documents.py points this same agent class
    at doc_prompts.build_extraction_messages instead of the code-oriented
    default -- verifies the override is actually used, not just accepted."""
    captured: list = []

    def fake_build_messages(file):
        return "custom system prompt", "custom user content"

    agent = ClaudeLogicExtractionAgent(build_messages=fake_build_messages)

    async def fake_ainvoke(self, messages):
        captured.extend(messages)
        return SimpleNamespace(content="extracted logic", usage_metadata=None)

    monkeypatch.setattr(ChatAnthropic, "ainvoke", fake_ainvoke)

    asyncio.run(agent.extract(_source_file()))

    assert captured[0].content == "custom system prompt"
    assert captured[1].content == "custom user content"


def test_defaults_to_code_prompt_builder_when_no_override_given():
    from ingestion.enrichment.prompts import build_extraction_messages

    agent = ClaudeLogicExtractionAgent()
    assert agent._build_messages is build_extraction_messages


def test_rebuilds_chat_client_only_when_settings_generation_changes():
    agent = ClaudeLogicExtractionAgent()

    first_chat = agent._chat
    first_generation = agent._built_at_generation
    assert first_chat is not None
    assert first_generation == settings.settings_generation

    # Calling _rebuild_if_needed() again with nothing changed must not rebuild.
    agent._rebuild_if_needed()
    assert agent._chat is first_chat

    original_key = settings.ANTHROPIC_API_KEY
    original_model = settings.CLAUDE_MODEL
    try:
        settings.apply_updates({"ANTHROPIC_API_KEY": "key-two", "CLAUDE_MODEL": "model-two"})
        agent._rebuild_if_needed()

        assert agent._chat is not first_chat
        assert agent._built_at_generation == settings.settings_generation
    finally:
        settings.apply_updates({"ANTHROPIC_API_KEY": original_key, "CLAUDE_MODEL": original_model})
