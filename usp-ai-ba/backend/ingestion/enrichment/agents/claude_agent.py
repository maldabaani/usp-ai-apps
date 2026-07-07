"""Claude-backed LogicExtractionAgent.

Moved verbatim from codemind/agents/claude_agent.py (itself ported from
com.jslogicextractor.agent.ClaudeLogicExtractionAgent) as part of unifying
CodeMind's per-file LLM extraction into the ChromaDB ingestion pipeline --
see plan file section I. Rebuilds its ChatAnthropic client only when
settings.settings_generation has advanced (the same generation-counter
pattern pipeline/nodes/generate.py's _get_llm() and clarify.py use) -- so a
settings-screen change to the Anthropic key/model takes effect on the next
extraction without a restart. Single attempt per file, catching all
exceptions into a failure_result (no internal retry loop).

``build_messages`` defaults to the code-oriented build_extraction_messages
but is overridable (see plan file section Q) -- ingestion/enrichment/
enrich_documents.py passes doc_prompts.build_extraction_messages instead, so
the same agent class can summarize manuals with a document-appropriate
prompt instead of silently reusing the code prompt.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from ingestion.enrichment.agents.base import ExtractionResult, failure_result, success_result
from ingestion.enrichment.models import SourceFile
from ingestion.enrichment.prompts import build_extraction_messages
from config import settings

logger = logging.getLogger(__name__)

NAME = "claude-logic-extractor"


class ClaudeLogicExtractionAgent:
    def __init__(self, build_messages: Callable[[SourceFile], tuple[str, str]] = build_extraction_messages) -> None:
        self._chat: ChatAnthropic | None = None
        self._built_at_generation = -1
        self._build_messages = build_messages
        self._rebuild_if_needed()

    def name(self) -> str:
        return NAME

    async def extract(self, file: SourceFile) -> ExtractionResult:
        self._rebuild_if_needed()
        start = time.monotonic()
        try:
            system_message, user_message = self._build_messages(file)
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
        self._chat = ChatAnthropic(
            model=settings.CLAUDE_MODEL,
            api_key=settings.ANTHROPIC_API_KEY,
            timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS,
        )
        self._built_at_generation = settings.settings_generation
