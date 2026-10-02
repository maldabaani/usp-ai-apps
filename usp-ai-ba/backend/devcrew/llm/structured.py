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

from devcrew.llm.client import LLMGateway
from devcrew.llm.models_config import Role

logger = logging.getLogger(__name__)

MAX_ERRORS_IN_FEEDBACK = 15
_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*\n?(.*?)```", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
# Zero-width/invisible characters a model occasionally leaks at the start or end of an
# otherwise-valid reply (stray special-token artifacts). None of these are whitespace per
# str.isspace(), so plain .strip() leaves them in place -- and because they render as nothing,
# the resulting text looks like complete, valid JSON everywhere it's displayed (logs, the
# Prompts tab) while json.loads still fails with "Expecting value at line 1 column 1", making
# the failure look like a logging bug rather than the parse bug it actually is.
_INVISIBLE_EDGE_RE = re.compile(r"^[﻿​‌‍⁠\s]+|[﻿​‌‍⁠\s]+$")


def _strip_invisible(text: str) -> str:
    return _INVISIBLE_EDGE_RE.sub("", text)


class StructuredOutputError(RuntimeError):
    def __init__(
        self,
        schema: str,
        errors: list[str],
        raw: list[str],
        truncated: list[bool] | None = None,
        transient: list[bool] | None = None,
    ) -> None:
        self.schema = schema
        self.errors = errors
        self.raw = raw
        # Parallel to `raw`: True where that attempt's reply was cut off by the model's own
        # output-token cap (see _is_truncated) rather than genuinely invalid. Defaults to all
        # False so existing callers that don't pass it (or old pickled state, if any) still work.
        self.truncated = truncated if truncated is not None else [False] * len(raw)
        # Parallel to `raw`: True where that attempt never got a reply at all -- the call itself
        # failed (timeout, dropped connection, Ollama restarting mid-request), not a malformed or
        # truncated answer. Same "worth another fresh attempt" signature as all-empty/all-truncated
        # for callers like coordinator.py's decide(), but distinct: retrying it immediately in the
        # SAME conversation (generate_structured's own in-conversation retry) would most likely
        # just wait out the same timeout again, so this always stops the attempt loop right away.
        self.transient = transient if transient is not None else [False] * len(raw)
        last = errors[-1] if errors else "no output"
        super().__init__(f"Could not obtain valid {schema} after {len(raw)} attempts: {last[:500]}")


@dataclass(frozen=True)
class StructuredResult[T: BaseModel]:
    value: T
    attempts: int
    used_fallback: bool


def strip_code_fences(text: str) -> str:
    text = _strip_invisible(text)
    match = _FENCE_RE.search(text)
    return _strip_invisible(match.group(1)) if match else text


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


def _is_truncated(response: AIMessage) -> bool:
    """True when the model's own completion signal says the reply was cut off by the output-
    token cap (Ollama: response_metadata["done_reason"] == "length") rather than genuinely
    invalid -- same check and same reasoning as pipeline/nodes/llm_retry.py's _is_truncated,
    duck-typed on response_metadata so it stays provider-agnostic. Distinguishing this from a
    generic JSON parse failure matters here: the log line for a plain parse failure truncates
    the raw text to 200 chars for readability, which on its own is visually indistinguishable
    from a genuinely truncated response -- only this metadata flag tells them apart for certain.
    """
    metadata = response.response_metadata or {}
    return metadata.get("done_reason") == "length" or metadata.get("stop_reason") == "max_tokens"


def _correction(error: str, schema: type[BaseModel]) -> HumanMessage:
    return HumanMessage(
        content=(
            f"Your previous answer was not a valid {schema.__name__}.\n"
            f"Errors:\n{error}\n\n"
            "Reply again with ONLY the corrected JSON object (no prose, no code fences). "
            "Keep everything that was correct."
        )
    )


def _truncation_correction(schema: type[BaseModel]) -> HumanMessage:
    return HumanMessage(
        content=(
            f"Your previous answer was cut off before it finished -- it exceeded the output "
            f"length limit, so it is not a complete {schema.__name__}.\n"
            "Reply again with ONLY the corrected JSON object (no prose, no code fences), but "
            "keep every text field (reason, guidance, description, etc.) short -- one or two "
            "sentences each. A complete, concise answer is required; a longer but truncated one "
            "is useless."
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
    truncated: list[bool] = []
    transient: list[bool] = []
    errors: list[str] = []
    output_format = _output_format(gateway, role, schema)

    for attempt in range(1, max_retries + 2):
        # first_response (a tool-calling agent's final answer) carries no response_metadata,
        # so truncation can't be checked for it -- treated as not truncated (no signal either way).
        cut_off = False
        if attempt == 1 and first_response is not None:
            text = first_response
        else:
            try:
                reply = await gateway.ainvoke(role, history, output_format=output_format)
            except Exception as exc:
                # The call itself failed (timeout, dropped connection, Ollama restarting
                # mid-request) -- not a malformed or truncated answer retrying the SAME request
                # would fix, since that would most likely just wait out the same timeout again.
                # Stop here and let the caller's own recovery (coordinator.py's decide() outer
                # retry, or routing to to_coordinator()) decide whether to try again.
                raw.append("")
                truncated.append(False)
                transient.append(True)
                errors.append(f"LLM call failed: {type(exc).__name__}: {exc}")
                raise StructuredOutputError(schema.__name__, errors, raw, truncated, transient) from exc
            text = reply.text
            cut_off = _is_truncated(reply)
        raw.append(text)
        truncated.append(cut_off)
        transient.append(False)
        if cut_off:
            error = (
                "Response was truncated by the output token limit before it finished "
                "(done_reason=length) -- this is a genuinely incomplete answer, not a parse "
                "failure to paper over."
            )
            errors.append(error)
            logger.info(
                "%s: %s truncated by output token limit (attempt %d) [raw=%r]",
                role,
                schema.__name__,
                attempt,
                text[:200],
            )
            history += [AIMessage(content=text), _truncation_correction(schema)]
            continue
        try:
            return StructuredResult(parse_strict(text, schema, context), attempt, False)
        except (json.JSONDecodeError, ValidationError) as exc:
            # A reply that's valid JSON preceded by a leading prose sentence ("Confirmed no
            # changes...\n\n{...}") fails parse_strict() outright -- it isn't malformed, just not
            # the very first character -- even though extract_json()'s balanced-brace scan would
            # recover it in a heartbeat. Recovering here avoids burning a whole extra model round
            # trip (and the correction message in history) on the single most common shape of
            # "technically broke the no-prose instruction" reply, from a tool-calling agent's own
            # natural wrap-up sentence in particular (first_response callers especially -- that
            # text was never actually re-prompted with the strict JSON-only instruction).
            if isinstance(exc, json.JSONDecodeError):
                recovered = extract_json(text, schema, context)
                if recovered is not None:
                    logger.info(
                        "%s: %s had a leading prose preamble (attempt %d) but was recovered by "
                        "extraction, not re-prompted [raw=%r]",
                        role,
                        schema.__name__,
                        attempt,
                        text[:200],
                    )
                    return StructuredResult(recovered, attempt, True)
            error = format_validation_error(exc)
            errors.append(error)
            logger.info(
                "%s: invalid %s (attempt %d): %s [raw=%r]",
                role,
                schema.__name__,
                attempt,
                error,
                text[:200],
            )
            history += [AIMessage(content=text), _correction(error, schema)]

    for text in reversed(raw):
        value = extract_json(text, schema, context)
        if value is not None:
            return StructuredResult(value, len(raw), True)
    raise StructuredOutputError(schema.__name__, errors, raw, truncated, transient)
