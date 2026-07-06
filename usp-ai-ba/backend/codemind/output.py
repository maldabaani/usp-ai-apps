"""Writes extraction results/job summaries to disk, and lists/reads them back
for the progress UI's file feed (polled, not watched).

Ported from com.jslogicextractor.output.{ExtractionResultWriter,
FileSystemExtractionResultWriter,OutputFileSnapshotService}. Job-specific
fields (id, phase, counts...) are passed in explicitly as plain values rather
than threading a full job object through this module, since the job type
itself is owned by codemind.job_store (a later phase) -- this keeps
output.py testable in isolation, matching the "near-zero risk, port first"
classification these file-I/O modules got in the port plan.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_SUMMARY_FILE_NAME = "_summary.json"
_COMPREHENSIVE_SUMMARY_FILE_NAME = "_comprehensive_summary.json"


def is_generated_metadata_file(name: str) -> bool:
    """True for any sidecar file this module writes into a job's output
    directory that isn't itself an extraction result -- centralized here (not
    just the filename strings) so every directory-walking function in this
    module, plus qa.py's own walk, agree on what to exclude without their
    exclusion sets silently drifting apart as more sidecar files are added."""
    return name in (_SUMMARY_FILE_NAME, _COMPREHENSIVE_SUMMARY_FILE_NAME)


def result_exists(output_directory: Path, relative_path: str) -> bool:
    return (output_directory / f"{relative_path}.json").exists()


def write_result(output_directory: Path, relative_path: str, result: dict) -> None:
    output_file = output_directory / f"{relative_path}.json"
    try:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(result, indent=2), encoding="utf-8")
    except OSError as e:
        logger.error("Failed to write extraction result for %s: %s", relative_path, e)


def write_summary(output_directory: Path, summary: dict) -> None:
    summary_file = output_directory / _SUMMARY_FILE_NAME
    try:
        output_directory.mkdir(parents=True, exist_ok=True)
        summary_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    except OSError as e:
        logger.error("Failed to write job summary: %s", e)


def write_comprehensive_summary(output_directory: Path, data: dict) -> None:
    """Caches qa.py's "comprehensive" Ask mode synthesis -- built once (lazily,
    on first comprehensive-mode question for a job) and reused for every later
    question against that job rather than rebuilt per question."""
    summary_file = output_directory / _COMPREHENSIVE_SUMMARY_FILE_NAME
    try:
        output_directory.mkdir(parents=True, exist_ok=True)
        summary_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as e:
        logger.error("Failed to write comprehensive summary: %s", e)


def read_comprehensive_summary(output_directory: Path) -> dict | None:
    """Returns None (never raises) if the cache doesn't exist yet or is
    unparseable -- either case just means qa.py should (re)build it."""
    summary_file = output_directory / _COMPREHENSIVE_SUMMARY_FILE_NAME
    try:
        return json.loads(summary_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@dataclass
class OutputFile:
    relative_path: str
    size_bytes: int
    modified_at: datetime


@dataclass
class FailedFile:
    relative_path: str
    error_message: str
    duration_millis: int


def recent_files(output_directory: Path, limit: int) -> list[OutputFile]:
    if not output_directory.is_dir():
        # Output dir is created lazily on first write; a job still
        # scanning/filtering has none yet.
        return []
    results: list[OutputFile] = []
    for path in output_directory.rglob("*.json"):
        if not path.is_file() or is_generated_metadata_file(path.name):
            continue
        out = _to_output_file(output_directory, path)
        if out is not None:
            results.append(out)
    results.sort(key=lambda f: f.modified_at, reverse=True)
    return results[:limit]


def read_output_file(output_directory: Path, relative_path: str) -> str | None:
    """Returns the raw JSON content of a single output file, guarded against
    path traversal."""
    file = (output_directory / relative_path).resolve()
    if not str(file).startswith(str(output_directory.resolve())):
        return None
    try:
        return file.read_text(encoding="utf-8")
    except OSError:
        return None


def list_failed_files(output_directory: Path) -> list[FailedFile]:
    """Scans all output files and returns those where success=false."""
    if not output_directory.is_dir():
        return []
    results: list[FailedFile] = []
    for path in output_directory.rglob("*.json"):
        if not path.is_file() or is_generated_metadata_file(path.name):
            continue
        failed = _try_read_failed_file(output_directory, path)
        if failed is not None:
            results.append(failed)
    results.sort(key=lambda f: f.relative_path)
    return results


def _try_read_failed_file(output_directory: Path, path: Path) -> FailedFile | None:
    try:
        node = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if node.get("success", True):
        return None
    rel = str(path.relative_to(output_directory)).replace("\\", "/")
    if rel.endswith(".json"):
        rel = rel[:-5]
    return FailedFile(rel, node.get("errorMessage") or "Unknown error", node.get("durationMillis") or 0)


def _to_output_file(output_directory: Path, path: Path) -> OutputFile | None:
    try:
        relative_path = str(path.relative_to(output_directory)).replace("\\", "/")
        stat = path.stat()
        modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        return OutputFile(relative_path, stat.st_size, modified_at)
    except OSError:
        # The writer may still be mid-write or the file may have been
        # replaced; skip it this poll.
        return None
