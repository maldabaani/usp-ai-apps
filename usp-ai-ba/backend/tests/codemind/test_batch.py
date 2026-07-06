"""Covers codemind/batch.py, ported from
com.jslogicextractor.batch.BatchExtractionService. The AsyncAnthropic client
is injected via run_batch(..., client=...) (a testability seam Java gets for
free through constructor injection) so these tests never touch the real
Anthropic Batches API -- matching this suite's mocked-ChatClient convention
used throughout the other codemind/ ports.
"""
import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from codemind import batch, output
from codemind.models import SourceFile
from codemind.orchestrator import ExtractionJob


def _file(relative_path: str, content: str) -> SourceFile:
    return SourceFile(Path(relative_path), relative_path, content, len(content))


def _job(tmp_path: Path) -> ExtractionJob:
    return ExtractionJob(
        id=uuid.uuid4(),
        repository_root=tmp_path / "repo",
        output_directory=tmp_path / "out",
        max_concurrency=4,
    )


def _succeeded_message(text: str = '{"summary":"adds nothing interesting"}', input_tokens=100, output_tokens=50):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def _succeeded_result(text: str = '{"summary":"adds nothing interesting"}'):
    return SimpleNamespace(type="succeeded", message=_succeeded_message(text))


def _errored_result(message: str = "file too large"):
    return SimpleNamespace(type="errored", error=SimpleNamespace(error=SimpleNamespace(type="invalid_request_error", message=message)))


def _individual(custom_id: str, result) -> SimpleNamespace:
    return SimpleNamespace(custom_id=custom_id, result=result)


def _message_batch(batch_id: str, processing_status: str = "ended") -> SimpleNamespace:
    return SimpleNamespace(id=batch_id, processing_status=processing_status)


def _canceled_result() -> SimpleNamespace:
    return SimpleNamespace(type="canceled")


class _FakeBatchesApi:
    def __init__(self, create_returns=None, create_error=None, retrieve_sequence=None, results_by_batch=None):
        self._create_returns = list(create_returns or [])
        self._create_error = create_error
        self._retrieve_sequence = list(retrieve_sequence or [])
        self._results_by_batch = results_by_batch or {}
        self.create_call_count = 0
        self.cancel_call_count = 0
        self.cancelled_batch_ids: list[str] = []

    async def create(self, **kwargs):
        self.create_call_count += 1
        if self._create_error is not None:
            raise self._create_error
        return self._create_returns.pop(0)

    async def retrieve(self, batch_id):
        if self._retrieve_sequence:
            return self._retrieve_sequence.pop(0)
        return _message_batch(batch_id, "ended")

    async def cancel(self, batch_id):
        self.cancel_call_count += 1
        self.cancelled_batch_ids.append(batch_id)
        return _message_batch(batch_id, "canceling")

    async def results(self, batch_id):
        items = self._results_by_batch.get(batch_id, [])

        async def _gen():
            for item in items:
                yield item

        return _gen()


def _fake_client(batches_api: _FakeBatchesApi) -> SimpleNamespace:
    return SimpleNamespace(messages=SimpleNamespace(batches=batches_api))


def test_maps_succeeded_and_errored_results_and_records_counts(tmp_path):
    succeeded_file = _file("a.js", "const a = 1;")
    errored_file = _file("b.js", "const b = 2;")
    job = _job(tmp_path)

    batches_api = _FakeBatchesApi(
        create_returns=[_message_batch("batch_123", "ended")],
        results_by_batch={
            "batch_123": [
                _individual("f0", _succeeded_result()),
                _individual("f1", _errored_result("file too large")),
            ]
        },
    )

    asyncio.run(batch.run_batch(job, [succeeded_file, errored_file], client=_fake_client(batches_api)))

    assert job.succeeded_files == 1
    assert job.failed_files == 1
    assert job.processed_files == 2

    succeeded_raw = output.read_output_file(job.output_directory, "a.js.json")
    assert succeeded_raw is not None
    assert "adds nothing interesting" in succeeded_raw

    failed_raw = output.read_output_file(job.output_directory, "b.js.json")
    assert failed_raw is not None
    assert "file too large" in failed_raw


def test_fails_all_files_in_chunk_when_create_throws(tmp_path):
    file_a = _file("a.js", "const a = 1;")
    file_b = _file("b.js", "const b = 2;")
    job = _job(tmp_path)

    batches_api = _FakeBatchesApi(create_error=RuntimeError("boom"))

    asyncio.run(batch.run_batch(job, [file_a, file_b], client=_fake_client(batches_api)))

    assert job.failed_files == 2
    assert job.succeeded_files == 0

    for name in ("a.js.json", "b.js.json"):
        raw = output.read_output_file(job.output_directory, name)
        assert raw is not None
        assert "boom" in raw


def test_splits_files_across_multiple_chunks_when_request_cap_is_exceeded(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "MAX_REQUESTS_PER_BATCH", 1)
    file_a = _file("a.js", "const a = 1;")
    file_b = _file("b.js", "const b = 2;")
    job = _job(tmp_path)

    batches_api = _FakeBatchesApi(
        create_returns=[_message_batch("batch_1", "ended"), _message_batch("batch_2", "ended")],
        results_by_batch={
            "batch_1": [_individual("f0", _succeeded_result())],
            "batch_2": [_individual("f0", _succeeded_result())],
        },
    )

    asyncio.run(batch.run_batch(job, [file_a, file_b], client=_fake_client(batches_api)))

    assert batches_api.create_call_count == 2
    assert job.succeeded_files == 2
    assert output.read_output_file(job.output_directory, "a.js.json") is not None
    assert output.read_output_file(job.output_directory, "b.js.json") is not None


def test_cancels_the_anthropic_batch_when_job_cancel_requested_mid_poll(tmp_path, monkeypatch):
    # Regression test: request_cancel() has no in-flight asyncio task to
    # cancel here (unlike SYNC mode) since the work runs server-side on
    # Anthropic's Batches API -- without this, "Stop Job" was a silent no-op
    # for BATCH-mode jobs, and the poll loop would keep waiting up to
    # POLL_TIMEOUT_SECONDS (26 hours) for the batch to finish on its own.
    monkeypatch.setattr(batch, "POLL_INTERVAL_SECONDS", 0.005)
    stuck_file = _file("a.js", "const a = 1;")
    job = _job(tmp_path)

    class _CancelsJobOnFirstRetrieve(_FakeBatchesApi):
        # Flips the job's own cancel flag as a side effect of the first poll
        # tick, simulating "Stop Job" arriving mid-poll (after submission,
        # not before) rather than the simpler-but-unrealistic "already
        # cancelled before the batch was even submitted" case the sibling
        # outer-loop test below covers.
        async def retrieve(self, batch_id):
            job.cancel_requested = True
            return await super().retrieve(batch_id)

    batches_api = _CancelsJobOnFirstRetrieve(
        create_returns=[_message_batch("batch_1", "in_progress")],
        retrieve_sequence=[_message_batch("batch_1", "in_progress"), _message_batch("batch_1", "ended")],
        results_by_batch={"batch_1": [_individual("f0", _canceled_result())]},
    )

    asyncio.run(batch.run_batch(job, [stuck_file], client=_fake_client(batches_api)))

    assert batches_api.cancel_call_count == 1
    assert batches_api.cancelled_batch_ids == ["batch_1"]
    assert job.failed_files == 1
    raw = output.read_output_file(job.output_directory, "a.js.json")
    assert raw is not None
    assert "canceled" in raw.lower()


def test_skips_submitting_further_chunks_once_job_cancel_requested(tmp_path, monkeypatch):
    # The per-chunk cancel above only reaches a batch already submitted --
    # this covers the outer loop guard that stops *new* chunk submissions
    # (each spends real Anthropic budget) once cancellation is requested.
    monkeypatch.setattr(batch, "MAX_REQUESTS_PER_BATCH", 1)
    file_a = _file("a.js", "const a = 1;")
    file_b = _file("b.js", "const b = 2;")
    job = _job(tmp_path)
    job.cancel_requested = True

    batches_api = _FakeBatchesApi()

    asyncio.run(batch.run_batch(job, [file_a, file_b], client=_fake_client(batches_api)))

    assert batches_api.create_call_count == 0


def test_fails_files_when_batch_never_reaches_ended_before_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "POLL_INTERVAL_SECONDS", 0.005)
    monkeypatch.setattr(batch, "POLL_TIMEOUT_SECONDS", 0.02)
    stuck_file = _file("a.js", "const a = 1;")
    job = _job(tmp_path)

    in_progress_batch = _message_batch("batch_stuck", "in_progress")
    batches_api = _FakeBatchesApi(
        create_returns=[in_progress_batch],
        retrieve_sequence=[in_progress_batch] * 20,
    )

    asyncio.run(batch.run_batch(job, [stuck_file], client=_fake_client(batches_api)))

    assert job.failed_files == 1
    assert job.succeeded_files == 0
    raw = output.read_output_file(job.output_directory, "a.js.json")
    assert raw is not None
    assert "timed out" in raw
