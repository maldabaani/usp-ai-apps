"""Standing Ask Technical/Business endpoints, querying the accumulated
ingestion corpus (ingestion/retrieval.py) directly -- unlike StoryForge's
assess pipeline, these are not scoped to one job/SDD; they're always
available once ingestion has run at least once, and both endpoints draw from
the exact same retrieval, differing only in prompt framing/depth (see
prompts/ask_prompts.py).

SSE contract matches codemind_ask.py's now-retired per-job Ask feature: one
`event: sources` frame (a JSON array of source file paths) followed by zero
or more `event: chunk` frames, each a JSON-encoded string chunk.
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from pydantic import BaseModel, field_validator

import prompt_store
from api import ask_cache, conversation_store
from api.deps import require_auth
from config import settings
from ingestion import ingestion_generation
from ingestion.chroma_client import collection_counts
from ingestion.retrieval import retrieve_all_collections
from prompts.ask_prompts import BUSINESS_ASK_SYSTEM_PROMPT, TECHNICAL_ASK_SYSTEM_PROMPT

_DEFAULT_PROMPTS = {"technical": TECHNICAL_ASK_SYSTEM_PROMPT, "business": BUSINESS_ASK_SYSTEM_PROMPT}


def _effective_prompt(kind: str) -> str:
    """A Settings-page prompt edit takes effect on the very next request --
    prompt_store.get_custom_prompt() re-reads the persisted override fresh
    every call, so unlike _get_ask_chat()'s cached client there's no
    generation counter to check here."""
    return prompt_store.get_custom_prompt(kind) or _DEFAULT_PROMPTS[kind]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ask", tags=["ask"])

_NO_RESULTS_MESSAGE = (
    "No content has been ingested yet. Run code/manual ingestion first, then ask again."
)


class AskRequest(BaseModel):
    question: str
    conversation_id: str | None = None

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("question must not be blank")
        return value


_ask_chat: ChatAnthropic | ChatOllama | None = None
_ask_chat_generation = -1
_ask_chat_model_kind: str | None = None


def _get_ask_chat() -> ChatAnthropic | ChatOllama:
    """Rebuilds only when settings.settings_generation has advanced or
    ASK_QA_MODEL itself changed -- same generation-counter hot-reload
    pattern as codemind/qa.py's now-retired _get_qa_chat()."""
    global _ask_chat, _ask_chat_generation, _ask_chat_model_kind
    model_kind = settings.ASK_QA_MODEL
    if (
        _ask_chat is None
        or _ask_chat_generation != settings.settings_generation
        or _ask_chat_model_kind != model_kind
    ):
        if model_kind == "ollama":
            _ask_chat = ChatOllama(
                model=settings.OLLAMA_LLM_MODEL,
                base_url=settings.OLLAMA_BASE_URL,
                num_ctx=settings.OLLAMA_NUM_CTX,
                timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS,
                temperature=0,
            )
        else:
            _ask_chat = ChatAnthropic(
                model=settings.CLAUDE_MODEL,
                api_key=settings.ANTHROPIC_API_KEY,
                timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS,
                temperature=0,
            )
        _ask_chat_generation = settings.settings_generation
        _ask_chat_model_kind = model_kind
    return _ask_chat


def _format_chunks(chunks: list[dict]) -> str:
    if not chunks:
        return "(none retrieved)"
    return "\n---\n".join(
        f"Source: {c['metadata'].get('source', 'unknown')} "
        f"[type={c['metadata'].get('type', 'unknown')}, "
        f"layer={c['metadata'].get('layer', 'unknown')}, "
        f"module={c['metadata'].get('module', 'unknown')}]\n{c['content']}"
        for c in chunks
    )


def _build_context(retrieved: dict[str, list[dict]]) -> str:
    return (
        f"## User Manual Excerpts\n{_format_chunks(retrieved.get('manuals', []))}\n\n"
        f"## Codebase Chunks\n{_format_chunks(retrieved.get('codebase', []))}\n\n"
        f"## JPA Entity Definitions\n{_format_chunks(retrieved.get('entities', []))}\n"
    )


def _source_files(retrieved: dict[str, list[dict]]) -> list[str]:
    seen: list[str] = []
    for chunks in retrieved.values():
        for chunk in chunks:
            source = chunk["metadata"].get("source")
            if source and source not in seen:
                seen.append(source)
    return seen


async def _single_chunk_stream(text: str) -> AsyncIterator[str]:
    yield text


def _build_conversation_context(messages: list[dict], budget_chars: int) -> list[BaseMessage]:
    """Converts a conversation's persisted messages into LangChain message
    objects for threading into chat.astream()'s own message list -- kept
    completely separate from _build_context()'s {context} placeholder, since
    conversation memory and RAG-retrieved context are orthogonal concerns.
    Trimmed from the oldest turn forward (once the running total would
    exceed budget_chars) so a long-running conversation can't silently blow
    past the model's context window; the single most recent turn is always
    kept even if it alone exceeds the budget, so a fresh follow-up question
    is never starved of its own immediately preceding answer."""
    selected: list[dict] = []
    total = 0
    for message in reversed(messages):
        length = len(message["text"])
        if selected and total + length > budget_chars:
            break
        selected.append(message)
        total += length
    selected.reverse()

    return [
        HumanMessage(content=m["text"]) if m["role"] == "user" else AIMessage(content=m["text"])
        for m in selected
    ]


def _conversation_context_text(prior_messages: list[BaseMessage]) -> str:
    """Canonical text form of the trimmed conversation history, folded into
    api/ask_cache.py's cache key -- without this, a cached answer generated
    inside one conversation could leak into an unrelated conversation (or a
    fresh no-conversation ask) asking the exact same question text."""
    return "\n".join(f"{m.type}:{m.content}" for m in prior_messages)


async def _stream_answer(
    question: str, context: str, template: str, prior_messages: list[BaseMessage]
) -> AsyncIterator[str]:
    chat = _get_ask_chat()
    system_prompt = template.format(context=context)
    messages = [SystemMessage(content=system_prompt), *prior_messages, HumanMessage(content=question)]
    async for chunk in chat.astream(messages):
        if chunk.content:
            yield chunk.content


async def _recording_stream(
    owner: str, conversation_id: str, question: str, sources: list[str], text_stream: AsyncIterator[str]
) -> AsyncIterator[str]:
    """Wraps a text stream so the full accumulated answer is persisted to the
    conversation once streaming ends -- in a finally, so a client disconnect
    or an upstream error mid-stream still records whatever partial answer
    was produced instead of silently dropping the turn from history."""
    buffer = ""
    try:
        async for chunk in text_stream:
            buffer += chunk
            yield chunk
    finally:
        conversation_store.append_message(owner, conversation_id, "user", question, [])
        conversation_store.append_message(owner, conversation_id, "assistant", buffer, sources)


async def _caching_stream(cache_key: str, sources: list[str], text_stream: AsyncIterator[str]) -> AsyncIterator[str]:
    """Wraps a text stream so the full answer is cached only once streaming
    completes without error -- unlike _recording_stream's finally (which
    must persist even a partial answer to conversation history), caching a
    truncated/failed answer would be actively harmful: a later identical
    question would replay a broken answer instead of retrying for real."""
    buffer = ""
    async for chunk in text_stream:
        buffer += chunk
        yield chunk
    ask_cache.put(cache_key, buffer, sources)


async def _sse_body(source_files: list[str], text_stream: AsyncIterator[str]) -> AsyncIterator[str]:
    yield f"event: sources\ndata: {json.dumps(source_files)}\n\n"
    try:
        async for chunk in text_stream:
            yield f"event: chunk\ndata: {json.dumps(chunk)}\n\n"
    except Exception:  # noqa: BLE001 - matches codemind_ask.py: stop, don't crash the process
        logger.exception("Ask SSE stream failed mid-response")


async def _ask(question: str, template: str, kind: str, owner: str, conversation_id: str | None) -> StreamingResponse:
    if conversation_id is not None:
        conversation = conversation_store.get_conversation(owner, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
    else:
        conversation = conversation_store.create_conversation(owner, kind)

    prior_messages = _build_conversation_context(conversation["messages"], settings.CONVERSATION_HISTORY_CHAR_BUDGET)

    # Cache check happens before retrieval so a hit can skip retrieve_all_collections()
    # and the chat call entirely -- a hit is only possible for a question that
    # previously found real content under the current ingestion generation, so
    # there's no need to re-verify the corpus isn't empty.
    cache_key = ask_cache.build_key(
        kind, ingestion_generation.current(), template, _conversation_context_text(prior_messages), question
    )
    cached = ask_cache.get(cache_key)
    if cached is not None:
        stream = _recording_stream(
            owner, conversation["id"], question, cached["sources"], _single_chunk_stream(cached["answer"])
        )
        response = StreamingResponse(_sse_body(cached["sources"], stream), media_type="text/event-stream")
        response.headers["X-Conversation-Id"] = conversation["id"]
        return response

    retrieved = await retrieve_all_collections(question)
    if not any(retrieved.values()):
        # Empty-corpus fallback is never cached -- caching it would mean a
        # later real ingestion's answer gets shadowed by this placeholder
        # forever (the cache key doesn't change just because the corpus
        # went from empty to non-empty within the same generation bump).
        stream = _recording_stream(
            owner, conversation["id"], question, [], _single_chunk_stream(_NO_RESULTS_MESSAGE)
        )
        response = StreamingResponse(_sse_body([], stream), media_type="text/event-stream")
        response.headers["X-Conversation-Id"] = conversation["id"]
        return response

    source_files = _source_files(retrieved)
    context = _build_context(retrieved)
    text_stream = _stream_answer(question, context, template, prior_messages)
    caching_stream = _caching_stream(cache_key, source_files, text_stream)
    recorded_stream = _recording_stream(owner, conversation["id"], question, source_files, caching_stream)
    response = StreamingResponse(_sse_body(source_files, recorded_stream), media_type="text/event-stream")
    response.headers["X-Conversation-Id"] = conversation["id"]
    return response


@router.post("/technical")
async def ask_technical(request: AskRequest, user: dict = Depends(require_auth)) -> StreamingResponse:
    return await _ask(
        request.question, _effective_prompt("technical"), "technical", user["username"], request.conversation_id
    )


@router.post("/business")
async def ask_business(request: AskRequest, user: dict = Depends(require_auth)) -> StreamingResponse:
    return await _ask(
        request.question, _effective_prompt("business"), "business", user["username"], request.conversation_id
    )


@router.get("/status")
async def ask_status(user: dict = Depends(require_auth)) -> dict:
    counts = collection_counts()
    return {"counts": counts, "has_content": any(counts.values())}
