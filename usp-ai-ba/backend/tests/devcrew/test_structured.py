from __future__ import annotations

import json
import logging

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from devcrew.graph.state import Design
from devcrew.llm.models_config import Role
from devcrew.llm.structured import (
    StructuredOutputError,
    extract_json,
    generate_structured,
    strip_code_fences,
)
from tests.devcrew.fakes import Brain, Call, final, gateway, truncated

MESSAGES = [SystemMessage(content="# Role: Planner\nx"), HumanMessage(content="go")]


class Item(BaseModel):
    name: str
    qty: int = Field(ge=1)


def scripted(*replies: str) -> tuple[Brain, list[Call]]:
    queue = list(replies)
    brain = Brain(responders={"planner": lambda c: final(queue.pop(0))})
    return brain, brain.calls


def scripted_replies(*replies: AIMessage) -> tuple[Brain, list[Call]]:
    queue = list(replies)
    brain = Brain(responders={"planner": lambda c: queue.pop(0)})
    return brain, brain.calls


async def test_valid_first_attempt_uses_schema_format() -> None:
    brain, calls = scripted('{"name": "a", "qty": 2}')
    result = await generate_structured(gateway(brain), Role.PLANNER, MESSAGES, Item)
    assert result.value == Item(name="a", qty=2)
    assert (result.attempts, result.used_fallback) == (1, False)
    assert calls[0].kwargs["format"]["properties"]["qty"]["minimum"] == 1


async def test_retry_feeds_back_validation_error() -> None:
    brain, calls = scripted('{"name": "a", "qty": 0}', '{"name": "a", "qty": 3}')
    result = await generate_structured(gateway(brain), Role.PLANNER, MESSAGES, Item)
    assert result.value.qty == 3 and result.attempts == 2
    feedback = str(calls[1].messages[-1].content)
    assert "qty" in feedback and "greater than or equal to 1" in feedback
    assert isinstance(calls[1].messages[-2], AIMessage)  # the bad answer is shown back


async def test_invalid_json_error_is_reported() -> None:
    brain, calls = scripted('{"name": "a", qty: 2}', '{"name": "a", "qty": 2}')
    await generate_structured(gateway(brain), Role.PLANNER, MESSAGES, Item)
    assert "Invalid JSON" in str(calls[1].messages[-1].content)


async def test_transport_failure_is_reported_as_transient_and_stops_immediately() -> None:
    # A timeout/dropped connection isn't a malformed-answer problem retrying the SAME request
    # would fix -- it would most likely just wait out the same timeout again. Unlike a truncated
    # or invalid reply, this must not trigger generate_structured's own in-conversation retry.
    def planner(call: Call) -> AIMessage:
        raise ConnectionError("ollama is down")

    brain = Brain(responders={"planner": planner})
    with pytest.raises(StructuredOutputError) as exc_info:
        await generate_structured(gateway(brain), Role.PLANNER, MESSAGES, Item)
    exc = exc_info.value
    assert exc.transient == [True]
    assert exc.truncated == [False]
    assert "ConnectionError" in str(exc) and "ollama is down" in str(exc)
    assert len(brain.calls) == 1


async def test_first_response_counts_as_attempt() -> None:
    brain, calls = scripted('{"name": "b", "qty": 1}')
    result = await generate_structured(
        gateway(brain), Role.PLANNER, MESSAGES, Item, first_response="not json"
    )
    assert result.value.name == "b" and result.attempts == 2 and len(calls) == 1


async def test_fallback_extracts_json_from_prose() -> None:
    prose = 'Sure! Here it is:\n```json\n{"name": "c", "qty": 4,}\n```\nHope it helps.'
    # Strict parse fails on every attempt (prose + trailing comma); extraction succeeds.
    brain, _ = scripted("nope", "still nope", prose)
    result = await generate_structured(gateway(brain), Role.PLANNER, MESSAGES, Item)
    assert result.value == Item(name="c", qty=4)
    assert result.used_fallback and result.attempts == 3


async def test_invalid_response_logs_raw_text(caplog: pytest.LogCaptureFixture) -> None:
    brain, _ = scripted("", '{"name": "a", "qty": 1}')
    with caplog.at_level(logging.INFO, logger="devcrew.llm.structured"):
        await generate_structured(gateway(brain), Role.PLANNER, MESSAGES, Item)
    assert "Expecting value" in caplog.text
    assert "raw=''" in caplog.text


async def test_truncated_response_is_not_accepted_as_valid(caplog: pytest.LogCaptureFixture) -> None:
    # A truncated reply that happens to look like a parseable fragment must still be rejected --
    # the whole point is that a genuinely incomplete answer never passes as valid.
    brain, calls = scripted_replies(
        truncated('{"name": "a", "qty": 1'), final('{"name": "a", "qty": 2}')
    )
    with caplog.at_level(logging.INFO, logger="devcrew.llm.structured"):
        result = await generate_structured(gateway(brain), Role.PLANNER, MESSAGES, Item)
    assert result.value == Item(name="a", qty=2) and result.attempts == 2
    assert "truncated by output token limit" in caplog.text
    # The retry asks for brevity, not the generic "your previous answer was not valid" feedback.
    feedback = str(calls[1].messages[-1].content)
    assert "cut off before it finished" in feedback and "short" in feedback


async def test_gives_up_after_retries_when_every_attempt_is_truncated() -> None:
    brain, _ = scripted_replies(
        truncated('{"a'), truncated('{"name'), truncated('{"name": "x"')
    )
    with pytest.raises(StructuredOutputError) as info:
        await generate_structured(gateway(brain), Role.PLANNER, MESSAGES, Item, max_retries=2)
    assert info.value.truncated == [True, True, True]


async def test_gives_up_after_retries() -> None:
    brain, calls = scripted("a", "b", "c")
    with pytest.raises(StructuredOutputError) as info:
        await generate_structured(gateway(brain), Role.PLANNER, MESSAGES, Item, max_retries=2)
    assert len(calls) == 3 and len(info.value.raw) == 3


async def test_json_mode_and_none_mode() -> None:
    for mode, expected in (("json", "json"), ("none", None)):
        brain, calls = scripted('{"name": "a", "qty": 1}')
        gw = gateway(brain, {"defaults": {"model": "m", "structured_format": mode}})
        await generate_structured(gw, Role.PLANNER, MESSAGES, Item)
        assert calls[0].kwargs.get("format") == expected


def test_strip_code_fences() -> None:
    assert strip_code_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_code_fences('  {"a": 1} ') == '{"a": 1}'


def test_strip_code_fences_removes_leading_invisible_characters() -> None:
    # A zero-width space here would otherwise make json.loads fail with "Expecting value at
    # line 1 column 1" on text that renders as complete, valid JSON everywhere it's displayed.
    assert strip_code_fences('​{"a": 1}') == '{"a": 1}'
    assert strip_code_fences('﻿{"a": 1}​') == '{"a": 1}'
    assert strip_code_fences('```json\n​{"a": 1}\n```') == '{"a": 1}'


def test_extract_unwraps_single_key_wrapper() -> None:
    assert extract_json('{"item": {"name": "x", "qty": 1}}', Item) == Item(name="x", qty=1)


def test_extract_handles_braces_inside_strings() -> None:
    text = 'noise {"name": "a}b{", "qty": 2} trailing {'
    assert extract_json(text, Item) == Item(name="a}b{", qty=2)


def test_extract_returns_none_when_nothing_valid() -> None:
    assert extract_json('{"name": 1}', Item) is None


def test_validation_context_reaches_model_validators() -> None:
    design = {
        "stack": "python",
        "template_id": "nope",
        "project_structure": [],
        "design_doc": "d",
        "modules": [{"name": "m", "path": "p", "responsibility": "r", "interface": "i"}],
    }
    assert extract_json(json.dumps(design), Design) is not None
    assert extract_json(json.dumps(design), Design, {"templates": {"py": "python"}}) is None
