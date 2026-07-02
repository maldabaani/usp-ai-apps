"""Shared resilience helper for the clarify/generate LLM call sites.

Both nodes run their `ChatOllama` model at temperature=0 with a fixed seed so
the same SDD produces the same clarifications/stories on every run. A retry
that resent the exact same request would therefore just fail identically, so
on failure this nudges the seed (base_seed + attempt - 1) before retrying --
a different sample has a chance to succeed, while the first attempt (the one
that runs on every normal, successful call) stays fully deterministic and
reproducible across separate runs.
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


async def invoke_and_parse_with_retry(
    llm: BaseChatModel,
    messages: list[BaseMessage],
    parse: Callable[[str], T],
    extract_text: Callable[[object], str],
    *,
    base_seed: int,
    node_name: str,
) -> T:
    """Invoke `llm`, extract its text, and parse it -- retrying on any failure.

    Raises the last exception if every attempt fails, so callers keep their
    existing fail-open / fail-error handling unchanged.
    """
    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        call_llm = llm if attempt == 1 else llm.bind(seed=base_seed + attempt - 1)
        try:
            response = await call_llm.ainvoke(messages)
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
