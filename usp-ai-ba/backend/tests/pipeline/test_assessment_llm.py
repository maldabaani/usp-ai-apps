"""Covers pipeline/nodes/assessment_llm.py's build_llm/build_ollama_llm --
pure construction, branching on the model_kind the caller passes in (never
reads settings.ASSESSMENT_MODEL itself; generate.py/clarify.py do that)."""
from __future__ import annotations

from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama

from config import settings
from pipeline.nodes import assessment_llm


def test_build_llm_ollama_uses_configured_ollama_settings():
    llm = assessment_llm.build_llm("ollama", ollama_num_predict=4096, claude_max_tokens=8192, seed=7)

    assert isinstance(llm, ChatOllama)
    assert llm.model == settings.OLLAMA_LLM_MODEL
    assert llm.base_url == settings.OLLAMA_BASE_URL
    assert llm.num_predict == 4096
    assert llm.num_ctx == settings.OLLAMA_NUM_CTX
    assert llm.seed == 7


def test_build_llm_claude_uses_configured_anthropic_settings():
    llm = assessment_llm.build_llm("claude", ollama_num_predict=4096, claude_max_tokens=8192, seed=7)

    assert isinstance(llm, ChatAnthropic)
    assert llm.model == settings.CLAUDE_MODEL
    assert llm.max_tokens == 8192


def test_build_ollama_llm_is_always_ollama_regardless_of_current_settings():
    original = settings.ASSESSMENT_MODEL
    try:
        settings.apply_updates({"ASSESSMENT_MODEL": "claude"})
        llm = assessment_llm.build_ollama_llm(num_predict=2048, seed=42)
        assert isinstance(llm, ChatOllama)
        assert llm.num_predict == 2048
        assert llm.seed == 42
    finally:
        settings.apply_updates({"ASSESSMENT_MODEL": original})
