"""Round-robins across every registered LogicExtractionAgent.

Ported from com.jslogicextractor.agent.AgentSelector. build_agents() replaces
Java's @ConditionalOnExpression/@ConditionalOnProperty bean registration with
a plain factory: Claude registers iff ANTHROPIC_API_KEY is non-blank, Ollama
iff CODEMIND_OLLAMA_ENABLED is set -- this is exactly what fixes the "No
LogicExtractionAgent beans configured" startup crash when neither condition
is met.
"""
from __future__ import annotations

from itertools import count

from codemind.agents.base import LogicExtractionAgent
from codemind.agents.claude_agent import ClaudeLogicExtractionAgent
from codemind.agents.ollama_agent import OllamaLogicExtractionAgent
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
    if settings.CODEMIND_OLLAMA_ENABLED:
        agents.append(OllamaLogicExtractionAgent())
    return agents


_agent_selector: AgentSelector | None = None


def get_agent_selector() -> AgentSelector:
    """Process-wide singleton, shared by every caller that dispatches an
    extraction (the /extraction-jobs router, the file-watcher) -- matching
    Java's singleton AgentSelector bean. Which agents exist only changes via
    codemind_ollama_enabled/the ANTHROPIC_API_KEY presence, both
    restart-required settings already (see api/routers/settings.py's
    RESTART_REQUIRED_FIELDS), so building this once per process is safe.
    """
    global _agent_selector
    if _agent_selector is None:
        _agent_selector = AgentSelector(build_agents())
    return _agent_selector
