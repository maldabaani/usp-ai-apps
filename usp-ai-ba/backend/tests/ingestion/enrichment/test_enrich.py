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


def _write(repo, relative: str, content: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_disabled_is_a_pure_no_op(tmp_path, monkeypatch, fake_store):
    def fail_if_called():
        raise AssertionError("build_agents() must not be called when enrichment is disabled")

    monkeypatch.setattr(enrich, "build_agents", fail_if_called)

    result = asyncio.run(enrich.enrich_repository(tmp_path, [], enabled=False))

    assert result == {"enabled": False, "files_summarized": 0, "files_skipped_unchanged": 0, "errors": []}


def test_skips_gracefully_when_no_agents_configured(tmp_path, monkeypatch, fake_store):
    monkeypatch.setattr(enrich, "build_agents", lambda: [])

    result = asyncio.run(enrich.enrich_repository(tmp_path, [], enabled=True))

    assert result["enabled"] is False
    assert result["errors"] == []


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


def test_failed_extraction_is_not_written_and_recorded_in_errors_only_on_exception(tmp_path, monkeypatch, fake_store):
    _write(tmp_path, "app.py", "def handler(): pass\n")
    monkeypatch.setattr(enrich, "build_agents", lambda: [_FailingAgent()])

    result = asyncio.run(
        enrich.enrich_repository(tmp_path, [tmp_path / "app.py"], enabled=True, manifests_root=tmp_path / "manifests")
    )

    assert result["files_summarized"] == 0
    assert result["errors"] == []
    assert len(fake_store.docs) == 0


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
