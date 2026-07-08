"""Covers ingestion/enrichment/enrich.py -- the optional LLM-summary
enrichment tier folded in from CodeMind's per-file extraction. A stub agent
(matching the LogicExtractionAgent protocol: name()/extract()) stands in for
a real ChatAnthropic/ChatOllama-backed agent, and a fake vector store stands
in for Chroma, matching this codebase's established mocked-client testing
convention.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from ingestion import chroma_client
from ingestion.enrichment import enrich
from ingestion.enrichment.agents.base import ExtractionResult, failure_result, success_result


class _FakeVectorStore:
    def __init__(self):
        self.docs: dict[str, object] = {}
        self.add_calls: list[tuple] = []

    async def aadd_documents(self, documents, ids=None):
        ids = ids or [str(uuid.uuid4()) for _ in documents]
        self.add_calls.append((documents, ids))
        for id_, doc in zip(ids, documents):
            self.docs[id_] = doc
        return ids

    async def adelete(self, ids=None, where=None):
        if where:
            source = where.get("source") if "source" in where else None
            if source is None and "$and" in where:
                for clause in where["$and"]:
                    if "source" in clause:
                        source = clause["source"]
            for id_ in [i for i, d in self.docs.items() if d.metadata.get("source") == source]:
                del self.docs[id_]

    def get(self, include=None):
        return {"metadatas": [doc.metadata for doc in self.docs.values()]}


@pytest.fixture
def fake_store(monkeypatch):
    store = _FakeVectorStore()
    monkeypatch.setattr(chroma_client, "get_vector_store", lambda key: store)
    return store


class _StubAgent:
    """Returns a fixed successful summary; records every file it was asked
    to extract, so tests can assert incremental-skip / oversized-file
    batching behavior via call count."""

    def __init__(self, content: str = "a synthesized business-logic summary"):
        self.content = content
        self.calls: list[str] = []

    def name(self) -> str:
        return "stub-agent"

    async def extract(self, file) -> ExtractionResult:
        self.calls.append(file.relative_path)
        return success_result(file, self.name(), self.content, 0, None, None)


class _FailingAgent:
    def name(self) -> str:
        return "failing-agent"

    async def extract(self, file) -> ExtractionResult:
        return failure_result(file, self.name(), "boom", 0)


class _RaisingAgent:
    """Unlike _FailingAgent (a graceful failure_result(), the "no work
    produced" case), this raises an exception -- the genuine "error" status
    case, e.g. a real network/API failure."""

    def name(self) -> str:
        return "raising-agent"

    async def extract(self, file) -> ExtractionResult:
        raise RuntimeError("credit balance too low")


def _write(repo, relative: str, content: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_disabled_is_a_pure_no_op(tmp_path, monkeypatch, fake_store):
    def fail_if_called():
        raise AssertionError("build_agents() must not be called when enrichment is disabled")

    monkeypatch.setattr(enrich, "build_agents", fail_if_called)

    result = asyncio.run(enrich.enrich_repository(tmp_path, [], enabled=False))

    assert result == {
        "enabled": False,
        "files_summarized": 0,
        "files_skipped_unchanged": 0,
        "errors": [],
        "files": [],
    }


def test_disabled_marks_every_file_skipped_with_reason(tmp_path, monkeypatch, fake_store):
    _write(tmp_path, "app.py", "def handler(): pass\n")

    result = asyncio.run(enrich.enrich_repository(tmp_path, [tmp_path / "app.py"], enabled=False))

    assert result["files"] == [{"path": "app.py", "status": "skipped", "reason": "llm_summary_disabled"}]


def test_skips_gracefully_when_no_agents_configured(tmp_path, monkeypatch, fake_store):
    monkeypatch.setattr(enrich, "build_agents", lambda: [])

    result = asyncio.run(enrich.enrich_repository(tmp_path, [], enabled=True))

    assert result["enabled"] is False
    assert result["errors"] == []


def test_no_agents_configured_marks_every_file_skipped_with_reason(tmp_path, monkeypatch, fake_store):
    _write(tmp_path, "app.py", "def handler(): pass\n")
    monkeypatch.setattr(enrich, "build_agents", lambda: [])

    result = asyncio.run(enrich.enrich_repository(tmp_path, [tmp_path / "app.py"], enabled=True))

    assert result["files"] == [{"path": "app.py", "status": "skipped", "reason": "no_agents_configured"}]


def test_summarizes_eligible_file_and_writes_llm_summary_document(tmp_path, monkeypatch, fake_store):
    _write(tmp_path, "app.py", "def handler():\n    return True\n")
    agent = _StubAgent("if the user is an admin, settings can be changed")
    monkeypatch.setattr(enrich, "build_agents", lambda: [agent])

    result = asyncio.run(
        enrich.enrich_repository(tmp_path, [tmp_path / "app.py"], enabled=True, manifests_root=tmp_path / "manifests")
    )

    assert result["enabled"] is True
    assert result["files_summarized"] == 1
    assert agent.calls == ["app.py"]
    assert result["files"] == [{"path": "app.py", "status": "summarized"}]
    docs = list(fake_store.docs.values())
    assert len(docs) == 1
    assert docs[0].metadata["type"] == "llm_summary"
    assert docs[0].metadata["source"] == "app.py"
    assert docs[0].metadata["language"] == "python"
    assert "admin" in docs[0].page_content


def test_skip_reason_files_are_not_summarized(tmp_path, monkeypatch, fake_store):
    _write(tmp_path, "src/foo.test.tsx", "export const x = 1;\n")
    agent = _StubAgent()
    monkeypatch.setattr(enrich, "build_agents", lambda: [agent])

    result = asyncio.run(
        enrich.enrich_repository(
            tmp_path, [tmp_path / "src/foo.test.tsx"], enabled=True, manifests_root=tmp_path / "manifests"
        )
    )

    assert result["files_summarized"] == 0
    assert agent.calls == []
    assert result["files"] == [{"path": "src/foo.test.tsx", "status": "skipped", "reason": "test/spec file"}]


def test_failed_extraction_is_not_written_and_recorded_in_errors_only_on_exception(tmp_path, monkeypatch, fake_store):
    _write(tmp_path, "app.py", "def handler(): pass\n")
    monkeypatch.setattr(enrich, "build_agents", lambda: [_FailingAgent()])

    result = asyncio.run(
        enrich.enrich_repository(tmp_path, [tmp_path / "app.py"], enabled=True, manifests_root=tmp_path / "manifests")
    )

    assert result["files_summarized"] == 0
    assert result["errors"] == []
    assert len(fake_store.docs) == 0
    assert result["files"] == [{"path": "app.py", "status": "skipped", "reason": "no_summary_produced"}]


def test_raised_exception_is_recorded_with_error_status_and_message(tmp_path, monkeypatch, fake_store):
    _write(tmp_path, "app.py", "def handler(): pass\n")
    monkeypatch.setattr(enrich, "build_agents", lambda: [_RaisingAgent()])

    result = asyncio.run(
        enrich.enrich_repository(tmp_path, [tmp_path / "app.py"], enabled=True, manifests_root=tmp_path / "manifests")
    )

    assert result["errors"] == ["app.py: credit balance too low"]
    assert result["files"] == [{"path": "app.py", "status": "error", "reason": "credit balance too low"}]


def test_failed_extraction_is_retried_on_next_run_not_silently_skipped(tmp_path, monkeypatch, fake_store):
    """Regression test: process_one() used to record a file's content hash as
    "seen" even when the enrichment call itself raised (e.g. a real 400 from
    an exhausted API credit balance) -- so a transient failure was silently
    and permanently treated as done on every later run, since the file's
    content never changed. The manifest must only remember a hash once real
    work succeeded."""
    _write(tmp_path, "app.py", "def handler(): pass\n")
    monkeypatch.setattr(enrich, "build_agents", lambda: [_FailingAgent()])
    manifests_root = tmp_path / "manifests"

    asyncio.run(
        enrich.enrich_repository(tmp_path, [tmp_path / "app.py"], enabled=True, manifests_root=manifests_root)
    )

    agent = _StubAgent()
    monkeypatch.setattr(enrich, "build_agents", lambda: [agent])
    result = asyncio.run(
        enrich.enrich_repository(tmp_path, [tmp_path / "app.py"], enabled=True, manifests_root=manifests_root)
    )

    assert agent.calls == ["app.py"]  # retried, not skipped as "unchanged"
    assert result["files_summarized"] == 1
    assert result["files_skipped_unchanged"] == 0


def test_incremental_skip_avoids_resummarizing_unchanged_file(tmp_path, monkeypatch, fake_store):
    _write(tmp_path, "app.py", "def handler():\n    return True\n")
    agent = _StubAgent()
    monkeypatch.setattr(enrich, "build_agents", lambda: [agent])
    manifests_root = tmp_path / "manifests"

    asyncio.run(
        enrich.enrich_repository(tmp_path, [tmp_path / "app.py"], enabled=True, manifests_root=manifests_root)
    )
    assert agent.calls == ["app.py"]

    result = asyncio.run(
        enrich.enrich_repository(tmp_path, [tmp_path / "app.py"], enabled=True, manifests_root=manifests_root)
    )

    assert agent.calls == ["app.py"]  # not called again
    assert result["files_skipped_unchanged"] == 1
    assert result["files_summarized"] == 0
    assert result["files"] == [{"path": "app.py", "status": "skipped", "reason": "unchanged_since_last_run"}]


def test_changed_file_content_is_resummarized(tmp_path, monkeypatch, fake_store):
    _write(tmp_path, "app.py", "def handler():\n    return True\n")
    agent = _StubAgent()
    monkeypatch.setattr(enrich, "build_agents", lambda: [agent])
    manifests_root = tmp_path / "manifests"

    asyncio.run(
        enrich.enrich_repository(tmp_path, [tmp_path / "app.py"], enabled=True, manifests_root=manifests_root)
    )

    _write(tmp_path, "app.py", "def handler():\n    return False\n")
    result = asyncio.run(
        enrich.enrich_repository(tmp_path, [tmp_path / "app.py"], enabled=True, manifests_root=manifests_root)
    )

    assert agent.calls == ["app.py", "app.py"]
    assert result["files_summarized"] == 1


def test_progress_callback_reports_in_progress_then_completion_per_file(tmp_path, monkeypatch, fake_store):
    _write(tmp_path, "a.py", "def a(): pass\n")
    _write(tmp_path, "b.py", "def b(): pass\n")
    agent = _StubAgent()
    monkeypatch.setattr(enrich, "build_agents", lambda: [agent])

    calls: list[tuple] = []

    async def progress_callback(done, total, *, phase, partial_result):
        calls.append((done, total, phase, partial_result))

    result = asyncio.run(
        enrich.enrich_repository(
            tmp_path,
            [tmp_path / "a.py", tmp_path / "b.py"],
            enabled=True,
            manifests_root=tmp_path / "manifests",
            progress_callback=progress_callback,
        )
    )

    # Two ticks per file (an "in_progress" tick right before its LLM call,
    # then a completion tick) -- not just once at the very end -- so the UI
    # can show live activity instead of the phase label going silent for a
    # file's entire (possibly long) summarization time.
    assert len(calls) == 4
    assert all(phase == "enrichment" for _done, _total, phase, _partial in calls)
    assert all(total == 2 for _done, total, _phase, _partial in calls)
    # At least one early tick shows a file actually marked in_progress --
    # proving the live-activity signal fires, not just terminal statuses.
    assert any(
        any(f["status"] == "in_progress" for f in partial["enrichment_files"]) for _d, _t, _p, partial in calls
    )
    # Completion order under the semaphore is nondeterministic, so assert
    # membership as a set of paths rather than a fixed order.
    last_partial = calls[-1][3]
    assert {f["path"] for f in last_partial["enrichment_files"]} == {"a.py", "b.py"}
    # The final tick must show no lingering "in_progress" entries -- every
    # file has reached a terminal status.
    assert all(f["status"] != "in_progress" for f in last_partial["enrichment_files"])
    assert {f["path"] for f in result["files"]} == {"a.py", "b.py"}


def test_oversized_file_is_chunked_and_summaries_joined(tmp_path, monkeypatch, fake_store):
    huge_content = "\n".join(f"line_{i} = {i}" for i in range(1000))
    _write(tmp_path, "big.py", huge_content)
    agent = _StubAgent("part summary")
    monkeypatch.setattr(enrich, "build_agents", lambda: [agent])

    result = asyncio.run(
        enrich.enrich_repository(tmp_path, [tmp_path / "big.py"], enabled=True, manifests_root=tmp_path / "manifests")
    )

    assert result["files_summarized"] == 1
    assert len(agent.calls) > 1  # split into multiple chunker parts
    doc = next(iter(fake_store.docs.values()))
    assert doc.page_content.count("part summary") == len(agent.calls)


def test_oversized_file_reports_progress_per_part(tmp_path, monkeypatch, fake_store):
    # A single huge file dominating a whole run (the motivating case: a
    # multi-thousand-line file split into many chunker parts, each its own
    # slow LLM call) must show live per-part activity, not just one
    # "in_progress"/"summarizing" note frozen for the file's entire duration.
    huge_content = "\n".join(f"line_{i} = {i}" for i in range(1000))
    _write(tmp_path, "big.py", huge_content)
    agent = _StubAgent("part summary")
    monkeypatch.setattr(enrich, "build_agents", lambda: [agent])

    calls: list[tuple] = []

    async def progress_callback(done, total, *, phase, partial_result):
        calls.append((done, total, phase, partial_result))

    asyncio.run(
        enrich.enrich_repository(
            tmp_path,
            [tmp_path / "big.py"],
            enabled=True,
            manifests_root=tmp_path / "manifests",
            progress_callback=progress_callback,
        )
    )

    in_progress_notes = [
        f["reason"]
        for _d, _t, _p, partial in calls
        for f in partial["enrichment_files"]
        if f["status"] == "in_progress"
    ]
    # At least two distinct "part N/M" notes were reported over the course
    # of the multi-part file, not just a single static "summarizing" note.
    assert len(agent.calls) > 1
    assert len({note for note in in_progress_notes if note.startswith("summarizing part")}) > 1


def test_oversized_file_reports_a_moving_overall_percentage(tmp_path, monkeypatch, fake_store):
    # A single-file job's overall progress must not sit frozen at 0% for the
    # entire enrichment run just because "done" (whole files) only ticks
    # once, at the very end -- it should credit partial progress from parts
    # already completed within that one still-in-flight file.
    huge_content = "\n".join(f"line_{i} = {i}" for i in range(1000))
    _write(tmp_path, "big.py", huge_content)
    agent = _StubAgent("part summary")
    monkeypatch.setattr(enrich, "build_agents", lambda: [agent])

    percents: list[float] = []

    async def progress_callback(done, total, *, phase, partial_result):
        percents.append(partial_result["enrichment_percent"])

    asyncio.run(
        enrich.enrich_repository(
            tmp_path,
            [tmp_path / "big.py"],
            enabled=True,
            manifests_root=tmp_path / "manifests",
            progress_callback=progress_callback,
        )
    )

    assert len(agent.calls) > 1
    # Strictly increasing (or at least non-decreasing) across the run, ending
    # at 100% -- not stuck at 0 until the last tick.
    assert percents[0] == 0.0
    assert percents[-1] == 100.0
    assert any(0 < p < 100 for p in percents)
    assert percents == sorted(percents)


def test_single_part_file_percentage_jumps_from_zero_to_full_credit(tmp_path, monkeypatch, fake_store):
    # A file too small to be split has no meaningful sub-progress -- its
    # fractional credit stays 0 for the file's entire single LLM call, then
    # jumps straight to full credit once it's actually done. Two files here
    # so there's still an overall percentage besides 0/100.
    _write(tmp_path, "a.py", "def a(): pass\n")
    _write(tmp_path, "b.py", "def b(): pass\n")
    agent = _StubAgent()
    monkeypatch.setattr(enrich, "build_agents", lambda: [agent])

    percents: list[float] = []

    async def progress_callback(done, total, *, phase, partial_result):
        percents.append(partial_result["enrichment_percent"])

    asyncio.run(
        enrich.enrich_repository(
            tmp_path,
            [tmp_path / "a.py", tmp_path / "b.py"],
            enabled=True,
            manifests_root=tmp_path / "manifests",
            progress_callback=progress_callback,
        )
    )

    assert percents[0] == 0.0
    assert percents[-1] == 100.0
