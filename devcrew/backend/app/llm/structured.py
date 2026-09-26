"""Validated structured output for weak local models.

Strategy (per call):
1. Ask for JSON (Ollama JSON-schema constrained decoding by default) and validate with Pydantic.
2. On failure, retry up to `max_retries` times, feeding the validation error back to the model.
3. If every attempt failed, fall back to extracting a JSON object from the raw texts
   (code fences, prose around the JSON, trailing commas, a single wrapper key).
4. Otherwise raise StructuredOutputError; callers route to the Coordinator.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel, ValidationError

from app.llm.client import LLMGateway
from app.llm.models_config import Role

logger = logging.getLogger(__name__)

MAX_ERRORS_IN_FEEDBACK = 15
_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*\n?(.*?)```", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


class StructuredOutputError(RuntimeError):
    def __init__(self, schema: str, errors: list[str], raw: list[str]) -> None:
        self.schema = schema
        self.errors = errors
        self.raw = raw
        last = errors[-1] if errors else "no output"
        super().__init__(f"Could not obtain valid {schema} after {len(raw)} attempts: {last[:500]}")


@dataclass(frozen=True)
class StructuredResult[T: BaseModel]:
    value: T
    attempts: int
    used_fallback: bool


def strip_code_fences(text: str) -> str:
    text = text.strip()
    match = _FENCE_RE.search(text)
    return match.group(1).strip() if match else text


def format_validation_error(exc: ValidationError | json.JSONDecodeError) -> str:
    if isinstance(exc, json.JSONDecodeError):
        return f"Invalid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}."
    lines = []
    for err in exc.errors()[:MAX_ERRORS_IN_FEEDBACK]:
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        lines.append(f"- {loc}: {err['msg']}")
    extra = exc.error_count() - MAX_ERRORS_IN_FEEDBACK
    if extra > 0:
        lines.append(f"- ... and {extra} more errors")
    return "\n".join(lines)


def _iter_json_objects(text: str) -> Iterator[str]:
    """Yield balanced {...} substrings, outermost first, respecting JSON strings."""
    for start in (i for i, ch in enumerate(text) if ch == "{"):
        depth = 0
        in_str = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    yield text[start : i + 1]
                    break


def _loads_lenient(candidate: str) -> Any:
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return json.loads(_TRAILING_COMMA_RE.sub(r"\1", candidate))


def _validate_candidates[T: BaseModel](
    obj: Any, schema: type[T], context: Mapping[str, Any] | None
) -> T:
    try:
        return schema.model_validate(obj, context=context)
    except ValidationError:
        # Models often wrap the answer: {"plan": {...}}.
        if isinstance(obj, dict) and len(obj) == 1:
            inner = next(iter(obj.values()))
            if isinstance(inner, dict):
                return schema.model_validate(inner, context=context)
        raise


def extract_json[T: BaseModel](
    text: str, schema: type[T], context: Mapping[str, Any] | None = None
) -> T | None:
    """Best-effort extraction of a valid `schema` instance from free text."""
    sources = [m.group(1) for m in _FENCE_RE.finditer(text)] + [text]
    for source in sources:
        for candidate in _iter_json_objects(source):
            try:
                return _validate_candidates(_loads_lenient(candidate), schema, context)
            except (json.JSONDecodeError, ValidationError):
                continue
    return None


def parse_strict[T: BaseModel](
    text: str, schema: type[T], context: Mapping[str, Any] | None = None
) -> T:
    return _validate_candidates(json.loads(strip_code_fences(text)), schema, context)


def schema_prompt(schema: type[BaseModel]) -> str:
    return json.dumps(schema.model_json_schema(), separators=(",", ":"))


def _correction(error: str, schema: type[BaseModel]) -> HumanMessage:
    return HumanMessage(
        content=(
            f"Your previous answer was not a valid {schema.__name__}.\n"
            f"Errors:\n{error}\n\n"
            "Reply again with ONLY the corrected JSON object (no prose, no code fences). "
            "Keep everything that was correct."
        )
    )


def _output_format(gateway: LLMGateway, role: Role, schema: type[BaseModel]) -> Any:
    mode = gateway.spec(role).structured_format
    if mode == "schema":
        return schema.model_json_schema()
    if mode == "json":
        return "json"
    return None


async def generate_structured[T: BaseModel](
    gateway: LLMGateway,
    role: Role,
    messages: Sequence[BaseMessage],
    schema: type[T],
    *,
    context: Mapping[str, Any] | None = None,
    max_retries: int = 2,
    first_response: str | None = None,
) -> StructuredResult[T]:
    """Obtain a validated `schema` instance from the model.

    `first_response` lets a tool-calling agent's final answer count as the first attempt.
    """
    history: list[BaseMessage] = list(messages)
    raw: list[str] = []
    errors: list[str] = []
    output_format = _output_format(gateway, role, schema)

    for attempt in range(1, max_retries + 2):
        if attempt == 1 and first_response is not None:
            text = first_response
        else:
            reply = await gateway.ainvoke(role, history, output_format=output_format)
            text = reply.text
        raw.append(text)
        try:
            return StructuredResult(parse_strict(text, schema, context), attempt, False)
        except (json.JSONDecodeError, ValidationError) as exc:
            error = format_validation_error(exc)
            errors.append(error)
            logger.info("%s: invalid %s (attempt %d): %s", role, schema.__name__, attempt, error)
            history += [AIMessage(content=text), _correction(error, schema)]

    for text in reversed(raw):
        value = extract_json(text, schema, context)
        if value is not None:
            return StructuredResult(value, len(raw), True)
    raise StructuredOutputError(schema.__name__, errors, raw)
