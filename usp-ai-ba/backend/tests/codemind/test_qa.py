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

from codemind import extraction_stats, output, qa
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


class _CountingFakeChat:
    """A chat fake whose ainvoke() returns a different fixed response per
    call (in order), so a test can assert both call count and that a later
    (e.g. "combine") step's output is what actually got cached, not an
    earlier batch's raw text."""

    def __init__(self, ainvoke_responses: list[str], stream_chunks: list[str] | None = None) -> None:
        self._responses = list(ainvoke_responses)
        self.ainvoke_call_count = 0
        self._chunks = stream_chunks or []

    async def ainvoke(self, messages):
        response = self._responses[self.ainvoke_call_count]
        self.ainvoke_call_count += 1
        return SimpleNamespace(content=response)

    async def astream(self, messages):
        for chunk in self._chunks:
            yield SimpleNamespace(content=chunk)


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


def test_comprehensive_mode_cache_hit_skips_synthesis_and_answers_from_cache(tmp_path, monkeypatch):
    output.write_comprehensive_summary(tmp_path, {"summary": "Cached whole-job overview.", "fileCount": 5})

    def _fail_if_ainvoke_called(*args, **kwargs):
        raise AssertionError("cache hit must not rebuild the synthesis (ainvoke should never be called)")

    fake = _CountingFakeChat(ainvoke_responses=[], stream_chunks=["It ", "uses ", "the ", "cached ", "overview."])
    monkeypatch.setattr(fake, "ainvoke", _fail_if_ainvoke_called)
    monkeypatch.setattr(qa, "_get_qa_chat", lambda: fake)

    result = asyncio.run(qa.ask_for_stream([tmp_path], "explain this codebase", mode="comprehensive"))

    assert result.source_files == []
    assert _collect(result.text_stream) == "It uses the cached overview."


def test_comprehensive_mode_cache_miss_single_shot_builds_and_caches(tmp_path, monkeypatch):
    _write_result(tmp_path, "auth.js.json", "auth.js", "Checks password and creates session.")
    _write_result(tmp_path, "payments.js.json", "payments.js", "Charges a credit card.")

    fake = _CountingFakeChat(ainvoke_responses=["Built overview."], stream_chunks=["Final ", "answer."])
    monkeypatch.setattr(qa, "_get_qa_chat", lambda: fake)

    result = asyncio.run(qa.ask_for_stream([tmp_path], "explain this codebase", mode="comprehensive"))

    assert _collect(result.text_stream) == "Final answer."
    assert fake.ainvoke_call_count == 1  # one synthesis call, no batching needed

    cached = output.read_comprehensive_summary(tmp_path)
    assert cached["summary"] == "Built overview."
    assert cached["fileCount"] == 2
    assert "generatedAt" in cached


def test_comprehensive_mode_cache_miss_batched_reduce_when_over_batch_size(tmp_path, monkeypatch):
    batch_size = qa._COMPREHENSIVE_BATCH_SIZE
    for i in range(batch_size + 5):  # forces 2 batches (20 + 5) + 1 combine call
        _write_result(tmp_path, f"file{i}.js.json", f"file{i}.js", f"Logic for file {i}.")

    fake = _CountingFakeChat(
        ainvoke_responses=["Batch 1 overview.", "Batch 2 overview.", "Combined overview."],
        stream_chunks=["Answer."],
    )
    monkeypatch.setattr(qa, "_get_qa_chat", lambda: fake)

    result = asyncio.run(qa.ask_for_stream([tmp_path], "explain this codebase", mode="comprehensive"))

    assert _collect(result.text_stream) == "Answer."
    assert fake.ainvoke_call_count == 3  # 2 batch calls + 1 final combine call

    cached = output.read_comprehensive_summary(tmp_path)
    assert cached["summary"] == "Combined overview."  # the combine step's output, not a raw batch
    assert cached["fileCount"] == batch_size + 5


def test_comprehensive_mode_returns_no_results_placeholder_when_job_has_nothing(tmp_path):
    result = asyncio.run(qa.ask_for_stream([tmp_path / "missing"], "anything?", mode="comprehensive"))

    assert result.source_files == []
    assert "No extraction results" in _collect(result.text_stream)


def test_comprehensive_mode_source_files_is_empty(tmp_path, monkeypatch):
    _write_result(tmp_path, "auth.js.json", "auth.js", "Checks password.")
    fake = _CountingFakeChat(ainvoke_responses=["Overview."], stream_chunks=["Answer."])
    monkeypatch.setattr(qa, "_get_qa_chat", lambda: fake)

    result = asyncio.run(qa.ask_for_stream([tmp_path], "explain this codebase", mode="comprehensive"))

    assert result.source_files == []


def test_get_qa_chat_builds_ollama_client_with_zero_temperature_for_determinism(monkeypatch):
    # temperature=0 reduces (not eliminates) small imprecisions like a
    # misspelled file path in a synthesized answer -- confirmed live this
    # session (claude_agent.py cited as "claud_agent.py").
    monkeypatch.setattr(settings, "CODEMIND_QA_MODEL", "ollama")
    monkeypatch.setattr(qa, "_qa_chat", None)
    monkeypatch.setattr(qa, "_qa_chat_generation", -1)
    monkeypatch.setattr(qa, "_qa_chat_model_kind", None)

    chat = qa._get_qa_chat()

    assert chat.temperature == 0
    assert chat.num_ctx == settings.OLLAMA_NUM_CTX


def test_get_qa_chat_builds_claude_client_with_zero_temperature_for_determinism(monkeypatch):
    monkeypatch.setattr(settings, "CODEMIND_QA_MODEL", "claude")
    monkeypatch.setattr(qa, "_qa_chat", None)
    monkeypatch.setattr(qa, "_qa_chat_generation", -1)
    monkeypatch.setattr(qa, "_qa_chat_model_kind", None)

    chat = qa._get_qa_chat()

    assert chat.temperature == 0


def test_build_context_accepts_plain_extraction_results():
    results = [
        ExtractionResult("auth.js", "test-agent", True, False, "Checks password.", None, 1, None, None),
        ExtractionResult("payments.js", "test-agent", True, False, "Charges a card.", None, 1, None, None),
    ]

    context = qa._build_context(results)

    assert "File: auth.js\nChecks password." in context
    assert "File: payments.js\nCharges a card." in context
