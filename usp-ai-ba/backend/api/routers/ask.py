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

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from pydantic import BaseModel, field_validator

from api.deps import require_auth
from config import settings
from ingestion.chroma_client import collection_counts
from ingestion.retrieval import retrieve_all_collections
from prompts.ask_prompts import BUSINESS_ASK_SYSTEM_PROMPT, TECHNICAL_ASK_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ask", tags=["ask"])

REQUEST_TIMEOUT_SECONDS = 120

_NO_RESULTS_MESSAGE = (
    "No content has been ingested yet. Run code/manual ingestion first, then ask again."
)


class AskRequest(BaseModel):
    question: str

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
                timeout=REQUEST_TIMEOUT_SECONDS,
                temperature=0,
            )
        else:
            _ask_chat = ChatAnthropic(
                model=settings.CLAUDE_MODEL,
                api_key=settings.ANTHROPIC_API_KEY,
                timeout=REQUEST_TIMEOUT_SECONDS,
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


async def _stream_answer(question: str, context: str, template: str) -> AsyncIterator[str]:
    chat = _get_ask_chat()
    system_prompt = template.format(context=context)
    async for chunk in chat.astream([SystemMessage(content=system_prompt), HumanMessage(content=question)]):
        if chunk.content:
            yield chunk.content


async def _sse_body(source_files: list[str], text_stream: AsyncIterator[str]) -> AsyncIterator[str]:
    yield f"event: sources\ndata: {json.dumps(source_files)}\n\n"
    try:
        async for chunk in text_stream:
            yield f"event: chunk\ndata: {json.dumps(chunk)}\n\n"
    except Exception:  # noqa: BLE001 - matches codemind_ask.py: stop, don't crash the process
        logger.exception("Ask SSE stream failed mid-response")


async def _ask(question: str, template: str) -> StreamingResponse:
    retrieved = await retrieve_all_collections(question)
    if not any(retrieved.values()):
        return StreamingResponse(
            _sse_body([], _single_chunk_stream(_NO_RESULTS_MESSAGE)), media_type="text/event-stream"
        )

    source_files = _source_files(retrieved)
    context = _build_context(retrieved)
    stream = _stream_answer(question, context, template)
    return StreamingResponse(_sse_body(source_files, stream), media_type="text/event-stream")


@router.post("/technical")
async def ask_technical(request: AskRequest, user: dict = Depends(require_auth)) -> StreamingResponse:
    return await _ask(request.question, TECHNICAL_ASK_SYSTEM_PROMPT)


@router.post("/business")
async def ask_business(request: AskRequest, user: dict = Depends(require_auth)) -> StreamingResponse:
    return await _ask(request.question, BUSINESS_ASK_SYSTEM_PROMPT)


@router.get("/status")
async def ask_status(user: dict = Depends(require_auth)) -> dict:
    counts = collection_counts()
    return {"counts": counts, "has_content": any(counts.values())}
