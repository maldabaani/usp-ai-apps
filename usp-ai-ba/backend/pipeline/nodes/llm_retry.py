"""Shared resilience helper for the clarify/generate LLM call sites.

Both nodes run their model at temperature=0. When the model supports it
(Ollama), a fixed seed also makes the same SDD produce the same
clarifications/stories on every run; a retry that resent the exact same
request would therefore just fail identically, so on failure this nudges the
seed (base_seed + attempt - 1) before retrying -- a different sample has a
chance to succeed, while the first attempt (the one that runs on every
normal, successful call) stays fully deterministic and reproducible across
separate runs. Anthropic's API has no seed parameter at all, so callers pass
`supports_seed=False` for a Claude client -- later attempts then just retry
the same client unmodified (temperature=0 is the only determinism knob
Claude offers). This is an explicit caller-supplied flag rather than an
isinstance(llm, ChatOllama) check so this module stays decoupled from any
specific LangChain client class, and so tests can exercise both branches
with hand-mocked fake clients instead of constructing a real ChatOllama.

Also treats a response cut off by the output-token cap as a failure (see
_is_truncated below) rather than letting it through to `parse` -- json_repair
(used by generate_node/clarify_node's JSON parsing) is deliberately lenient
about malformed JSON, which means a genuinely truncated array/object (e.g. a
multi-epic story list cut off mid-way through the second epic) can still
"successfully" parse into valid-looking JSON that's silently missing
everything after the cutoff. Catching this here, before parsing, turns that
into a retry (a different sample, same token budget, may finish in time) or
-- if every attempt still gets cut off -- a genuine surfaced error instead of
a quietly incomplete result.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage

logger = logging.getLogger(__name__)

T = TypeVar("T")

MAX_ATTEMPTS = 3
BASE_DELAY_SECONDS = 1.0


def _is_truncated(response) -> bool:
    """True when the model's own completion signal says the response was cut
    off by the output-token cap (Ollama: response_metadata["done_reason"] ==
    "length"; Claude: response_metadata["stop_reason"] == "max_tokens") --
    checked before parsing so a truncated JSON array/object (which
    json_repair's deliberately lenient repair can turn into syntactically
    valid but silently INCOMPLETE JSON, e.g. dropping every story after the
    one being written when the cap hit) is never accepted as a genuine
    result. Duck-typed on response_metadata's keys rather than an
    isinstance check, matching this module's existing provider-agnostic
    design (see module docstring)."""
    metadata = getattr(response, "response_metadata", None) or {}
    return metadata.get("done_reason") == "length" or metadata.get("stop_reason") == "max_tokens"


async def invoke_and_parse_with_retry(
    llm: BaseChatModel,
    messages: list[BaseMessage],
    parse: Callable[[str], T],
    extract_text: Callable[[object], str],
    *,
    base_seed: int,
    node_name: str,
    supports_seed: bool = True,
) -> T:
    """Invoke `llm`, extract its text, and parse it -- retrying on any failure.

    Raises the last exception if every attempt fails, so callers keep their
    existing fail-open / fail-error handling unchanged.
    """
    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if attempt == 1 or not supports_seed:
            call_llm = llm
        else:
            call_llm = llm.model_copy(update={"seed": base_seed + attempt - 1})
        try:
            response = await call_llm.ainvoke(messages)
            if _is_truncated(response):
                raise RuntimeError(
                    f"{node_name}: LLM response was truncated by the output token "
                    "limit before it finished -- raw output is genuinely "
                    "incomplete (e.g. missing epics/stories cut off mid-JSON), "
                    "not a parse failure to paper over"
                )
            raw_text = extract_text(response.content)
            return parse(raw_text)
        except Exception as exc:  # noqa: BLE001 - retried here, re-raised after final attempt
            last_exc = exc
            logger.warning(
                "%s: attempt %d/%d failed: %s", node_name, attempt, MAX_ATTEMPTS, exc
            )
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(BASE_DELAY_SECONDS * attempt)

    assert last_exc is not None
    raise last_exc


async def invoke_and_parse_with_fallback(
    llm: BaseChatModel,
    fallback_llm: BaseChatModel | None,
    messages: list[BaseMessage],
    parse: Callable[[str], T],
    extract_text: Callable[[object], str],
    *,
    base_seed: int,
    node_name: str,
    supports_seed: bool = True,
) -> T:
    """Runs invoke_and_parse_with_retry against `llm` (its own full
    MAX_ATTEMPTS-attempt cycle); if that's completely exhausted and a
    `fallback_llm` was provided (settings.ASSESSMENT_MODEL == "claude"; see
    pipeline/nodes/generate.py and clarify.py), retries the same messages
    against it too, getting its own full attempt cycle. `fallback_llm` is
    always Ollama by construction (see assessment_llm.build_ollama_llm), so
    its own retry cycle always runs with supports_seed=True regardless of
    what the primary call used. Logged at WARNING so a Claude outage
    silently degrading to local Ollama is still visible, not a silent
    behavior change. Raises the fallback's failure (or the primary's, if no
    fallback_llm was given) if that also fails."""
    try:
        return await invoke_and_parse_with_retry(
            llm, messages, parse, extract_text, base_seed=base_seed, node_name=node_name, supports_seed=supports_seed
        )
    except Exception as primary_exc:  # noqa: BLE001 - re-raised below if no fallback applies
        if fallback_llm is None:
            raise
        logger.warning(
            "%s: Claude failed after %d attempts (%s), falling back to local Ollama",
            node_name,
            MAX_ATTEMPTS,
            primary_exc,
        )
        return await invoke_and_parse_with_retry(
            fallback_llm,
            messages,
            parse,
            extract_text,
            base_seed=base_seed,
            node_name=f"{node_name} (ollama fallback)",
        )
