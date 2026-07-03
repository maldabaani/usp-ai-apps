"""Ollama-backed LogicExtractionAgent.

Ported from com.jslogicextractor.agent.OllamaLogicExtractionAgent. Single
attempt per file, catching all exceptions into a failure_result (no internal
retry loop -- unlike pipeline/nodes/llm_retry.py's multi-attempt seed-bumping
retry, which is specific to generate.py/clarify.py's story-generation use
case, not per-file extraction).
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

NUM_CTX = 8192
REQUEST_TIMEOUT_SECONDS = 120


class OllamaLogicExtractionAgent:
    def __init__(self) -> None:
        self._chat = ChatOllama(
            model=settings.CODEMIND_OLLAMA_MODEL,
            base_url=settings.OLLAMA_BASE_URL,
            num_ctx=NUM_CTX,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

    def name(self) -> str:
        return NAME

    async def extract(self, file: SourceFile) -> ExtractionResult:
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
