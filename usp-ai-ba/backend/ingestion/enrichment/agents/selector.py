"""Builds the LLM-summary enrichment agent(s) for one ingestion run and
routes each file/part to them.

build_agents() picks a single PRIMARY agent based on settings.INGEST_LLM_MODEL
("ollama" default, or "claude"), returning it as agents[0]; when the primary
is Claude, Ollama is always appended as agents[1], an unconditional fallback
(bypassing INGEST_OLLAMA_ENABLED, same precedent as
pipeline/nodes/assessment_llm.build_ollama_llm's own "always Ollama
regardless" fallback) -- Claude has a real failure mode (exhausted API
credits, rate limits) Ollama doesn't, so a Claude-primary run always has
somewhere to fall back to. When the primary is Ollama, INGEST_OLLAMA_ENABLED
still gates whether it's actually available at all -- an empty list means
"no agents configured", handled by enrich.py's/enrich_documents.py's own
graceful skip.

This previously registered BOTH Claude and Ollama whenever both were
configured and round-robinned between them per file/part regardless of
health -- so a Claude account with exhausted credits still got retried on
every Nth file/part instead of a run-wide fallback (a real production
complaint). AgentRouter replaces that round-robin with a sticky
circuit-breaker: once the primary fails once, every remaining file/part in
*this run* goes straight to the fallback (agents[1], if present) with no
further primary attempts. This state lives only as long as one AgentRouter
instance, scoped to a single enrich_repository()/enrich_documents() call --
the next ingestion run starts fresh and retries the primary again.

``build_messages``, when given, is forwarded to both agent constructors in
place of their code-oriented default -- see plan file section Q. Used by
ingestion/enrichment/enrich_documents.py to point the same agent classes at
doc_prompts.build_extraction_messages instead of prompts.build_extraction_messages.
"""
from __future__ import annotations

import logging
from typing import Callable

from ingestion.enrichment.agents.base import ExtractionResult, LogicExtractionAgent
from ingestion.enrichment.agents.claude_agent import ClaudeLogicExtractionAgent
from ingestion.enrichment.agents.ollama_agent import OllamaLogicExtractionAgent
from ingestion.enrichment.models import SourceFile
from config import settings

logger = logging.getLogger(__name__)


class AgentRouter:
    """Wraps 1 or 2 agents as returned by build_agents(): agents[0] is always
    the primary, agents[1] (if present) is the sticky, run-scoped fallback.
    """

    def __init__(self, agents: list[LogicExtractionAgent]) -> None:
        if not agents:
            raise ValueError("No LogicExtractionAgent beans configured")
        self._primary = agents[0]
        self._fallback = agents[1] if len(agents) > 1 else None
        self._primary_failed = False

    def agent_count(self) -> int:
        return 1 if self._fallback is None else 2

    async def extract(self, file: SourceFile) -> ExtractionResult:
        if self._primary_failed and self._fallback is not None:
            return await self._fallback.extract(file)

        result = await self._primary.extract(file)
        if not result.success and self._fallback is not None and not self._primary_failed:
            self._primary_failed = True
            logger.warning(
                "%s failed (%s) -- falling back to %s for the rest of this ingestion run",
                self._primary.name(),
                result.error_message,
                self._fallback.name(),
            )
        return result


def build_agents(
    build_messages: Callable[[SourceFile], tuple[str, str]] | None = None,
) -> list[LogicExtractionAgent]:
    def _claude() -> ClaudeLogicExtractionAgent:
        return ClaudeLogicExtractionAgent() if build_messages is None else ClaudeLogicExtractionAgent(build_messages)

    def _ollama() -> OllamaLogicExtractionAgent:
        return OllamaLogicExtractionAgent() if build_messages is None else OllamaLogicExtractionAgent(build_messages)

    if settings.INGEST_LLM_MODEL == "claude":
        return [_claude(), _ollama()]
    if settings.INGEST_OLLAMA_ENABLED:
        return [_ollama()]
    return []


_agent_router: AgentRouter | None = None


def get_agent_router() -> AgentRouter:
    """Process-wide singleton for callers that need at least one agent to
    exist (raises if none are configured) -- enrich.py/enrich_documents.py do
    NOT use this; they call build_agents() directly so they can skip
    enrichment gracefully instead. Which agents exist only changes via
    INGEST_LLM_MODEL/INGEST_OLLAMA_ENABLED/the ANTHROPIC_API_KEY presence,
    all restart-required settings already, so building this once per process
    is safe.
    """
    global _agent_router
    if _agent_router is None:
        _agent_router = AgentRouter(build_agents())
    return _agent_router
