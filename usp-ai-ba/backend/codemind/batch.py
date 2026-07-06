"""Anthropic Message Batches API path for BATCH execution mode.

Ported from com.jslogicextractor.batch.BatchExtractionService: bulk
execution path for large repositories, submitting every eligible file as one
Claude request inside Anthropic Message Batches (flat 50% discount on all
token usage vs. the sync path), with the shared extraction instructions
cached via a single cache_control breakpoint on the system block so the same
prompt text is billed once per cache write instead of once per file.

Bypasses the LogicExtractionAgent abstraction entirely, same as Java:
neither Spring AI nor langchain-anthropic exposes the Batches API, so this
talks to the raw `anthropic.AsyncAnthropic` SDK directly, reusing the same
ANTHROPIC_API_KEY as codemind/agents/claude_agent.py's sync-mode agent.
Chunks run sequentially, one batch at a time -- sufficient for the 50% cost
win this mode exists for; chunk parallelism is a future scaling knob, not
implemented here (matching Java).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import TYPE_CHECKING, Optional

from anthropic import AsyncAnthropic

from codemind.agents.base import ExtractionResult, failure_result, success_result
from codemind.models import Language, SourceFile
from codemind.prompts import render_static_system_skeleton, render_user_content
from codemind import output
from config import settings

if TYPE_CHECKING:
    from codemind.orchestrator import ExtractionJob

logger = logging.getLogger(__name__)

AGENT_NAME = "claude-batch-extractor"

# Mirrors com.jslogicextractor.config.BatchExtractionProperties' defaults.
# model/max-tokens/temperature are the same env vars the sync-mode Anthropic
# config already reads (ANTHROPIC_MAX_TOKENS/ANTHROPIC_TEMPERATURE aren't yet
# StoryForge Settings fields, so plain env-var constants here, matching the
# "not settings-screen-editable" precedent orchestrator.py's own constants
# set); model reuses settings.CLAUDE_MODEL directly since that already is a
# hot-reloadable Settings field.
MAX_TOKENS = int(os.getenv("ANTHROPIC_MAX_TOKENS", "4096"))
TEMPERATURE = float(os.getenv("ANTHROPIC_TEMPERATURE", "0.0"))
POLL_INTERVAL_SECONDS = 30
POLL_TIMEOUT_SECONDS = 26 * 3600
MAX_REQUESTS_PER_BATCH = 10_000
MAX_BATCH_BYTES = 200_000_000
# Rough JSON structural overhead per request (custom_id, params wrapper, model/maxTokens/temperature fields).
REQUEST_OVERHEAD_BYTES = 256


async def run_batch(job: "ExtractionJob", files: list[SourceFile], *, client: Optional[AsyncAnthropic] = None) -> None:
    if not files:
        return

    client = client if client is not None else AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    by_language: dict[Language, list[SourceFile]] = {}
    for file in files:
        by_language.setdefault(Language.from_path(file.relative_path), []).append(file)

    total_chunks = sum(
        len(_chunk_files(lang_files, render_static_system_skeleton(lang)))
        for lang, lang_files in by_language.items()
    )
    logger.info(
        "Job %s: submitting %d files to Anthropic Batches API across %d batch(es) (%d language group(s))",
        job.id, len(files), total_chunks, len(by_language),
    )

    for lang, lang_files in by_language.items():
        system_skeleton = render_static_system_skeleton(lang)
        system_blocks = [
            {
                "type": "text",
                "text": system_skeleton,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }
        ]
        for chunk in _chunk_files(lang_files, system_skeleton):
            # Checked before every *new* batch submission (not just inside
            # _run_chunk's own poll loop) so a cancel requested between
            # chunks stops further Anthropic spend instead of submitting one
            # more batch of up to MAX_REQUESTS_PER_BATCH requests regardless.
            if job.cancel_requested:
                return
            await _run_chunk(client, job, chunk, system_blocks)


async def _run_chunk(
    client: AsyncAnthropic, job: "ExtractionJob", chunk: list[SourceFile], system_blocks: list[dict]
) -> None:
    files_by_custom_id: dict[str, SourceFile] = {}
    requests = []
    for i, file in enumerate(chunk):
        custom_id = f"f{i}"
        files_by_custom_id[custom_id] = file
        requests.append(
            {
                "custom_id": custom_id,
                "params": {
                    "model": settings.CLAUDE_MODEL,
                    "max_tokens": MAX_TOKENS,
                    "temperature": TEMPERATURE,
                    "system": system_blocks,
                    "messages": [{"role": "user", "content": render_user_content(file)}],
                },
            }
        )

    seen_custom_ids: set[str] = set()
    unresolved_reason = "No batch result returned"
    cancel_sent = False
    try:
        batch = await client.messages.batches.create(requests=requests)
        batch_id = batch.id
        deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
        while batch.processing_status != "ended":
            if time.monotonic() > deadline:
                logger.error(
                    "Job %s: batch %s timed out waiting for completion (%d files)",
                    job.id, batch_id, len(chunk),
                )
                _fail_remaining(job, files_by_custom_id, seen_custom_ids, "Batch processing timed out")
                return
            # "Stop Job" only sets a local flag -- request_cancel() has no
            # in-flight asyncio task to cancel here (unlike SYNC mode), since
            # the actual work is running server-side on Anthropic's Batches
            # API. Without this, the poll loop would keep waiting for the
            # batch to finish on its own (up to POLL_TIMEOUT_SECONDS), making
            # the button a no-op for BATCH-mode jobs. Cancel the batch itself
            # so already-completed requests are kept and unstarted ones stop.
            if job.cancel_requested and not cancel_sent:
                logger.info("Job %s: cancel requested, cancelling batch %s", job.id, batch_id)
                await client.messages.batches.cancel(batch_id)
                cancel_sent = True
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            batch = await client.messages.batches.retrieve(batch_id)

        results = await client.messages.batches.results(batch_id)
        async for individual in results:
            file = files_by_custom_id.get(individual.custom_id)
            if file is None:
                continue
            seen_custom_ids.add(individual.custom_id)
            result = _to_extraction_result(file, individual.result)
            output.write_result(job.output_directory, result.relative_path, result.to_dict())
            job.record_result(result.success)
    except Exception as exc:  # noqa: BLE001
        logger.error("Job %s: batch processing failed for a chunk of %d files: %s", job.id, len(chunk), exc)
        unresolved_reason = f"Batch processing failed: {exc}"

    # Covers both the exception path above and the (expected-rare) case where
    # the API simply never returned a result line for some custom_id -- never
    # double-counts an already-recorded file.
    _fail_remaining(job, files_by_custom_id, seen_custom_ids, unresolved_reason)


def _fail_remaining(
    job: "ExtractionJob", files_by_custom_id: dict[str, SourceFile], seen_custom_ids: set[str], reason: str
) -> None:
    for custom_id, file in files_by_custom_id.items():
        if custom_id not in seen_custom_ids:
            seen_custom_ids.add(custom_id)
            result = failure_result(file, AGENT_NAME, reason, 0)
            output.write_result(job.output_directory, result.relative_path, result.to_dict())
            job.record_result(False)


def _to_extraction_result(file: SourceFile, result) -> ExtractionResult:
    if result.type == "succeeded":
        message = result.message
        text = _extract_text(message)
        if text is None:
            return failure_result(file, AGENT_NAME, "No text content in batch response", 0)
        usage = message.usage
        return success_result(file, AGENT_NAME, text, 0, usage.input_tokens, usage.output_tokens)
    if result.type == "errored":
        error = result.error.error
        return failure_result(
            file, AGENT_NAME, f"{getattr(error, 'type', 'error')}: {getattr(error, 'message', error)}", 0
        )
    if result.type == "canceled":
        return failure_result(file, AGENT_NAME, "Batch request canceled", 0)
    return failure_result(file, AGENT_NAME, "Batch request expired", 0)


def _extract_text(message) -> Optional[str]:
    for block in message.content:
        if block.type == "text":
            return block.text
    return None


def _chunk_files(files: list[SourceFile], system_skeleton: str) -> list[list[SourceFile]]:
    system_bytes = len(system_skeleton.encode("utf-8"))
    chunks: list[list[SourceFile]] = []
    current: list[SourceFile] = []
    current_bytes = 0

    for file in files:
        # The Batches API has no submission-time dedup: the cached system
        # text still counts against the request-body cap on every request
        # line, even though Claude itself only processes it once per cache write.
        request_bytes = system_bytes + _estimate_user_content_bytes(file) + REQUEST_OVERHEAD_BYTES
        would_exceed_count = len(current) + 1 > MAX_REQUESTS_PER_BATCH
        would_exceed_bytes = current_bytes + request_bytes > MAX_BATCH_BYTES
        if current and (would_exceed_count or would_exceed_bytes):
            chunks.append(current)
            current = []
            current_bytes = 0
        current.append(file)
        current_bytes += request_bytes
    if current:
        chunks.append(current)
    return chunks


def _estimate_user_content_bytes(file: SourceFile) -> int:
    return len(file.content.encode("utf-8")) + len(file.relative_path) + 64
