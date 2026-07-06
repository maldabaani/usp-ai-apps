"""SSE Q&A streaming routes, ported from
com.jslogicextractor.web.GlobalAskController (the cross-job "Ask All" route)
and ExtractionJobController's per-job POST .../qa/stream route.

Preserves the exact SSE contract Java's SseEmitter produced: one
`event: sources` frame (a JSON array of source file paths) followed by zero
or more `event: chunk` frames, each a JSON-encoded string -- double-encoded
on top of an already-string chunk, matching
`objectMapper.writeValueAsString(chunk)` on a String in Java. See
deploy/nginx.conf for the matching proxy_buffering-off block these routes
need (SSE must not be buffered by the reverse proxy).
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import AsyncIterator, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator

from api.deps import require_auth
from codemind import job_registry, qa
from codemind.orchestrator import ExtractionJob, JobPhase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["codemind-ask"])


class QaRequest(BaseModel):
    question: str
    # "comprehensive" (Job Ask only -- see ask_stream below) skips the top-K
    # retrieval+LLM path: it reduces every extracted file's summary into one
    # cached, question-agnostic overview (built lazily on first use, then
    # reused) and answers from that instead of a 6-file sample. Ask All
    # doesn't support it; ask_all_stream below never passes mode through, so
    # it's harmlessly accepted-but-ignored on that route.
    mode: Literal["deep", "comprehensive"] = "deep"

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("question must not be blank")
        return value


def _require_job(job_id: uuid.UUID) -> ExtractionJob:
    job = job_registry.find(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No such job: {job_id}")
    return job


async def _sse_body(stream_result: qa.QaStreamResult) -> AsyncIterator[str]:
    yield f"event: sources\ndata: {json.dumps(stream_result.source_files)}\n\n"
    try:
        async for chunk in stream_result.text_stream:
            yield f"event: chunk\ndata: {json.dumps(chunk)}\n\n"
    except Exception:  # noqa: BLE001 - matches Java's emitter.completeWithError: stop, don't crash the process
        logger.exception("SSE stream failed mid-response")


@router.post("/extraction-jobs/{job_id}/qa/stream")
async def ask_stream(
    job_id: uuid.UUID, request: QaRequest, user: dict = Depends(require_auth)
) -> StreamingResponse:
    job = _require_job(job_id)
    stream_result = await qa.ask_for_stream([job.output_directory], request.question, mode=request.mode)
    return StreamingResponse(_sse_body(stream_result), media_type="text/event-stream")


@router.post("/ask/stream")
async def ask_all_stream(request: QaRequest, user: dict = Depends(require_auth)) -> StreamingResponse:
    completed_output_directories = [
        job.output_directory for job in job_registry.find_all() if job.phase == JobPhase.COMPLETED
    ]
    # Deliberately not passing request.mode through -- Ask All has no
    # "comprehensive" mode (see QaRequest.mode's docstring above), it's always deep.
    stream_result = await qa.ask_for_stream(completed_output_directories, request.question)
    return StreamingResponse(_sse_body(stream_result), media_type="text/event-stream")
