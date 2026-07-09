"""Covers ingestion/enrichment/enrich.py -- the optional LLM-summary
enrichment tier folded in from CodeMind's per-file extraction. A stub agent
(matching the LogicExtractionAgent protocol: name()/extract()) stands in for
a real ChatAnthropic/ChatOllama-backed agent, and a fake vector store stands
in for Chroma, matching this codebase's established mocked-client testing
convention.
"""
from __future__ import annotations

import asyncio
import re
import uuid

import pytest

from ingestion import chroma_client, ingest_code, manifest
from ingestion.enrichment import chunker, enrich, part_progress
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


class _FlakyAgent:
    """Succeeds for the first `succeed_count` calls (across the whole agent
    instance's lifetime, not per-file), then returns a graceful
    failure_result for every call after that -- simulates real Anthropic API
    credits running out partway through a large file's chunker.py parts."""

    def __init__(self, succeed_count: int, content: str = "part summary"):
        self.succeed_count = succeed_count
        self.content = content
        self.calls: list[str] = []

    def name(self) -> str:
        return "flaky-agent"

    async def extract(self, file) -> ExtractionResult:
        self.calls.append(file.relative_path)
        if len(self.calls) <= self.succeed_count:
            return success_result(file, self.name(), self.content, 0, None, None)
        return failure_result(file, self.name(), "credit balance too low", 0)


class _RaisingAgent:
    """Unlike _FailingAgent (a graceful failure_result(), the "no work
    produced" case), this raises an exception -- the genuine "error" status
    case, e.g. a real network/API failure."""

    def name(self) -> str:
        return "raising-agent"

    async def extract(self, file) -> ExtractionResult:
        raise RuntimeError("credit balance too low")


class _ConcurrencyTrackingAgent:
    """Sleeps briefly on every call so overlapping calls actually coexist,
    tracking the maximum number of calls in flight at once -- proves parts
    of a single oversized file are summarized concurrently (bounded by
    max_concurrency), not strictly one-at-a-time as before this fix."""

    def __init__(self, delay: float = 0.05):
        self.delay = delay
        self.calls: list[str] = []
        self._in_flight = 0
        self.max_in_flight = 0

    def name(self) -> str:
        return "concurrency-tracking-agent"

    async def extract(self, file) -> ExtractionResult:
        self.calls.append(file.relative_path)
        self._in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self._in_flight)
        await asyncio.sleep(self.delay)
        self._in_flight -= 1
        return success_result(file, self.name(), f"summary for {file.relative_path}", 0, None, None)


_PART_NUMBER_RE = re.compile(r"/part-(\d+)")


class _OrderInvertingAgent:
    """Completes later-arriving parts before earlier ones (sleeping longer
    for lower part numbers), to prove enrich.py's concurrent-parts path
    preserves original part order in the combined summary regardless of
    which part's LLM call actually finishes first."""

    def name(self) -> str:
        return "order-inverting-agent"

    async def extract(self, file) -> ExtractionResult:
        match = _PART_NUMBER_RE.search(file.relative_path)
        part_number = int(match.group(1)) if match else 0
        await asyncio.sleep(0.05 * (10 - part_number))
        return success_result(file, self.name(), f"summary-part-{part_number}", 0, None, None)


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
    # The real failure_result message ("boom") is named, not just the
    # generic "no_summary_produced" -- a graceful failure previously
    # discarded its own error_message entirely.
    assert result["files"] == [
        {"path": "app.py", "status": "skipped", "reason": "no_summary_produced (last error: 'boom')"}
    ]


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


def test_oversized_combined_summary_is_split_into_multiple_size_capped_documents(tmp_path, monkeypatch, fake_store):
    # Regression test: a multi-part file's combined summary previously had
    # no size ceiling at all, unlike ingest_code.py's mechanical chunks
    # (always capped at MAX_CHUNK_CHARS) -- in production this produced one
    # single Chroma document large enough to single-handedly balloon a
    # downstream prompt to 656,961 tokens, which Ollama then silently
    # truncated by 97%+.
    huge_content = "\n".join(f"line_{i} = {i}" for i in range(1000))
    _write(tmp_path, "big.py", huge_content)
    agent = _StubAgent("x" * 2000)  # large enough per-part that 3+ parts exceed MAX_CHUNK_CHARS
    monkeypatch.setattr(enrich, "build_agents", lambda: [agent])

    result = asyncio.run(
        enrich.enrich_repository(tmp_path, [tmp_path / "big.py"], enabled=True, manifests_root=tmp_path / "manifests")
    )

    assert result["files_summarized"] == 1
    assert len(agent.calls) > 1
    docs = [d for d in fake_store.docs.values() if d.metadata.get("source") == "big.py"]
    assert len(docs) > 1  # split into multiple documents, not one unbounded blob
    for doc in docs:
        assert len(doc.page_content) <= ingest_code.MAX_CHUNK_CHARS + 200  # small header overhead
    chunk_parts = sorted(doc.metadata["chunk_part"] for doc in docs)
    assert chunk_parts == list(range(len(docs)))  # sequential, no gaps
    ids = [id_ for id_, doc in fake_store.docs.items() if doc.metadata.get("source") == "big.py"]
    assert len(set(ids)) == len(ids)  # every piece gets a distinct id


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


def test_enrichment_eta_seconds_computed_from_observed_rate(tmp_path, monkeypatch, fake_store):
    # Two single-part files with no real async yield points anywhere in the
    # stub agent/fake store means asyncio.gather runs them in strict
    # creation order here (nothing ever actually suspends the event loop),
    # so with a fully deterministic fake clock the exact sequence of
    # enrichment_percent/enrichment_eta_seconds ticks is knowable: a.py
    # starts and finishes, then b.py starts and finishes.
    _write(tmp_path, "a.py", "def a(): pass\n")
    _write(tmp_path, "b.py", "def b(): pass\n")
    agent = _StubAgent()
    monkeypatch.setattr(enrich, "build_agents", lambda: [agent])

    # One call for run_started_at, then one per _report_progress tick (a.py
    # start, a.py done, b.py start, b.py done) = 5 total.
    fake_clock = iter([0, 0, 10, 10, 20])
    monkeypatch.setattr(enrich, "_monotonic", lambda: next(fake_clock))

    ticks: list[tuple] = []

    async def progress_callback(done, total, *, phase, partial_result):
        ticks.append((partial_result["enrichment_percent"], partial_result["enrichment_eta_seconds"]))

    asyncio.run(
        enrich.enrich_repository(
            tmp_path,
            [tmp_path / "a.py", tmp_path / "b.py"],
            enabled=True,
            manifests_root=tmp_path / "manifests",
            progress_callback=progress_callback,
        )
    )

    # a.py start: nothing done yet -- 0%, no ETA (can't estimate a rate with
    # zero completed work).
    assert ticks[0] == (0.0, None)
    # a.py done at t=10 (elapsed 10s for 1 of 2 parts): rate = 0.1 parts/s,
    # 1 part remaining -> 10s ETA.
    assert ticks[1] == (50.0, 10)
    # b.py start, still t=10: same observed rate/remaining as the previous
    # tick (b.py's own part hasn't completed yet).
    assert ticks[2] == (50.0, 10)
    # b.py done at t=20: everything finished, 0s remaining.
    assert ticks[3] == (100.0, 0)


def _huge_content(lines: int = 2000) -> str:
    # 2000 lines splits into exactly 5 chunker.py parts at the default
    # MAX_LINES_PER_CHUNK=400 -- confirmed directly against chunker.chunk().
    return "\n".join(f"line_{i} = {i}" for i in range(lines))


def test_partial_failure_on_multi_part_file_persists_completed_parts_and_reports_error(
    tmp_path, monkeypatch, fake_store
):
    _write(tmp_path, "big.py", _huge_content())
    agent = _FlakyAgent(succeed_count=3)
    monkeypatch.setattr(enrich, "build_agents", lambda: [agent])
    progress_root = tmp_path / "part-progress"

    result = asyncio.run(
        enrich.enrich_repository(
            tmp_path,
            [tmp_path / "big.py"],
            enabled=True,
            manifests_root=tmp_path / "manifests",
            progress_root=progress_root,
        )
    )

    assert len(agent.calls) == 5  # every part attempted this run
    assert result["files_summarized"] == 0  # incomplete -- never written
    assert len(fake_store.docs) == 0
    assert result["files"][0]["status"] == "error"
    reason = result["files"][0]["reason"]
    assert "3/5 parts summarized" in reason
    # The real, deduped failure message (both failed parts hit the same
    # cause) is named, not a generic "rate limit or exhausted credits"
    # placeholder.
    assert '"credit balance too low" (2)' in reason
    assert any("3/5 parts summarized" in e for e in result["errors"])


def test_multi_part_file_with_distinct_error_messages_are_each_counted(tmp_path, monkeypatch, fake_store):
    class _MultiErrorAgent:
        def __init__(self):
            self.calls: list[str] = []

        def name(self) -> str:
            return "multi-error-agent"

        async def extract(self, file):
            self.calls.append(file.relative_path)
            call_number = len(self.calls)
            if call_number <= 2:
                return success_result(file, self.name(), "part summary", 0, None, None)
            if call_number == 3:
                return failure_result(file, self.name(), "rate limit exceeded", 0)
            return failure_result(file, self.name(), "connection refused", 0)

    _write(tmp_path, "big.py", _huge_content())
    monkeypatch.setattr(enrich, "build_agents", lambda: [_MultiErrorAgent()])

    result = asyncio.run(
        enrich.enrich_repository(
            tmp_path,
            [tmp_path / "big.py"],
            enabled=True,
            manifests_root=tmp_path / "manifests",
            progress_root=tmp_path / "part-progress",
        )
    )

    reason = result["files"][0]["reason"]
    assert '"rate limit exceeded" (1)' in reason
    assert '"connection refused" (2)' in reason


def test_more_than_three_distinct_error_messages_are_truncated(tmp_path, monkeypatch, fake_store):
    class _EveryPartDifferentErrorAgent:
        def __init__(self):
            self.calls: list[str] = []

        def name(self) -> str:
            return "every-part-different-error-agent"

        async def extract(self, file):
            self.calls.append(file.relative_path)
            return failure_result(file, self.name(), f"error #{len(self.calls)}", 0)

    huge_content = "\n".join(f"line_{i} = {i}" for i in range(3600))  # 9 chunker.py parts
    _write(tmp_path, "big.py", huge_content)
    monkeypatch.setattr(enrich, "build_agents", lambda: [_EveryPartDifferentErrorAgent()])

    result = asyncio.run(
        enrich.enrich_repository(
            tmp_path,
            [tmp_path / "big.py"],
            enabled=True,
            manifests_root=tmp_path / "manifests",
            progress_root=tmp_path / "part-progress",
        )
    )

    reason = result["files"][0]["reason"]
    assert "and 6 more distinct error(s)" in reason  # 9 distinct messages, top 3 shown


def test_resumed_run_only_retries_missing_parts_not_all_of_them(tmp_path, monkeypatch, fake_store):
    _write(tmp_path, "big.py", _huge_content())
    manifests_root = tmp_path / "manifests"
    progress_root = tmp_path / "part-progress"

    flaky = _FlakyAgent(succeed_count=3)
    monkeypatch.setattr(enrich, "build_agents", lambda: [flaky])
    first = asyncio.run(
        enrich.enrich_repository(
            tmp_path,
            [tmp_path / "big.py"],
            enabled=True,
            manifests_root=manifests_root,
            progress_root=progress_root,
        )
    )
    assert first["files_summarized"] == 0

    # Credits "restored": a fresh agent that always succeeds. Only the 2
    # parts that failed last time should ever reach it -- the 3 that already
    # succeeded must be served from persisted progress, never re-billed.
    resumed_agent = _StubAgent("resumed summary")
    monkeypatch.setattr(enrich, "build_agents", lambda: [resumed_agent])
    second = asyncio.run(
        enrich.enrich_repository(
            tmp_path,
            [tmp_path / "big.py"],
            enabled=True,
            manifests_root=manifests_root,
            progress_root=progress_root,
        )
    )

    assert len(resumed_agent.calls) == 2  # only the previously-missing parts
    assert second["files_summarized"] == 1
    doc = next(iter(fake_store.docs.values()))
    assert doc.page_content.count("part summary") == 3  # carried over from the first run
    assert doc.page_content.count("resumed summary") == 2  # newly completed this run


def test_full_completion_clears_partial_progress_file(tmp_path, monkeypatch, fake_store):
    _write(tmp_path, "big.py", _huge_content())
    agent = _StubAgent("part summary")
    monkeypatch.setattr(enrich, "build_agents", lambda: [agent])
    progress_root = tmp_path / "part-progress"

    result = asyncio.run(
        enrich.enrich_repository(
            tmp_path,
            [tmp_path / "big.py"],
            enabled=True,
            manifests_root=tmp_path / "manifests",
            progress_root=progress_root,
        )
    )

    assert result["files_summarized"] == 1
    progress_file = part_progress._progress_path(progress_root, tmp_path, "big.py")
    assert not progress_file.exists()


def test_content_change_invalidates_stale_partial_progress(tmp_path, monkeypatch, fake_store):
    _write(tmp_path, "big.py", _huge_content())
    manifests_root = tmp_path / "manifests"
    progress_root = tmp_path / "part-progress"

    flaky = _FlakyAgent(succeed_count=3)
    monkeypatch.setattr(enrich, "build_agents", lambda: [flaky])
    asyncio.run(
        enrich.enrich_repository(
            tmp_path,
            [tmp_path / "big.py"],
            enabled=True,
            manifests_root=manifests_root,
            progress_root=progress_root,
        )
    )

    # Content changes -- even though the relative path is the same, the old
    # part summaries no longer correspond to the current text and must not
    # be reused.
    new_content = _huge_content(2001)
    _write(tmp_path, "big.py", new_content)
    expected_parts = len(chunker.chunk(tmp_path / "big.py", "big.py", new_content, enrich.MAX_LINES_PER_CHUNK))
    fresh_agent = _StubAgent("fresh summary")
    monkeypatch.setattr(enrich, "build_agents", lambda: [fresh_agent])
    result = asyncio.run(
        enrich.enrich_repository(
            tmp_path,
            [tmp_path / "big.py"],
            enabled=True,
            manifests_root=manifests_root,
            progress_root=progress_root,
        )
    )

    assert len(fresh_agent.calls) == expected_parts  # every part attempted fresh, none skipped
    assert result["files_summarized"] == 1


def test_oversized_file_parts_run_concurrently_bounded_by_max_concurrency(tmp_path, monkeypatch, fake_store):
    # Regression test for the actual bug reported live: a single huge file's
    # parts used to run strictly one-at-a-time regardless of max_concurrency,
    # since the only semaphore in enrich_repository bounded concurrent
    # *files*, not parts -- useless when there's only one file. This proves
    # parts of one file now run concurrently, bounded by max_concurrency.
    huge_content = "\n".join(f"line_{i} = {i}" for i in range(2000))  # 5 parts of 400 lines
    _write(tmp_path, "big.py", huge_content)
    agent = _ConcurrencyTrackingAgent(delay=0.05)
    monkeypatch.setattr(enrich, "build_agents", lambda: [agent])

    result = asyncio.run(
        enrich.enrich_repository(
            tmp_path,
            [tmp_path / "big.py"],
            enabled=True,
            max_concurrency=3,
            manifests_root=tmp_path / "manifests",
        )
    )

    assert result["files_summarized"] == 1
    assert len(agent.calls) == 5
    assert agent.max_in_flight == 3


def test_oversized_file_summary_preserves_part_order_despite_concurrent_completion(
    tmp_path, monkeypatch, fake_store
):
    huge_content = "\n".join(f"line_{i} = {i}" for i in range(2000))  # 5 parts
    _write(tmp_path, "big.py", huge_content)
    agent = _OrderInvertingAgent()
    monkeypatch.setattr(enrich, "build_agents", lambda: [agent])

    asyncio.run(
        enrich.enrich_repository(
            tmp_path,
            [tmp_path / "big.py"],
            enabled=True,
            manifests_root=tmp_path / "manifests",
        )
    )

    doc = next(iter(fake_store.docs.values()))
    positions = [doc.page_content.index(f"summary-part-{n}") for n in range(1, 6)]
    # Original part order (1..5) must be preserved in the combined summary
    # even though completion order was deliberately reversed (part 5
    # finished first, part 1 last).
    assert positions == sorted(positions)


def test_llm_call_concurrency_is_shared_across_files_not_multiplied(tmp_path, monkeypatch, fake_store):
    # Two oversized files, each split into several parts. Total concurrent
    # LLM calls across BOTH files combined must stay bounded by
    # max_concurrency -- not max_concurrency-per-file (which would let
    # concurrency multiply with how many files happen to be enriching at
    # once).
    huge_content = "\n".join(f"line_{i} = {i}" for i in range(1600))  # 4 parts each
    _write(tmp_path, "big_a.py", huge_content)
    _write(tmp_path, "big_b.py", huge_content)
    agent = _ConcurrencyTrackingAgent(delay=0.05)
    monkeypatch.setattr(enrich, "build_agents", lambda: [agent])

    result = asyncio.run(
        enrich.enrich_repository(
            tmp_path,
            [tmp_path / "big_a.py", tmp_path / "big_b.py"],
            enabled=True,
            max_concurrency=3,
            manifests_root=tmp_path / "manifests",
        )
    )

    assert result["files_summarized"] == 2
    assert len(agent.calls) == 8
    assert agent.max_in_flight == 3


def test_resumed_run_eta_is_not_inflated_by_free_resumed_parts(tmp_path, monkeypatch, fake_store):
    # Regression test for a real bug: a resumed multi-part file's ETA was
    # computed from *all* done parts (including ones loaded instantly from
    # part_progress, costing this run zero time), wildly overstating the
    # observed rate and understating time remaining. Pre-populate 3 of 5
    # parts as already-done (as if resumed from a prior interrupted run),
    # then confirm the rate/ETA reflects only the 2 genuinely-timed parts
    # processed this run.
    content = _huge_content()
    _write(tmp_path, "big.py", content)
    manifests_root = tmp_path / "manifests"
    progress_root = tmp_path / "part-progress"
    digest = manifest.compute_hash(tmp_path / "big.py")
    part_progress.save(
        progress_root,
        tmp_path,
        "big.py",
        digest,
        5,
        {0: "old summary 0", 1: "old summary 1", 2: "old summary 2"},
    )

    agent = _StubAgent("new summary")
    monkeypatch.setattr(enrich, "build_agents", lambda: [agent])

    # No real yield points anywhere in the stub agent/fake store means
    # asyncio.gather runs the 2 pending parts in strict creation order here
    # (same determinism this file's existing ETA test already relies on) --
    # so the exact sequence of _monotonic() calls is knowable: run_started_at,
    # then one per _report_progress tick (pre-work, part 3 done, part 4 done,
    # file fully finalized) = 5 total.
    fake_clock = iter([0, 2, 12, 22, 22])
    monkeypatch.setattr(enrich, "_monotonic", lambda: next(fake_clock))

    etas: list[float | None] = []

    async def progress_callback(done, total, *, phase, partial_result):
        etas.append(partial_result["enrichment_eta_seconds"])

    result = asyncio.run(
        enrich.enrich_repository(
            tmp_path,
            [tmp_path / "big.py"],
            enabled=True,
            manifests_root=manifests_root,
            progress_root=progress_root,
            progress_callback=progress_callback,
        )
    )

    assert len(agent.calls) == 2  # only the 2 missing parts, not all 5
    assert result["files_summarized"] == 1
    # Before any genuinely-new part completes this run: unknown, not a
    # falsely-optimistic number computed from the 3 free resumed parts (the
    # old bug would have reported 1 here, not None).
    assert etas[0] is None
    # After 1 of 2 new parts completes (elapsed 12s): rate = 1 part / 12s,
    # 1 part remaining -> 12s. The old bug (rate from all 4 done parts over
    # 12s) would have wrongly reported 3.
    assert etas[1] == 12
    # Both new parts done: nothing left.
    assert etas[2] == 0
    assert etas[3] == 0
