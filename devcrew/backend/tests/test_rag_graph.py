"""RAG inside the graph: index at scaffold, re-index after merges, retrieval, cleanup."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

from app.db.models import RunStatus
from app.events.types import EventType
from app.graph.interrupts import ResumePayload
from app.tools.git import GitRepo
from tests.fakes import Call, FakeChroma, final, tool_call, tool_results
from tests.graph_harness import APPROVE, Harness, default_developer, make_harness

RUN = "run0003aaaabbbbccccdddd"
COLLECTION = f"run_{RUN}"


def developer(call: Call) -> AIMessage:
    """T1 writes a recognizable model; others use the default script."""
    if "id: T1" in str(call.messages[1].content) and not tool_results(call):
        return tool_call(
            "write_file",
            path="app/schemas/todo.py",
            content="class TodoCreate:\n    title: str\n    priority: int\n",
        )
    return default_developer(call)


async def to_final(h: Harness) -> Any:
    h.brain.responders["developer"] = developer
    await h.driver.start(RUN, "Build a TODO API", "me/todo", False)
    await h.driver.resume(RUN, APPROVE)
    return await h.driver.resume(RUN, APPROVE)


def indexed(chroma: FakeChroma) -> dict[str, dict[str, Any]]:
    return {m["path"]: m for _, _, m in chroma.collections[COLLECTION].rows.values()}


async def test_index_lifecycle_and_retrieval(tmp_path: Path) -> None:
    chroma = FakeChroma()
    h = make_harness(tmp_path, chroma=chroma)
    outcome = await to_final(h)
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL

    meta = indexed(chroma)
    # Scaffold: template + design doc + rules; merges: every task's files.
    assert {
        "app/main.py",
        "docs/design.md",
        "app/schemas/todo.py",
        "app/routers/todos.py",
        "rules/python.md",
    } <= set(meta)
    assert meta["rules/python.md"]["source"] == "rules"
    ws = Path((await h.driver.state(RUN))["workspace"])
    t1_commit = (await GitRepo(ws).run("log", "--format=%H", "--grep=^T1:", "-1")).strip()
    assert meta["app/schemas/todo.py"]["commit_sha"] == t1_commit
    assert meta["app/schemas/todo.py"]["symbol"] == "class TodoCreate"

    events = [e for e in await h.events(RUN) if e.payload.get("tool") == "index_codebase"]
    assert [e.node for e in events] == ["scaffold", "merge", "merge"]
    assert events[1].payload["stats"]["added"] == ["app/schemas/todo.py", "tests/test_t1.py"]

    # T2 (depends on T1) sees T1's merged code as retrieved context; T1 did not.
    fresh = [c for c in h.brain.calls_for("developer") if c.fresh]
    assert "class TodoCreate" not in fresh[0].text()
    assert "Relevant existing code" in fresh[1].text() and "class TodoCreate" in fresh[1].text()
    qa_t2 = next(c for c in h.brain.calls_for("qa") if "id: T2" in c.text())
    assert "Existing tests" in qa_t2.text() and "tests/test_t1.py" in qa_t2.text()

    tools = {c.role: set(c.tool_names) for c in h.brain.calls if c.has_tools}
    for role in ("developer", "reviewer", "qa"):
        assert "search_codebase" in tools[role], role
    assert "search_codebase" not in tools["planner"] | tools["architect"]

    outcome = await h.approve_until_done(await h.driver.resume(RUN, APPROVE), RUN)
    assert outcome.status is RunStatus.COMPLETED
    assert COLLECTION not in chroma.collections  # no cross-run memory


async def test_search_codebase_tool(tmp_path: Path) -> None:
    chroma = FakeChroma()
    h = make_harness(tmp_path, chroma=chroma)
    results: list[str] = []

    def dev(call: Call) -> AIMessage:
        if "id: T2" not in str(call.messages[1].content):
            return developer(call)
        found = tool_results(call)
        if not found:
            return tool_call(
                "search_codebase", query="TodoCreate priority", k=2, path_filter="app/"
            )
        if len(found) == 1:
            results.append(str(found[0].content))
            return tool_call("write_file", path="app/routers/todos.py", content="X = 1\n")
        return final("done")

    h.brain.responders["developer"] = dev
    await h.driver.start(RUN, "Build a TODO API", "me/todo", False)
    await h.driver.resume(RUN, APPROVE)
    await h.driver.resume(RUN, APPROVE)
    assert results[0].startswith("--- app/schemas/todo.py:1-3  (class TodoCreate)")
    assert "priority: int" in results[0]


async def test_failed_run_also_drops_collection(tmp_path: Path) -> None:
    chroma = FakeChroma()
    h = make_harness(tmp_path, chroma=chroma)
    outcome = await to_final(h)
    assert COLLECTION in chroma.collections
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL
    # Force a run-level failure path: a fresh run whose planner cannot produce a plan.
    h.brain.responders["planner"] = lambda c: final("no json")
    other = "run0004aaaabbbbccccdddd"
    chroma.get_or_create_collection(f"run_{other}")
    await h.driver.start(other, "x", "me/x", False)
    outcome = await h.driver.resume(other, ResumePayload(action="reject", feedback="abort"))
    assert outcome.status is RunStatus.FAILED
    assert f"run_{other}" not in chroma.collections
    assert COLLECTION in chroma.collections  # other runs are untouched


async def test_indexing_failure_is_reported_not_fatal(tmp_path: Path) -> None:
    class BrokenChroma(FakeChroma):
        def get_or_create_collection(self, name: str, **kwargs: Any) -> Any:
            raise ConnectionError("chroma down")

    h = make_harness(tmp_path, chroma=BrokenChroma())
    outcome = await to_final(h)
    assert outcome.status is RunStatus.AWAITING_FINAL_APPROVAL
    errors = [e.payload["message"] for e in await h.events(RUN) if e.type is EventType.ERROR]
    assert any("indexing failed: chroma down" in m for m in errors)
