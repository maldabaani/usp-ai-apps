"""Shared ChatOllama/ChatAnthropic construction for clarify_node/generate_node,
branching on settings.ASSESSMENT_MODEL -- the same "ollama"/"claude" pattern
api/routers/ask.py's _get_ask_chat() already established for Ask Technical/
Business, generalized here since two nodes (with different output-token
budgets) need it instead of one.

Pure construction only -- no caching. Each node keeps its own cached
_get_llm() (see generate.py/clarify.py), so a settings-screen change still
hot-reloads via the existing settings_generation counter convention, and
existing tests asserting on generate._get_llm()/clarify._get_llm()'s cache
identity/attributes stay unaffected when ASSESSMENT_MODEL is left at its
default ("ollama").
"""
from __future__ import annotations

from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama

from config import settings


def build_llm(model_kind: str, *, ollama_num_predict: int, claude_max_tokens: int, seed: int) -> ChatOllama | ChatAnthropic:
    if model_kind == "claude":
        return ChatAnthropic(
            model=settings.CLAUDE_MODEL,
            api_key=settings.ANTHROPIC_API_KEY,
            timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS,
            temperature=0,
            max_tokens=claude_max_tokens,
        )
    return ChatOllama(
        model=settings.OLLAMA_LLM_MODEL,
        base_url=settings.OLLAMA_BASE_URL,
        num_predict=ollama_num_predict,
        num_ctx=settings.OLLAMA_NUM_CTX,
        temperature=0,
        seed=seed,
        timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS,
    )


def build_ollama_llm(*, num_predict: int, seed: int) -> ChatOllama:
    """Always Ollama, regardless of ASSESSMENT_MODEL -- used for the
    Claude-failed fallback path, which by definition must not also be Claude."""
    llm = build_llm("ollama", ollama_num_predict=num_predict, claude_max_tokens=0, seed=seed)
    assert isinstance(llm, ChatOllama)
    return llm
