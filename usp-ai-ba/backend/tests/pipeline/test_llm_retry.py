"""Covers pipeline/nodes/llm_retry.py: retry-with-seed-bump (skipped when the
caller says the model doesn't support it, e.g. Claude), and the Claude-fails
-> Ollama-fallback orchestration. Hand-mocked fake chat clients, matching
this codebase's established convention (see tests/test_ask_router.py's
_FakeChat) -- no real ChatOllama/ChatAnthropic construction needed here.
"""
from __future__ import annotations

import asyncio

import pytest

from pipeline.nodes.llm_retry import invoke_and_parse_with_fallback, invoke_and_parse_with_retry

_real_sleep = asyncio.sleep  # captured before any test monkeypatches asyncio.sleep


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChat:
    """Returns canned responses (or raises canned exceptions) in order, one
    per call to ainvoke(). Records every bind() call so tests can assert on
    whether/how the seed was bumped between attempts."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.ainvoke_calls = 0
        self.bind_calls: list[dict] = []

    def bind(self, **kwargs):
        self.bind_calls.append(kwargs)
        return self

    async def ainvoke(self, messages):
        self.ainvoke_calls += 1
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return _FakeResponse(response)


def _parse(raw_text: str) -> str:
    return raw_text


def _extract_text(content) -> str:
    return content


def test_succeeds_on_first_attempt_no_retry(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", lambda _: _real_sleep(0))
    chat = _FakeChat(["result"])

    result = asyncio.run(
        invoke_and_parse_with_retry(chat, [], _parse, _extract_text, base_seed=42, node_name="test_node")
    )

    assert result == "result"
    assert chat.ainvoke_calls == 1
    assert chat.bind_calls == []


def test_retries_with_bumped_seed_when_supports_seed(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", lambda _: _real_sleep(0))
    chat = _FakeChat([RuntimeError("boom"), "result"])

    result = asyncio.run(
        invoke_and_parse_with_retry(
            chat, [], _parse, _extract_text, base_seed=42, node_name="test_node", supports_seed=True
        )
    )

    assert result == "result"
    assert chat.ainvoke_calls == 2
    assert chat.bind_calls == [{"seed": 43}]  # base_seed + attempt(2) - 1


def test_retries_without_rebinding_seed_when_supports_seed_is_false(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", lambda _: _real_sleep(0))
    chat = _FakeChat([RuntimeError("boom"), RuntimeError("boom again"), "result"])

    result = asyncio.run(
        invoke_and_parse_with_retry(
            chat, [], _parse, _extract_text, base_seed=42, node_name="test_node", supports_seed=False
        )
    )

    assert result == "result"
    assert chat.ainvoke_calls == 3
    # Never bound a seed, even across multiple retries -- Claude has no seed param.
    assert chat.bind_calls == []


def test_raises_last_exception_after_all_attempts_fail(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", lambda _: _real_sleep(0))
    chat = _FakeChat([RuntimeError("first"), RuntimeError("second"), RuntimeError("third")])

    with pytest.raises(RuntimeError, match="third"):
        asyncio.run(invoke_and_parse_with_retry(chat, [], _parse, _extract_text, base_seed=42, node_name="test_node"))

    assert chat.ainvoke_calls == 3


def test_fallback_not_invoked_when_primary_succeeds(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", lambda _: _real_sleep(0))
    primary = _FakeChat(["primary result"])
    fallback = _FakeChat(["fallback result"])

    result = asyncio.run(
        invoke_and_parse_with_fallback(
            primary, fallback, [], _parse, _extract_text, base_seed=42, node_name="test_node"
        )
    )

    assert result == "primary result"
    assert fallback.ainvoke_calls == 0


def test_fallback_used_after_primary_exhausts_all_attempts(monkeypatch, caplog):
    monkeypatch.setattr(asyncio, "sleep", lambda _: _real_sleep(0))
    primary = _FakeChat([RuntimeError("claude down")] * 3)
    fallback = _FakeChat(["fallback result"])

    with caplog.at_level("WARNING"):
        result = asyncio.run(
            invoke_and_parse_with_fallback(
                primary,
                fallback,
                [],
                _parse,
                _extract_text,
                base_seed=42,
                node_name="test_node",
                supports_seed=False,
            )
        )

    assert result == "fallback result"
    assert primary.ainvoke_calls == 3  # primary got its own full MAX_ATTEMPTS cycle
    assert fallback.ainvoke_calls == 1
    assert any("falling back to local Ollama" in message for message in caplog.messages)
    assert any("test_node" in message for message in caplog.messages)


def test_fallback_gets_its_own_full_retry_cycle_with_seed_support(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", lambda _: _real_sleep(0))
    primary = _FakeChat([RuntimeError("claude down")] * 3)
    fallback = _FakeChat([RuntimeError("ollama hiccup"), "fallback result"])

    result = asyncio.run(
        invoke_and_parse_with_fallback(
            primary, fallback, [], _parse, _extract_text, base_seed=42, node_name="test_node", supports_seed=False
        )
    )

    assert result == "fallback result"
    assert fallback.ainvoke_calls == 2
    # Fallback is always Ollama, so its own retry cycle bumps the seed.
    assert fallback.bind_calls == [{"seed": 43}]


def test_no_fallback_provided_raises_primary_failure(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", lambda _: _real_sleep(0))
    primary = _FakeChat([RuntimeError("ollama down")] * 3)

    with pytest.raises(RuntimeError, match="ollama down"):
        asyncio.run(
            invoke_and_parse_with_fallback(
                primary, None, [], _parse, _extract_text, base_seed=42, node_name="test_node"
            )
        )


def test_both_primary_and_fallback_exhausted_raises_fallback_failure(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", lambda _: _real_sleep(0))
    primary = _FakeChat([RuntimeError("claude down")] * 3)
    fallback = _FakeChat([RuntimeError("ollama also down")] * 3)

    with pytest.raises(RuntimeError, match="ollama also down"):
        asyncio.run(
            invoke_and_parse_with_fallback(
                primary, fallback, [], _parse, _extract_text, base_seed=42, node_name="test_node", supports_seed=False
            )
        )
