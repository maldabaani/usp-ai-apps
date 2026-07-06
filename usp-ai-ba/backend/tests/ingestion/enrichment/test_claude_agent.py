"""Covers ingestion/enrichment/agents/claude_agent.py, moved from codemind/agents/claude_agent.py (originally ported from
com.jslogicextractor.agent.ClaudeLogicExtractionAgent -- verifies the
settings-screen hot-reload path (settings.settings_generation -> the agent
rebuilding its ChatAnthropic client) without a real network call, mirroring
the Java test's reflection-based rebuildIfNeeded() drive (Python needs no
reflection since the fields aren't private)."""
from config import settings

from ingestion.enrichment.agents.claude_agent import ClaudeLogicExtractionAgent


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
