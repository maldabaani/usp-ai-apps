"""Covers ingestion/enrichment/agents/selector.py, moved from codemind/agents/selector.py (originally ported from
com.jslogicextractor.agent.AgentSelector)."""
import pytest

from ingestion.enrichment.agents.selector import AgentSelector


class _StubAgent:
    def __init__(self, agent_name: str) -> None:
        self._name = agent_name

    def name(self) -> str:
        return self._name

    async def extract(self, file):
        raise NotImplementedError("not used in this test")


def test_round_robins_across_multiple_agents():
    a = _StubAgent("a")
    b = _StubAgent("b")
    selector = AgentSelector([a, b])

    assert selector.next().name() == "a"
    assert selector.next().name() == "b"
    assert selector.next().name() == "a"


def test_single_agent_always_returns_itself():
    only = _StubAgent("only")
    selector = AgentSelector([only])

    assert selector.next() is only
    assert selector.next() is only


def test_rejects_empty_agent_list():
    with pytest.raises(ValueError):
        AgentSelector([])
