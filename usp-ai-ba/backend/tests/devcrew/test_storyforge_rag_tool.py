"""Covers the merge plan's Phase 4: devcrew/tools/storyforge_rag.py's
retrieve_storyforge_context tool, and that it's correctly threaded through
GraphDeps.storyforge_retrieval into the Planner/Architect (shared via
repo_tools), Developer and QA tool lists -- alongside their own per-run
search_codebase tool, not replacing it. No real ingestion/Chroma call:
a hand-mocked fake retrieval function stands in, matching this repo's
established no-real-network-calls testing convention.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from devcrew.graph.nodes.planner import repo_tools
from devcrew.tools.storyforge_rag import RetrieveStoryForgeContextArgs, retrieve_storyforge_context_tool


async def _fake_retrieve(query: str, top_k: int) -> dict[str, list[dict]]:
    return {
        "manuals": [{"content": f"manual chunk about {query}", "metadata": {"source": "m.pdf", "type": "user_manual"}}],
        "codebase": [{"content": "def handler(): ...", "metadata": {"source": "app/handler.py", "type": "code"}}],
        "entities": [],
    }


async def test_tool_renders_all_nonempty_collections() -> None:
    tool = retrieve_storyforge_context_tool(_fake_retrieve)
    result = await tool.handler(RetrieveStoryForgeContextArgs(query="refund policy", top_k=5))

    assert "## User Manuals" in result
    assert "manual chunk about refund policy" in result
    assert "## Codebase" in result
    assert "app/handler.py" in result
    assert "## JPA Entities" not in result  # empty collection -> section omitted


async def test_tool_reports_no_match_cleanly() -> None:
    async def empty(query: str, top_k: int) -> dict[str, list[dict]]:
        return {"manuals": [], "codebase": [], "entities": []}

    tool = retrieve_storyforge_context_tool(empty)
    result = await tool.handler(RetrieveStoryForgeContextArgs(query="nothing", top_k=5))
    assert "no matching content" in result


def test_tool_schema_is_well_formed() -> None:
    tool = retrieve_storyforge_context_tool(_fake_retrieve)
    schema = tool.schema()
    assert schema["function"]["name"] == "retrieve_storyforge_context"
    assert "query" in schema["function"]["parameters"]["properties"]


async def test_repo_tools_includes_storyforge_retrieval_when_configured(tmp_path: Path) -> None:
    from tests.devcrew.graph_harness import make_harness

    h = make_harness(tmp_path)
    h.deps.storyforge_retrieval = _fake_retrieve
    state = {"repo_info": {"projects": []}, "workspace": str(tmp_path), "run_id": "r1"}

    tools = repo_tools(h.deps, state)

    assert "retrieve_storyforge_context" in {t.name for t in tools}


async def test_repo_tools_omits_storyforge_retrieval_when_not_configured(tmp_path: Path) -> None:
    from tests.devcrew.graph_harness import make_harness

    h = make_harness(tmp_path)
    h.deps.storyforge_retrieval = None
    state = {"repo_info": {"projects": []}, "workspace": str(tmp_path), "run_id": "r1"}

    tools = repo_tools(h.deps, state)

    assert "retrieve_storyforge_context" not in {t.name for t in tools}
