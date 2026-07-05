"""Ollama-backed LogicExtractionAgent.

Ported from com.jslogicextractor.agent.OllamaLogicExtractionAgent. Single
attempt per file, catching all exceptions into a failure_result (no internal
retry loop -- unlike pipeline/nodes/llm_retry.py's multi-attempt seed-bumping
retry, which is specific to generate.py/clarify.py's story-generation use
case, not per-file extraction).

Rebuilds its ChatOllama client only when settings.settings_generation has
advanced (the same generation-counter pattern claude_agent.py and
pipeline/nodes/generate.py's _get_llm() use) and reads num_ctx from
settings.OLLAMA_NUM_CTX rather than a value hardcoded here -- StoryForge's
generate_node/clarify_node and this agent hit the same physical Ollama server
and the same model, so requesting two different num_ctx values from the same
model forces Ollama to repeatedly reload it (expensive on slow hardware) and
whichever call happened last "wins" the loaded context size, silently
re-truncating the other side's prompts.
"""
from __future__ import annotations

import logging
import time

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

from codemind.agents.base import ExtractionResult, failure_result, success_result
from codemind.models import SourceFile
from codemind.prompts import build_extraction_messages
from config import settings

logger = logging.getLogger(__name__)

NAME = "ollama-logic-extractor"

REQUEST_TIMEOUT_SECONDS = 120


class OllamaLogicExtractionAgent:
    def __init__(self) -> None:
        self._chat: ChatOllama | None = None
        self._built_at_generation = -1
        self._rebuild_if_needed()

    def name(self) -> str:
        return NAME

    async def extract(self, file: SourceFile) -> ExtractionResult:
        self._rebuild_if_needed()
        start = time.monotonic()
        try:
            system_message, user_message = build_extraction_messages(file)
            response = await self._chat.ainvoke(
                [SystemMessage(content=system_message), HumanMessage(content=user_message)]
            )
            duration_millis = int((time.monotonic() - start) * 1000)
            usage = response.usage_metadata
            prompt_tokens = usage.get("input_tokens") if usage else None
            completion_tokens = usage.get("output_tokens") if usage else None
            return success_result(file, NAME, response.content, duration_millis, prompt_tokens, completion_tokens)
        except Exception as exc:  # noqa: BLE001 - per-file isolation, matches Java's catch-all
            duration_millis = int((time.monotonic() - start) * 1000)
            logger.warning("Extraction failed for %s: %s", file.relative_path, exc)
            return failure_result(file, NAME, str(exc), duration_millis)

    def _rebuild_if_needed(self) -> None:
        if self._chat is not None and self._built_at_generation == settings.settings_generation:
            return
        self._chat = ChatOllama(
            model=settings.CODEMIND_OLLAMA_MODEL,
            base_url=settings.OLLAMA_BASE_URL,
            num_ctx=settings.OLLAMA_NUM_CTX,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        self._built_at_generation = settings.settings_generation
