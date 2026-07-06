"""Covers codemind/qa.py, ported from com.jslogicextractor.qa.ExtractionQaService.

Chat calls are mocked via monkeypatching qa._get_qa_chat(); the embedding
client is mocked via monkeypatching qa.OllamaEmbeddings so the vector-search
path can be exercised without a running Ollama daemon, mirroring the Java
suite's mocked ChatClient/EmbeddingModel.
"""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from config import settings

from codemind import extraction_stats, qa
from codemind.agents.base import ExtractionResult


def _write_result(directory: Path, file_name: str, relative_path: str | None, content: str | None) -> None:
    if relative_path is None:
        payload = {}
    else:
        payload = ExtractionResult(relative_path, "test-agent", True, False, content, None, 1, None, None).to_dict()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / file_name).write_text(json.dumps(payload), encoding="utf-8")


class _FakeChat:
    def __init__(self, content: str | None = None, chunks: list[str] | None = None) -> None:
        self._content = content
        self._chunks = chunks or []

    async def ainvoke(self, messages):
        return SimpleNamespace(content=self._content)

    async def astream(self, messages):
        for chunk in self._chunks:
            yield SimpleNamespace(content=chunk)


class _FakeEmbeddings:
    """auth.js's content mentions "password" -> embeds as [1, 0]; everything
    else (payments.js, and the query in these tests) embeds as [0, 1] or
    [1, 0] as set up per-test, so cosine similarity ranks auth.js first."""

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if "password" in text else [0.0, 1.0] for text in texts]

    async def aembed_query(self, text: str) -> list[float]:
        return [1.0, 0.0]


class _FailingEmbeddings:
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("ollama unreachable")

    async def aembed_query(self, text: str) -> list[float]:
        raise RuntimeError("ollama unreachable")


def _collect(stream) -> str:
    async def _run():
        return "".join([chunk async for chunk in stream])

    return asyncio.run(_run())


def test_answers_using_top_scoring_files_as_context_via_keyword_fallback(tmp_path, monkeypatch):
    _write_result(tmp_path, "auth.js.json", "auth.js", "Checks password and creates session for login users.")
    _write_result(tmp_path, "payments.js.json", "payments.js", "Charges a credit card via the Stripe API.")
    _write_result(tmp_path, "_summary.json", None, None)

    monkeypatch.setattr(qa, "_get_qa_chat", lambda: _FakeChat(content="It checks the password and creates a session."))

    answer = asyncio.run(qa.ask(tmp_path, "how does login check the password and session work?"))

    # Only 1 of the 2 real (non-_summary.json) results scored high enough to
    # be shown -- the answer must disclose that rather than read as complete.
    assert answer.answer == "(Showing the 1 of 2 extracted files most relevant to this question -- " \
        "not an exhaustive count or full listing.)\n\nIt checks the password and creates a session."
    assert answer.source_files == ["auth.js"]


def test_answers_using_vector_search_when_embedding_model_is_present(tmp_path, monkeypatch):
    _write_result(tmp_path, "auth.js.json", "auth.js", "Checks password and creates session for login users.")
    _write_result(tmp_path, "payments.js.json", "payments.js", "Charges a credit card via the Stripe API.")

    monkeypatch.setattr(settings, "CODEMIND_EMBEDDING_ENABLED", True)
    monkeypatch.setattr(qa, "OllamaEmbeddings", lambda **kwargs: _FakeEmbeddings())
    monkeypatch.setattr(qa, "_get_qa_chat", lambda: _FakeChat(content="Vector-grounded answer."))

    answer = asyncio.run(qa.ask(tmp_path, "how does login work?"))

    # Vector search's ranked list covers all loaded results here (2 files,
    # both under _TOP_K) -- no scope note needed since nothing was left out.
    assert answer.answer == "Vector-grounded answer."
    assert answer.source_files[0] == "auth.js"


def test_no_scope_note_when_every_result_is_shown(tmp_path, monkeypatch):
    _write_result(tmp_path, "auth.js.json", "auth.js", "Checks password and creates session.")

    monkeypatch.setattr(qa, "_get_qa_chat", lambda: _FakeChat(content="It checks the password."))

    answer = asyncio.run(qa.ask(tmp_path, "how does login check the password?"))

    assert answer.answer == "It checks the password."


def test_scope_note_prefixes_the_stream_when_not_every_result_is_shown(tmp_path, monkeypatch):
    _write_result(tmp_path, "auth.js.json", "auth.js", "Checks password and creates session for login users.")
    _write_result(tmp_path, "payments.js.json", "payments.js", "Charges a credit card via the Stripe API.")

    monkeypatch.setattr(qa, "_get_qa_chat", lambda: _FakeChat(chunks=["It ", "checks ", "the ", "password."]))

    result = asyncio.run(qa.ask_for_stream([tmp_path], "how does login check the password and session work?"))

    assert _collect(result.text_stream) == (
        "(Showing the 1 of 2 extracted files most relevant to this question -- "
        "not an exhaustive count or full listing.)\n\nIt checks the password."
    )


def test_falls_back_to_keyword_search_when_embedding_call_fails(tmp_path, monkeypatch):
    _write_result(tmp_path, "auth.js.json", "auth.js", "Checks password and creates session for login users.")
    _write_result(tmp_path, "payments.js.json", "payments.js", "Charges a credit card via the Stripe API.")

    monkeypatch.setattr(settings, "CODEMIND_EMBEDDING_ENABLED", True)
    monkeypatch.setattr(qa, "OllamaEmbeddings", lambda **kwargs: _FailingEmbeddings())
    monkeypatch.setattr(qa, "_get_qa_chat", lambda: _FakeChat(content="It checks the password and creates a session."))

    answer = asyncio.run(qa.ask(tmp_path, "how does login check the password and session work?"))

    assert answer.answer == "(Showing the 1 of 2 extracted files most relevant to this question -- " \
        "not an exhaustive count or full listing.)\n\nIt checks the password and creates a session."
    assert answer.source_files == ["auth.js"]


def test_returns_placeholder_when_no_results_exist_yet(tmp_path):
    answer = asyncio.run(qa.ask(tmp_path / "missing", "anything?"))

    assert answer.source_files == []
    assert "No extraction results" in answer.answer


def test_returns_placeholder_when_nothing_matches_the_question(tmp_path):
    _write_result(tmp_path, "payments.js.json", "payments.js", "Charges a credit card via the Stripe API.")

    answer = asyncio.run(qa.ask(tmp_path, "xyzxyz nonsense qqq"))

    assert answer.source_files == []
    assert "None of the" in answer.answer


def test_ask_for_stream_returns_source_files_and_text_stream_via_keyword_fallback(tmp_path, monkeypatch):
    _write_result(tmp_path, "auth.js.json", "auth.js", "Checks password and creates session for login users.")

    monkeypatch.setattr(qa, "_get_qa_chat", lambda: _FakeChat(chunks=["It ", "checks ", "the ", "password."]))

    result = asyncio.run(qa.ask_for_stream([tmp_path], "how does login work?"))

    assert result.source_files == ["auth.js"]
    assert _collect(result.text_stream) == "It checks the password."


def test_ask_for_stream_returns_fallback_stream_when_no_results_exist(tmp_path):
    result = asyncio.run(qa.ask_for_stream([tmp_path / "missing"], "anything?"))

    assert result.source_files == []
    assert "No extraction results" in _collect(result.text_stream)


def test_generic_mode_returns_deterministic_report_without_any_llm_or_embedding_call(tmp_path, monkeypatch):
    _write_result(tmp_path, "auth.js.json", "auth.js", '{"rules": [{"name": "a"}, {"name": "b"}]}')
    _write_result(tmp_path, "payments.js.json", "payments.js", '{"rules": [{"name": "c"}]}')

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("generic mode must never call the chat model")

    monkeypatch.setattr(qa, "_get_qa_chat", _fail_if_called)
    monkeypatch.setattr(qa, "OllamaEmbeddings", _fail_if_called)

    result = asyncio.run(qa.ask_for_stream([tmp_path], "how many functions do you have?", mode="generic"))

    assert result.source_files == []
    expected = extraction_stats.format_report(extraction_stats.compute_stats(tmp_path))
    assert _collect(result.text_stream) == expected
    assert "Total extracted rules across all files: 3" in expected


def test_generic_mode_ignores_question_text(tmp_path, monkeypatch):
    _write_result(tmp_path, "auth.js.json", "auth.js", '{"rules": [{"name": "a"}]}')
    monkeypatch.setattr(qa, "_get_qa_chat", lambda: _FakeChat(content="should never be reached"))

    first = asyncio.run(qa.ask_for_stream([tmp_path], "how many functions?", mode="generic"))
    second = asyncio.run(qa.ask_for_stream([tmp_path], "something totally different", mode="generic"))

    assert _collect(first.text_stream) == _collect(second.text_stream)


def test_generic_mode_returns_no_results_placeholder_when_job_has_nothing(tmp_path):
    result = asyncio.run(qa.ask_for_stream([tmp_path / "missing"], "anything?", mode="generic"))

    assert result.source_files == []
    assert "No extraction results" in _collect(result.text_stream)
