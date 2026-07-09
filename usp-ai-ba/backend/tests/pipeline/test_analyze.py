"""Covers pipeline/nodes/analyze.py's analyze_node -- SDD text extraction
(now dispatching PDF/DOCX via ingestion.ingest_documents' shared helper
instead of a private, duplicated PDF-only reader) and the pasted-text
skip-extraction path (no file at all, see api/routers/assess.py's
submit_assessment)."""
from __future__ import annotations

import asyncio

import docx
import pytest

from pipeline.nodes import analyze
from pipeline.state import new_state


def _write_docx(path, paragraphs: list[str]) -> None:
    document = docx.Document()
    for text in paragraphs:
        document.add_paragraph(text)
    document.save(str(path))


def _empty_context() -> dict:
    return {"manuals": [], "codebase": [], "entities": []}


@pytest.fixture(autouse=True)
def _stub_retrieval(monkeypatch):
    async def fake_retrieve_all_collections(query_text: str):
        return _empty_context()

    monkeypatch.setattr(analyze, "retrieve_all_collections", fake_retrieve_all_collections)


def test_analyze_node_extracts_docx_via_shared_dispatcher(tmp_path):
    docx_path = tmp_path / "sdd.docx"
    _write_docx(docx_path, ["The system must validate the user's eligibility."])

    state = new_state(
        job_id="job-1",
        ppm_number="1",
        ppm_name="A",
        system_name="S",
        solution_doc_path=str(docx_path),
        review_mode=False,
        output_mode="document",
    )

    result = asyncio.run(analyze.analyze_node(state))

    assert "eligibility" in result["solution_doc_text"]
    assert result["status"] == "analyzing"


def test_analyze_node_pasted_text_skips_extraction_entirely(monkeypatch):
    def fail_if_called(path):
        raise AssertionError("no file to extract from a pasted-text submission")

    monkeypatch.setattr(analyze.ingest_documents, "_extract_text", fail_if_called)

    state = new_state(
        job_id="job-2",
        ppm_number="1",
        ppm_name="A",
        system_name="S",
        solution_doc_path="",
        review_mode=False,
        output_mode="document",
        solution_doc_text="The pasted SDD text, verbatim.",
    )

    result = asyncio.run(analyze.analyze_node(state))

    assert result["solution_doc_text"] == "The pasted SDD text, verbatim."
    assert result["status"] == "analyzing"
