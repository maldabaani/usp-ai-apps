"""RAG over a job's already-written extraction results.

Ported from com.jslogicextractor.qa.ExtractionQaService. Retrieves the files
most relevant to the question, feeds them to a chat model as grounded
context, and returns the answer plus the source files it drew from.

Retrieval is real vector search (embeddings + cosine similarity, computed
fresh per query and discarded -- NOT persisted, unlike ingestion/
chroma_client.py's persistent collections, since that would be a real
behavior change from Java's ephemeral-per-query SimpleVectorStore) when
CODEMIND_EMBEDDING_ENABLED is set; otherwise, or if an embedding call fails
(e.g. the local Ollama daemon is unreachable), falls back to keyword-overlap
scoring so the endpoint keeps working with zero extra infrastructure.

job_id/output_directory are passed explicitly (a list of output directories
for the cross-job "Ask All" case) rather than threading a job object
through, matching output.py's precedent -- the job type itself belongs to a
later phase (orchestrator.py/job_store.py).
"""
from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Literal

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama, OllamaEmbeddings

from codemind import extraction_stats, output
from codemind.agents.base import ExtractionResult
from config import settings

logger = logging.getLogger(__name__)

_WORD_PATTERN = re.compile(r"[A-Za-z0-9_]+")
_STOPWORDS = {
    "the", "is", "are", "what", "how", "does", "this", "that", "with", "for", "and", "where",
    "which", "who", "why", "when", "did", "can", "could", "would", "should", "to", "of", "in",
    "on", "a", "an", "it", "do", "be", "i", "you", "we",
}
_TOP_K = 6
_MAX_CONTENT_CHARS_PER_FILE = 3000

_SYSTEM_PROMPT_TEMPLATE = (
    "You are answering questions about a codebase using only the extracted logic summaries\n"
    "provided below as context. Each summary is labeled with its source file path. Ground your\n"
    "answer strictly in this context; if the context doesn't contain the answer, say so\n"
    "explicitly rather than guessing. Cite the relevant file path(s) inline when you reference\n"
    "specific logic.\n\n"
    "Context:\n{context}\n"
)

_NO_RESULTS_MESSAGE_SINGLE = (
    "No extraction results are available yet for this job. Wait for files to finish "
    "processing, then ask again."
)
_NO_RESULTS_MESSAGE_MULTI = (
    "No extraction results are available yet. Wait for jobs to finish processing, then ask again."
)

REQUEST_TIMEOUT_SECONDS = 120

# "comprehensive" mode: files per reduce call. Derived from settings.OLLAMA_NUM_CTX's
# default (32768 tokens) * the one chars-per-token precedent in this codebase
# (ingestion/ingest_code.py's CHARS_PER_TOKEN = 4) =~ 131k raw chars; a
# conservative 60k-char working budget (headroom for prompt boilerplate +
# completion tokens) / _MAX_CONTENT_CHARS_PER_FILE = 20 files. Since every
# file's content is already hard-capped at _MAX_CONTENT_CHARS_PER_FILE before
# entering any context string, a file count is enough of a budget -- no
# token-estimation pass needed. Also safe under Claude's much larger window.
_COMPREHENSIVE_BATCH_SIZE = 20
# Ceiling for the joined partial-syntheses text fed into the final combine
# call, so a pathological number of batches degrades gracefully (drops the
# tail) instead of erroring, the same way _build_context already truncates
# individual files.
_COMPREHENSIVE_COMBINE_CHARS_LIMIT = _COMPREHENSIVE_BATCH_SIZE * _MAX_CONTENT_CHARS_PER_FILE

_COMPREHENSIVE_SYNTHESIS_INSTRUCTION = "Produce the comprehensive codebase overview now."
_COMPREHENSIVE_COMBINE_INSTRUCTION = "Combine the partial overviews into one now."

_COMPREHENSIVE_SYSTEM_PROMPT_TEMPLATE = (
    "You are building a comprehensive, question-agnostic overview of an entire codebase from the\n"
    "extracted logic summaries provided below. This overview will be cached and reused to answer\n"
    "many different future questions about this codebase, so do not tailor it to any single\n"
    "question -- cover the overall architecture, key modules/components, notable business rules,\n"
    "and cross-cutting patterns (e.g. error handling, auth, data flow) that appear across multiple\n"
    "files. Cite the relevant file path(s) inline when you reference specific logic.\n\n"
    "Context:\n{context}\n"
)

_COMPREHENSIVE_COMBINE_PROMPT_TEMPLATE = (
    "You are given several partial overviews, each already summarizing a different subset of the\n"
    "same codebase's files. Merge them into a single, coherent, non-redundant whole-codebase\n"
    "overview. Preserve distinct details and file-path citations from each partial overview;\n"
    "do not simply concatenate them -- integrate overlapping points and organize the result as one\n"
    "unified overview.\n\n"
    "Partial overviews:\n{context}\n"
)

_COMPREHENSIVE_ANSWER_SYSTEM_PROMPT_TEMPLATE = (
    "You previously built the comprehensive overview below, covering every file this job\n"
    "extracted (not a sample). Answer the user's question using only this overview as context;\n"
    "if it doesn't contain enough detail to answer confidently, say so explicitly rather than\n"
    "guessing.\n\n"
    "Comprehensive overview:\n{context}\n"
)


@dataclass(frozen=True)
class QaAnswer:
    answer: str
    source_files: list[str]


@dataclass
class QaStreamResult:
    source_files: list[str]
    text_stream: AsyncIterator[str]


@dataclass(frozen=True)
class _ScoredResult:
    result: ExtractionResult
    score: float


_qa_chat: ChatAnthropic | ChatOllama | None = None
_qa_chat_generation = -1
_qa_chat_model_kind: str | None = None


def _get_qa_chat() -> ChatAnthropic | ChatOllama:
    """Rebuilds only when settings.settings_generation has advanced or
    CODEMIND_QA_MODEL itself changed, matching the same generation-counter
    hot-reload pattern as codemind/agents/claude_agent.py and
    pipeline/nodes/generate.py's _get_llm()."""
    global _qa_chat, _qa_chat_generation, _qa_chat_model_kind
    model_kind = settings.CODEMIND_QA_MODEL
    if (
        _qa_chat is None
        or _qa_chat_generation != settings.settings_generation
        or _qa_chat_model_kind != model_kind
    ):
        if model_kind == "ollama":
            _qa_chat = ChatOllama(
                model=settings.CODEMIND_OLLAMA_MODEL,
                base_url=settings.OLLAMA_BASE_URL,
                num_ctx=settings.OLLAMA_NUM_CTX,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        else:
            _qa_chat = ChatAnthropic(
                model=settings.CLAUDE_MODEL,
                api_key=settings.ANTHROPIC_API_KEY,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        _qa_chat_generation = settings.settings_generation
        _qa_chat_model_kind = model_kind
    return _qa_chat


async def ask(output_directory: Path, question: str) -> QaAnswer:
    results = _load_results(output_directory)
    if not results:
        return QaAnswer(_NO_RESULTS_MESSAGE_SINGLE, [])

    ranked = await _retrieve(question, results)
    if not ranked:
        return QaAnswer(_no_match_message(len(results)), [])

    answer = await _call_chat(question, _build_context([s.result for s in ranked]))
    source_files = [scored.result.relative_path for scored in ranked]
    return QaAnswer(_scope_note(len(ranked), len(results)) + answer, source_files)


async def ask_for_stream(
    output_directories: list[Path], question: str, mode: Literal["deep", "generic", "comprehensive"] = "deep"
) -> QaStreamResult:
    if mode == "generic":
        return _generic_stream_result(output_directories)
    if mode == "comprehensive":
        return await _comprehensive_stream_result(output_directories, question)

    results = [result for directory in output_directories for result in _load_results(directory)]
    if not results:
        return QaStreamResult([], _single_chunk_stream(_NO_RESULTS_MESSAGE_MULTI))

    ranked = await _retrieve(question, results)
    if not ranked:
        return QaStreamResult([], _single_chunk_stream(_no_match_message(len(results))))

    source_files = [scored.result.relative_path for scored in ranked]
    stream = _stream_chat(question, _build_context([s.result for s in ranked]))
    note = _scope_note(len(ranked), len(results))
    if note:
        stream = _prefixed_stream(note, stream)
    return QaStreamResult(source_files, stream)


def _generic_stream_result(output_directories: list[Path]) -> QaStreamResult:
    """The "generic" Ask mode: a deterministic, zero-LLM tally of every result
    a job wrote (codemind/extraction_stats.py), ignoring the question text
    entirely -- unlike "deep" mode, this reads every file, not just the top
    _TOP_K, so it can actually answer aggregate/counting questions ("how many
    functions do you have") that deep mode structurally cannot. Job Ask only
    ever passes a single output directory; there is no cross-job "generic"
    mode for Ask All."""
    stats = extraction_stats.compute_stats(output_directories[0])
    if stats.total_files == 0:
        return QaStreamResult([], _single_chunk_stream(_NO_RESULTS_MESSAGE_MULTI))
    return QaStreamResult([], _single_chunk_stream(extraction_stats.format_report(stats)))


async def _comprehensive_stream_result(output_directories: list[Path], question: str) -> QaStreamResult:
    """The "comprehensive" Ask mode: unlike "deep" (top-K sample) and
    "generic" (counts only), this can answer synthesis questions spanning the
    whole job ("explain this codebase," "what security patterns exist here")
    by reducing every extracted file's already-written summary into one
    overview -- built lazily on the first comprehensive-mode question for a
    job, then cached (codemind/output.py's write_comprehensive_summary) and
    reused for every later question, even reworded ones, instead of rebuilt
    per question. Job Ask only ever passes a single output directory; there
    is no cross-job "comprehensive" mode for Ask All.

    Known v1 limitation: no cache invalidation -- if an incremental job is
    re-run later and results change, the cached overview goes stale until the
    output directory is deleted/recreated.
    """
    output_directory = output_directories[0]
    cached = output.read_comprehensive_summary(output_directory)
    synthesis = cached.get("summary") if cached else None

    if not synthesis or not isinstance(synthesis, str) or not synthesis.strip():
        results = _load_results(output_directory)
        if not results:
            return QaStreamResult([], _single_chunk_stream(_NO_RESULTS_MESSAGE_MULTI))
        synthesis = await _build_comprehensive_synthesis(results)
        output.write_comprehensive_summary(
            output_directory,
            {
                "summary": synthesis,
                "generatedAt": datetime.now(timezone.utc).isoformat(),
                "fileCount": len(results),
            },
        )

    stream = _stream_chat(question, synthesis, template=_COMPREHENSIVE_ANSWER_SYSTEM_PROMPT_TEMPLATE)
    return QaStreamResult([], stream)


async def _build_comprehensive_synthesis(results: list[ExtractionResult]) -> str:
    if len(results) <= _COMPREHENSIVE_BATCH_SIZE:
        return await _call_chat(
            _COMPREHENSIVE_SYNTHESIS_INSTRUCTION,
            _build_context(results),
            template=_COMPREHENSIVE_SYSTEM_PROMPT_TEMPLATE,
        )

    partials = []
    for i in range(0, len(results), _COMPREHENSIVE_BATCH_SIZE):
        batch = results[i : i + _COMPREHENSIVE_BATCH_SIZE]
        partial = await _call_chat(
            _COMPREHENSIVE_SYNTHESIS_INSTRUCTION,
            _build_context(batch),
            template=_COMPREHENSIVE_SYSTEM_PROMPT_TEMPLATE,
        )
        partials.append(partial)

    combined_context = "\n\n---\n\n".join(partials)
    if len(combined_context) > _COMPREHENSIVE_COMBINE_CHARS_LIMIT:
        combined_context = combined_context[:_COMPREHENSIVE_COMBINE_CHARS_LIMIT] + "... [truncated]"

    return await _call_chat(
        _COMPREHENSIVE_COMBINE_INSTRUCTION,
        combined_context,
        template=_COMPREHENSIVE_COMBINE_PROMPT_TEMPLATE,
    )


def _no_match_message(result_count: int) -> str:
    return (
        f"None of the {result_count} extracted files were relevant enough to answer "
        "that question confidently."
    )


def _scope_note(shown: int, total: int) -> str:
    """Ask never reasons over the whole codebase -- only the top _TOP_K
    highest-scoring files per question -- so an aggregate/counting question
    ("how many functions...") would otherwise read as a complete answer when
    it's really a partial sample. Prepended deterministically rather than
    left to the model's own judgment, since a prompt instruction to disclose
    this is not reliably followed."""
    if shown >= total:
        return ""
    return (
        f"(Showing the {shown} of {total} extracted files most relevant to this question -- "
        "not an exhaustive count or full listing.)\n\n"
    )


async def _single_chunk_stream(text: str) -> AsyncIterator[str]:
    yield text


async def _prefixed_stream(prefix: str, stream: AsyncIterator[str]) -> AsyncIterator[str]:
    yield prefix
    async for chunk in stream:
        yield chunk


async def _retrieve(question: str, results: list[ExtractionResult]) -> list[_ScoredResult]:
    if settings.CODEMIND_EMBEDDING_ENABLED:
        try:
            return await _rank_by_vector_search(question, results)
        except Exception as exc:  # noqa: BLE001 - falls back to keyword search
            logger.warning("Vector search failed (%s); falling back to keyword search", exc)
    return _rank_by_keyword_overlap(question, results)


async def _rank_by_vector_search(question: str, results: list[ExtractionResult]) -> list[_ScoredResult]:
    embeddings = OllamaEmbeddings(base_url=settings.OLLAMA_BASE_URL, model=settings.OLLAMA_EMBED_MODEL)
    texts = [_truncate(result.content) for result in results]
    document_vectors = await embeddings.aembed_documents(texts)
    query_vector = await embeddings.aembed_query(question)

    scored = [
        (result, _cosine_similarity(query_vector, vector))
        for result, vector in zip(results, document_vectors)
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [_ScoredResult(result, 1) for result, _similarity in scored[:_TOP_K]]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _rank_by_keyword_overlap(question: str, results: list[ExtractionResult]) -> list[_ScoredResult]:
    query_terms = _tokenize(question)
    scored = [_ScoredResult(result, _score(query_terms, result)) for result in results]
    scored = [s for s in scored if s.score > 0]
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored[:_TOP_K]


def _tokenize(text: str) -> set[str]:
    return {
        word
        for word in _WORD_PATTERN.findall(text.lower())
        if len(word) > 2 and word not in _STOPWORDS
    }


def _score(query_terms: set[str], result: ExtractionResult) -> int:
    if not query_terms:
        return 0
    content_lower = result.content.lower()
    path_lower = result.relative_path.lower()
    score = 0
    for term in query_terms:
        if term in path_lower:
            score += 3
        if term in content_lower:
            score += 1
    return score


def _truncate(content: str) -> str:
    if len(content) <= _MAX_CONTENT_CHARS_PER_FILE:
        return content
    return content[:_MAX_CONTENT_CHARS_PER_FILE]


def _build_context(results: list[ExtractionResult]) -> str:
    parts = []
    for result in results:
        content = result.content
        if len(content) > _MAX_CONTENT_CHARS_PER_FILE:
            content = content[:_MAX_CONTENT_CHARS_PER_FILE] + "... [truncated]"
        parts.append(f"File: {result.relative_path}\n{content}\n\n---\n\n")
    return "".join(parts)


async def _call_chat(question: str, context: str, template: str = _SYSTEM_PROMPT_TEMPLATE) -> str:
    chat = _get_qa_chat()
    system_prompt = template.format(context=context)
    response = await chat.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=question)])
    return response.content


async def _stream_chat(question: str, context: str, template: str = _SYSTEM_PROMPT_TEMPLATE) -> AsyncIterator[str]:
    chat = _get_qa_chat()
    system_prompt = template.format(context=context)
    async for chunk in chat.astream([SystemMessage(content=system_prompt), HumanMessage(content=question)]):
        if chunk.content:
            yield chunk.content


def _load_results(output_directory: Path) -> list[ExtractionResult]:
    if not output_directory.is_dir():
        return []
    results: list[ExtractionResult] = []
    for path in output_directory.rglob("*.json"):
        if not path.is_file() or output.is_generated_metadata_file(path.name):
            continue
        result = _read_result(path)
        if result is not None and _has_usable_content(result):
            results.append(result)
    return results


def _has_usable_content(result: ExtractionResult) -> bool:
    return result.success and not result.skipped and bool(result.content and result.content.strip())


def _read_result(path: Path) -> ExtractionResult | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        return ExtractionResult.from_dict(data)
    except (TypeError, AttributeError):
        return None
