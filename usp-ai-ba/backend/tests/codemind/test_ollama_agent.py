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

from codemind.agents.ollama_agent import OllamaLogicExtractionAgent
from codemind.models import SourceFile


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
