"""Covers generate_node/clarify_node's new ASSESSMENT_MODEL branching and
Claude->Ollama fallback wiring end-to-end, via hand-mocked _get_llm()/
_get_ollama_fallback_llm() (matching this codebase's established fake-LLM-
client convention -- see tests/pipeline/test_llm_retry.py's _FakeChat).
"""
from __future__ import annotations

import asyncio

from config import settings
from pipeline.nodes import clarify, generate
from pipeline.state import new_state

_real_sleep = asyncio.sleep


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChat:
    def __init__(self, responses: list):
        self._responses = list(responses)
        self.ainvoke_calls = 0

    def model_copy(self, *, update=None):
        return self

    async def ainvoke(self, messages):
        self.ainvoke_calls += 1
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return _FakeResponse(response)


def _new_state(**overrides) -> dict:
    state = new_state(
        job_id="job-1",
        ppm_number="123",
        ppm_name="Test PPM",
        system_name="Test System",
        solution_doc_path="",
        review_mode=False,
        output_mode="document",
        solution_doc_text="The SDD text.",
    )
    state["retrieved_context"] = {"manuals": [], "codebase": [], "entities": []}
    state.update(overrides)
    return state


def test_generate_node_does_not_build_fallback_client_when_model_is_ollama(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", lambda _: _real_sleep(0))
    assert settings.ASSESSMENT_MODEL == "ollama"  # untouched default

    monkeypatch.setattr(generate, "_get_llm", lambda: _FakeChat(['[{"epic_title": "E", "user_story": "U", '
                                                                  '"acceptance_criteria": [], "dev_tasks": [], '
                                                                  '"unit_test_tasks": []}]']))

    def fail_if_called():
        raise AssertionError("fallback client must not be built when ASSESSMENT_MODEL is ollama")

    monkeypatch.setattr(generate, "_get_ollama_fallback_llm", fail_if_called)

    result = asyncio.run(generate.generate_node(_new_state()))

    assert result["status"] in ("reviewing", "creating")
    assert result["generated_stories"][0]["epic_title"] == "E"


def test_generate_node_falls_back_to_ollama_when_claude_fails(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", lambda _: _real_sleep(0))
    original = settings.ASSESSMENT_MODEL
    try:
        settings.apply_updates({"ASSESSMENT_MODEL": "claude"})

        claude = _FakeChat([RuntimeError("rate limited")] * 3)
        ollama_fallback = _FakeChat(['[{"epic_title": "Fallback Epic", "user_story": "U", '
                                     '"acceptance_criteria": [], "dev_tasks": [], "unit_test_tasks": []}]'])
        monkeypatch.setattr(generate, "_get_llm", lambda: claude)
        monkeypatch.setattr(generate, "_get_ollama_fallback_llm", lambda: ollama_fallback)

        result = asyncio.run(generate.generate_node(_new_state()))

        assert claude.ainvoke_calls == 3
        assert ollama_fallback.ainvoke_calls == 1
        assert result["generated_stories"][0]["epic_title"] == "Fallback Epic"
        assert result["status"] in ("reviewing", "creating")
    finally:
        settings.apply_updates({"ASSESSMENT_MODEL": original})


def test_generate_node_errors_when_claude_and_fallback_both_fail(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", lambda _: _real_sleep(0))
    original = settings.ASSESSMENT_MODEL
    try:
        settings.apply_updates({"ASSESSMENT_MODEL": "claude"})

        claude = _FakeChat([RuntimeError("rate limited")] * 3)
        ollama_fallback = _FakeChat([RuntimeError("ollama also down")] * 3)
        monkeypatch.setattr(generate, "_get_llm", lambda: claude)
        monkeypatch.setattr(generate, "_get_ollama_fallback_llm", lambda: ollama_fallback)

        result = asyncio.run(generate.generate_node(_new_state()))

        assert result["status"] == "error"
        assert any("generate_node" in e for e in result["errors"])
    finally:
        settings.apply_updates({"ASSESSMENT_MODEL": original})


def test_clarify_node_does_not_build_fallback_client_when_model_is_ollama(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", lambda _: _real_sleep(0))
    assert settings.ASSESSMENT_MODEL == "ollama"

    monkeypatch.setattr(clarify, "_get_llm", lambda: _FakeChat(['{"ambiguities": []}']))

    def fail_if_called():
        raise AssertionError("fallback client must not be built when ASSESSMENT_MODEL is ollama")

    monkeypatch.setattr(clarify, "_get_ollama_fallback_llm", fail_if_called)

    result = asyncio.run(clarify.clarify_node(_new_state()))

    assert result["clarification_needed"] is False


def test_clarify_node_falls_back_to_ollama_when_claude_fails(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", lambda _: _real_sleep(0))
    original = settings.ASSESSMENT_MODEL
    try:
        settings.apply_updates({"ASSESSMENT_MODEL": "claude"})

        claude = _FakeChat([RuntimeError("rate limited")] * 3)
        ollama_fallback = _FakeChat(['{"ambiguities": ["What is X?"]}'])
        monkeypatch.setattr(clarify, "_get_llm", lambda: claude)
        monkeypatch.setattr(clarify, "_get_ollama_fallback_llm", lambda: ollama_fallback)

        result = asyncio.run(clarify.clarify_node(_new_state()))

        assert claude.ainvoke_calls == 3
        assert ollama_fallback.ainvoke_calls == 1
        assert result["clarification_needed"] is True
        assert result["clarification_questions"] == ["What is X?"]
    finally:
        settings.apply_updates({"ASSESSMENT_MODEL": original})
