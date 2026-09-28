from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.graph.state import Design
from app.llm.models_config import Role
from app.llm.structured import (
    StructuredOutputError,
    extract_json,
    generate_structured,
    strip_code_fences,
)
from tests.fakes import Brain, Call, final, gateway

MESSAGES = [SystemMessage(content="# Role: Planner\nx"), HumanMessage(content="go")]


class Item(BaseModel):
    name: str
    qty: int = Field(ge=1)


def scripted(*replies: str) -> tuple[Brain, list[Call]]:
    queue = list(replies)
    brain = Brain(responders={"planner": lambda c: final(queue.pop(0))})
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
