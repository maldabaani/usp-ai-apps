"""Round-robins across every registered LogicExtractionAgent.

build_agents() is a plain factory: Claude registers iff ANTHROPIC_API_KEY is
non-blank, Ollama iff INGEST_OLLAMA_ENABLED is set.

ingestion/enrichment/enrich.py deliberately calls build_agents() directly
(not get_agent_selector()) so it can check for an empty list and skip the
LLM-summary enrichment tier gracefully with a logged warning -- ingestion's
raw-chunk tier must still succeed even with no LLM configured at all.
AgentSelector itself keeps raising ValueError on an empty list, since that's
still the right behavior for any caller that does need at least one agent.
"""
from __future__ import annotations

from itertools import count

from ingestion.enrichment.agents.base import LogicExtractionAgent
from ingestion.enrichment.agents.claude_agent import ClaudeLogicExtractionAgent
from ingestion.enrichment.agents.ollama_agent import OllamaLogicExtractionAgent
from config import settings


class AgentSelector:
    def __init__(self, agents: list[LogicExtractionAgent]) -> None:
        if not agents:
            raise ValueError("No LogicExtractionAgent beans configured")
        self._agents = list(agents)
        self._counter = count()

    def next(self) -> LogicExtractionAgent:
        index = next(self._counter) % len(self._agents)
        return self._agents[index]

    def agent_count(self) -> int:
        return len(self._agents)


def build_agents() -> list[LogicExtractionAgent]:
    agents: list[LogicExtractionAgent] = []
    if settings.ANTHROPIC_API_KEY.strip():
        agents.append(ClaudeLogicExtractionAgent())
    if settings.INGEST_OLLAMA_ENABLED:
        agents.append(OllamaLogicExtractionAgent())
    return agents


_agent_selector: AgentSelector | None = None


def get_agent_selector() -> AgentSelector:
    """Process-wide singleton for callers that need at least one agent to
    exist (raises if none are configured) -- enrich.py does NOT use this; it
    calls build_agents() directly so it can skip enrichment gracefully
    instead. Which agents exist only changes via INGEST_OLLAMA_ENABLED/the
    ANTHROPIC_API_KEY presence, both restart-required settings already, so
    building this once per process is safe.
    """
    global _agent_selector
    if _agent_selector is None:
        _agent_selector = AgentSelector(build_agents())
    return _agent_selector
