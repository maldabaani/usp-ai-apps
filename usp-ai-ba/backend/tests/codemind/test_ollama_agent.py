"""Covers codemind/agents/ollama_agent.py, ported from
com.jslogicextractor.agent.OllamaLogicExtractionAgent. The ChatOllama
instance itself is real (safe to construct -- no network call happens until
.ainvoke() is called); ainvoke() is monkeypatched on the class (ChatOllama is
a pydantic model, which rejects setting attributes that aren't declared
fields directly on an instance), mirroring the Java test's ChatClient mock.
"""
import asyncio
from pathlib import Path
from types import SimpleNamespace

from langchain_ollama import ChatOllama

from codemind.agents.ollama_agent import MAX_OUTPUT_TOKENS, OllamaLogicExtractionAgent
from codemind.models import SourceFile
from config import settings


def _source_file() -> SourceFile:
    return SourceFile(Path("/repo/src/index.js"), "src/index.js", "console.log('hi');", 19)


def test_returns_successful_extraction_with_usage_on_happy_path(monkeypatch):
    agent = OllamaLogicExtractionAgent()

    async def fake_ainvoke(self, messages):
        return SimpleNamespace(content="extracted logic", usage_metadata={"input_tokens": 100, "output_tokens": 50})

    monkeypatch.setattr(ChatOllama, "ainvoke", fake_ainvoke)

    result = asyncio.run(agent.extract(_source_file()))

    assert result.success is True
    assert result.agent_name == "ollama-logic-extractor"
    assert result.content == "extracted logic"
    assert result.prompt_tokens == 100
    assert result.completion_tokens == 50


def test_returns_failure_when_chat_client_throws(monkeypatch):
    agent = OllamaLogicExtractionAgent()

    async def fake_ainvoke(self, messages):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(ChatOllama, "ainvoke", fake_ainvoke)

    result = asyncio.run(agent.extract(_source_file()))

    assert result.success is False
    assert result.error_message == "connection refused"


def test_uses_shared_ollama_num_ctx_setting_not_a_hardcoded_value():
    agent = OllamaLogicExtractionAgent()
    assert agent._chat.num_ctx == settings.OLLAMA_NUM_CTX


def test_caps_num_predict_so_a_non_terminating_generation_cannot_run_to_the_full_context():
    agent = OllamaLogicExtractionAgent()
    assert agent._chat.num_predict == MAX_OUTPUT_TOKENS
    assert agent._chat.num_predict < settings.OLLAMA_NUM_CTX


def test_rebuilds_chat_client_only_when_settings_generation_changes():
    agent = OllamaLogicExtractionAgent()

    first_chat = agent._chat
    first_generation = agent._built_at_generation
    assert first_chat is not None
    assert first_generation == settings.settings_generation

    # Calling _rebuild_if_needed() again with nothing changed must not rebuild.
    agent._rebuild_if_needed()
    assert agent._chat is first_chat

    original_num_ctx = settings.OLLAMA_NUM_CTX
    try:
        settings.apply_updates({"OLLAMA_NUM_CTX": 4096})
        agent._rebuild_if_needed()

        assert agent._chat is not first_chat
        assert agent._chat.num_ctx == 4096
        assert agent._built_at_generation == settings.settings_generation
    finally:
        settings.apply_updates({"OLLAMA_NUM_CTX": original_num_ctx})
