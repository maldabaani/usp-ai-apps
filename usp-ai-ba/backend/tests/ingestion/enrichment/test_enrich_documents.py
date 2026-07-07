"""Covers ingestion/enrichment/enrich_documents.py -- the optional LLM-summary
enrichment tier for document ingestion (tier 2). Mirrors
ingestion/enrichment/enrich.py's own test fixture conventions (stub agents,
fake vector store) but adapted for documents: text is read via a monkeypatched
_extract_text (standing in for real PDF/DOCX parsing) rather than plain
path.read_text(), and summaries are written into "manuals", not "codebase".
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid
from pathlib import Path

import pytest

from config import settings
from ingestion import chroma_client
from ingestion.enrichment import doc_prompts, enrich_documents
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
    to extract, so tests can assert incremental-skip / oversized-document
    batching behavior via call count."""

    def __init__(self, content: str = "if the account balance is negative, block further withdrawals"):
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
    def name(self) -> str:
        return "raising-agent"

    async def extract(self, file) -> ExtractionResult:
        raise RuntimeError("credit balance too low")


def _write(repo, relative: str, content: bytes = b"placeholder bytes") -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _stub_extract_text(monkeypatch, text_by_path: dict[str, str]) -> None:
    def fake_extract_text(path):
        return text_by_path[path.name]

    monkeypatch.setattr(enrich_documents, "_extract_text", fake_extract_text)


def test_disabled_is_a_pure_no_op(tmp_path, monkeypatch, fake_store):
    def fail_if_called(**kwargs):
        raise AssertionError("build_agents() must not be called when enrichment is disabled")

    monkeypatch.setattr(enrich_documents, "build_agents", fail_if_called)

    result = asyncio.run(enrich_documents.enrich_documents(tmp_path, [], enabled=False))

    assert result == {
        "enabled": False,
        "files_summarized": 0,
        "files_skipped_unchanged": 0,
        "errors": [],
        "files": [],
    }


def test_disabled_marks_every_file_skipped_with_reason(tmp_path, monkeypatch, fake_store):
    _write(tmp_path, "manual.pdf")

    result = asyncio.run(enrich_documents.enrich_documents(tmp_path, [tmp_path / "manual.pdf"], enabled=False))

    assert result["files"] == [{"path": "manual.pdf", "status": "skipped", "reason": "llm_summary_disabled"}]


def test_skips_gracefully_when_no_agents_configured(tmp_path, monkeypatch, fake_store):
    monkeypatch.setattr(enrich_documents, "build_agents", lambda **kwargs: [])

    result = asyncio.run(enrich_documents.enrich_documents(tmp_path, [], enabled=True))

    assert result["enabled"] is False
    assert result["errors"] == []


def test_no_agents_configured_marks_every_file_skipped_with_reason(tmp_path, monkeypatch, fake_store):
    _write(tmp_path, "manual.pdf")
    monkeypatch.setattr(enrich_documents, "build_agents", lambda **kwargs: [])

    result = asyncio.run(enrich_documents.enrich_documents(tmp_path, [tmp_path / "manual.pdf"], enabled=True))

    assert result["files"] == [{"path": "manual.pdf", "status": "skipped", "reason": "no_agents_configured"}]


def test_summarizes_eligible_document_and_writes_llm_summary_document(tmp_path, monkeypatch, fake_store):
    _write(tmp_path, "policy.pdf")
    _stub_extract_text(monkeypatch, {"policy.pdf": "Refunds are issued within 30 days of purchase."})
    agent = _StubAgent("if a request is made within 30 days, a refund is issued")
    monkeypatch.setattr(enrich_documents, "build_agents", lambda **kwargs: [agent])

    result = asyncio.run(
        enrich_documents.enrich_documents(
            tmp_path, [tmp_path / "policy.pdf"], enabled=True, manifests_root=tmp_path / "manifests"
        )
    )

    assert result["enabled"] is True
    assert result["files_summarized"] == 1
    assert agent.calls == ["policy.pdf"]
    assert result["files"] == [{"path": "policy.pdf", "status": "summarized"}]
    docs = list(fake_store.docs.values())
    assert len(docs) == 1
    assert docs[0].metadata["type"] == "llm_summary"
    assert docs[0].metadata["source"] == "policy.pdf"
    assert docs[0].metadata["format"] == "pdf"
    assert "ingested_at" in docs[0].metadata
    # No code-only placeholder fields -- documents have no module/language/
    # layer/class_name concept, so none should be carried into new code.
    assert "module" not in docs[0].metadata
    assert "language" not in docs[0].metadata
    assert "layer" not in docs[0].metadata
    assert "class_name" not in docs[0].metadata
    assert "refund" in docs[0].page_content


def test_empty_extracted_text_is_skipped(tmp_path, monkeypatch, fake_store):
    _write(tmp_path, "blank.pdf")
    _stub_extract_text(monkeypatch, {"blank.pdf": "   \n  "})
    agent = _StubAgent()
    monkeypatch.setattr(enrich_documents, "build_agents", lambda **kwargs: [agent])

    result = asyncio.run(
        enrich_documents.enrich_documents(
            tmp_path, [tmp_path / "blank.pdf"], enabled=True, manifests_root=tmp_path / "manifests"
        )
    )

    assert result["files_summarized"] == 0
    assert agent.calls == []
    assert result["files"] == [{"path": "blank.pdf", "status": "skipped", "reason": "no_content_extracted"}]


def test_failed_extraction_is_not_written_and_recorded_in_errors_only_on_exception(tmp_path, monkeypatch, fake_store):
    _write(tmp_path, "manual.pdf")
    _stub_extract_text(monkeypatch, {"manual.pdf": "Some manual content."})
    monkeypatch.setattr(enrich_documents, "build_agents", lambda **kwargs: [_FailingAgent()])

    result = asyncio.run(
        enrich_documents.enrich_documents(
            tmp_path, [tmp_path / "manual.pdf"], enabled=True, manifests_root=tmp_path / "manifests"
        )
    )

    assert result["files_summarized"] == 0
    assert result["errors"] == []
    assert len(fake_store.docs) == 0
    assert result["files"] == [{"path": "manual.pdf", "status": "skipped", "reason": "no_summary_produced"}]


def test_raised_exception_is_recorded_with_error_status_and_message(tmp_path, monkeypatch, fake_store):
    _write(tmp_path, "manual.pdf")
    _stub_extract_text(monkeypatch, {"manual.pdf": "Some manual content."})
    monkeypatch.setattr(enrich_documents, "build_agents", lambda **kwargs: [_RaisingAgent()])

    result = asyncio.run(
        enrich_documents.enrich_documents(
            tmp_path, [tmp_path / "manual.pdf"], enabled=True, manifests_root=tmp_path / "manifests"
        )
    )

    assert result["errors"] == ["manual.pdf: credit balance too low"]
    assert result["files"] == [{"path": "manual.pdf", "status": "error", "reason": "credit balance too low"}]


def test_failed_extraction_is_retried_on_next_run_not_silently_skipped(tmp_path, monkeypatch, fake_store):
    """Regression test mirroring plan file section K's fix for enrich.py:
    a failed enrichment attempt must not be recorded in the manifest as
    "seen", or a transient failure would be silently and permanently treated
    as done."""
    _write(tmp_path, "manual.pdf")
    _stub_extract_text(monkeypatch, {"manual.pdf": "Some manual content."})
    monkeypatch.setattr(enrich_documents, "build_agents", lambda **kwargs: [_FailingAgent()])
    manifests_root = tmp_path / "manifests"

    asyncio.run(
        enrich_documents.enrich_documents(
            tmp_path, [tmp_path / "manual.pdf"], enabled=True, manifests_root=manifests_root
        )
    )

    agent = _StubAgent()
    monkeypatch.setattr(enrich_documents, "build_agents", lambda **kwargs: [agent])
    result = asyncio.run(
        enrich_documents.enrich_documents(
            tmp_path, [tmp_path / "manual.pdf"], enabled=True, manifests_root=manifests_root
        )
    )

    assert agent.calls == ["manual.pdf"]  # retried, not skipped as "unchanged"
    assert result["files_summarized"] == 1
    assert result["files_skipped_unchanged"] == 0


def test_incremental_skip_of_unchanged_document(tmp_path, monkeypatch, fake_store):
    _write(tmp_path, "manual.pdf")
    _stub_extract_text(monkeypatch, {"manual.pdf": "Some manual content."})
    agent = _StubAgent()
    monkeypatch.setattr(enrich_documents, "build_agents", lambda **kwargs: [agent])
    manifests_root = tmp_path / "manifests"

    asyncio.run(
        enrich_documents.enrich_documents(
            tmp_path, [tmp_path / "manual.pdf"], enabled=True, manifests_root=manifests_root
        )
    )
    assert agent.calls == ["manual.pdf"]

    result = asyncio.run(
        enrich_documents.enrich_documents(
            tmp_path, [tmp_path / "manual.pdf"], enabled=True, manifests_root=manifests_root
        )
    )

    assert agent.calls == ["manual.pdf"]  # not called again
    assert result["files_skipped_unchanged"] == 1
    assert result["files_summarized"] == 0
    assert result["files"] == [{"path": "manual.pdf", "status": "skipped", "reason": "unchanged_since_last_run"}]


def test_changed_document_content_is_resummarized(tmp_path, monkeypatch, fake_store):
    _write(tmp_path, "manual.pdf", b"version one bytes")
    _stub_extract_text(monkeypatch, {"manual.pdf": "Some manual content."})
    agent = _StubAgent()
    monkeypatch.setattr(enrich_documents, "build_agents", lambda **kwargs: [agent])
    manifests_root = tmp_path / "manifests"

    asyncio.run(
        enrich_documents.enrich_documents(
            tmp_path, [tmp_path / "manual.pdf"], enabled=True, manifests_root=manifests_root
        )
    )

    _write(tmp_path, "manual.pdf", b"version two bytes, different content")
    result = asyncio.run(
        enrich_documents.enrich_documents(
            tmp_path, [tmp_path / "manual.pdf"], enabled=True, manifests_root=manifests_root
        )
    )

    assert agent.calls == ["manual.pdf", "manual.pdf"]
    assert result["files_summarized"] == 1


def test_progress_callback_fires_with_enrichment_phase(tmp_path, monkeypatch, fake_store):
    _write(tmp_path, "a.pdf")
    _write(tmp_path, "b.pdf")
    _stub_extract_text(monkeypatch, {"a.pdf": "Content A.", "b.pdf": "Content B."})
    agent = _StubAgent()
    monkeypatch.setattr(enrich_documents, "build_agents", lambda **kwargs: [agent])

    calls: list[tuple] = []

    async def progress_callback(done, total, *, phase, partial_result):
        calls.append((done, total, phase, partial_result))

    result = asyncio.run(
        enrich_documents.enrich_documents(
            tmp_path,
            [tmp_path / "a.pdf", tmp_path / "b.pdf"],
            enabled=True,
            manifests_root=tmp_path / "manifests",
            progress_callback=progress_callback,
        )
    )

    assert len(calls) == 2
    assert all(phase == "enrichment" for _done, _total, phase, _partial in calls)
    assert all(total == 2 for _done, total, _phase, _partial in calls)
    last_partial = calls[-1][3]
    assert {f["path"] for f in last_partial["enrichment_files"]} == {"a.pdf", "b.pdf"}
    assert {f["path"] for f in result["files"]} == {"a.pdf", "b.pdf"}


def test_oversized_document_is_split_and_summaries_joined(tmp_path, monkeypatch, fake_store):
    huge_text = "\n\n".join(f"Paragraph {i} of the manual." for i in range(2000))
    assert len(huge_text) > enrich_documents.MAX_CHARS_BEFORE_SPLITTING
    _write(tmp_path, "big.pdf")
    _stub_extract_text(monkeypatch, {"big.pdf": huge_text})
    agent = _StubAgent("part summary")
    monkeypatch.setattr(enrich_documents, "build_agents", lambda **kwargs: [agent])

    result = asyncio.run(
        enrich_documents.enrich_documents(
            tmp_path, [tmp_path / "big.pdf"], enabled=True, manifests_root=tmp_path / "manifests"
        )
    )

    assert result["files_summarized"] == 1
    assert len(agent.calls) > 1  # split into multiple parts
    doc = next(iter(fake_store.docs.values()))
    assert doc.page_content.count("part summary") == len(agent.calls)


def test_default_manifests_root_uses_document_enrichment_namespace(tmp_path, monkeypatch, fake_store):
    monkeypatch.setattr(settings, "JOBS_DIR", str(tmp_path / "jobs"))
    _write(tmp_path, "manual.pdf")
    _stub_extract_text(monkeypatch, {"manual.pdf": "Some manual content."})
    agent = _StubAgent()
    monkeypatch.setattr(enrich_documents, "build_agents", lambda **kwargs: [agent])

    asyncio.run(enrich_documents.enrich_documents(tmp_path, [tmp_path / "manual.pdf"], enabled=True))

    digest = hashlib.sha256(str(tmp_path.absolute()).encode("utf-8")).hexdigest()
    expected_manifest = Path(settings.JOBS_DIR) / ".document-enrichment-manifests" / f"{digest}.json"
    assert expected_manifest.exists()
    stale_manifest = Path(settings.JOBS_DIR) / ".enrichment-manifests" / f"{digest}.json"
    assert not stale_manifest.exists()


def test_build_agents_is_called_with_document_prompt_builder(tmp_path, monkeypatch, fake_store):
    _write(tmp_path, "manual.pdf")
    _stub_extract_text(monkeypatch, {"manual.pdf": "Some manual content."})
    agent = _StubAgent()
    captured_kwargs = {}

    def fake_build_agents(**kwargs):
        captured_kwargs.update(kwargs)
        return [agent]

    monkeypatch.setattr(enrich_documents, "build_agents", fake_build_agents)

    asyncio.run(
        enrich_documents.enrich_documents(
            tmp_path, [tmp_path / "manual.pdf"], enabled=True, manifests_root=tmp_path / "manifests"
        )
    )

    assert captured_kwargs.get("build_messages") is doc_prompts.build_extraction_messages
