"""Persists partial per-part progress for an oversized file's LLM-summary
enrichment, so a run interrupted partway through a many-part file (e.g.
Claude API credits running out after 200 of 412 parts) doesn't have to
re-attempt -- and re-pay for -- every part that already succeeded on the
next run. Only the parts still missing get attempted again.

Keyed by the file's own content hash and part count (like manifest.py's
whole-file tracking), so a genuine content change or a different chunker
split discards stale progress rather than silently resuming against
outdated text.

Deliberately separate from manifest.py's whole-file "done" tracking: a file
only earns a manifest entry once *every* part has succeeded (see
enrich.py's process_one()) -- this module tracks the in-between state that
exists while some parts are done and others aren't yet.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _progress_path(progress_root: Path, repo_root: Path, relative_path: str) -> Path:
    key = f"{repo_root.absolute()}::{relative_path}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return progress_root / f"{digest}.json"


def load(
    progress_root: Path,
    repo_root: Path,
    relative_path: str,
    content_hash: str,
    total_parts: int,
) -> dict[int, str]:
    """Returns {part_index: summary_text} for parts already completed for
    this exact file content and part count. Empty if there's no saved
    progress, the content has changed since it was saved, or the file was
    re-split into a different number of parts (e.g. MAX_LINES_PER_CHUNK
    changed between runs) -- either way, stale progress is never reused."""
    file = _progress_path(progress_root, repo_root, relative_path)
    if not file.exists():
        return {}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Could not read enrichment part-progress at %s, starting fresh: %s", file, e)
        return {}
    if data.get("content_hash") != content_hash or data.get("total_parts") != total_parts:
        return {}
    return {int(index): text for index, text in data.get("parts", {}).items()}


def save(
    progress_root: Path,
    repo_root: Path,
    relative_path: str,
    content_hash: str,
    total_parts: int,
    completed: dict[int, str],
) -> None:
    file = _progress_path(progress_root, repo_root, relative_path)
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(
            json.dumps(
                {
                    "content_hash": content_hash,
                    "total_parts": total_parts,
                    "parts": {str(index): text for index, text in completed.items()},
                }
            ),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("Could not save enrichment part-progress at %s: %s", file, e)


def clear(progress_root: Path, repo_root: Path, relative_path: str) -> None:
    """Called once a file's parts all genuinely succeed and its combined
    summary is written -- there's nothing left to resume, so the partial
    record is removed rather than left to accumulate indefinitely."""
    file = _progress_path(progress_root, repo_root, relative_path)
    try:
        file.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("Could not clear enrichment part-progress at %s: %s", file, e)
