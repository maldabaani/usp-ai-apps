"""Persists a per-repository content-hash manifest so a tier can detect which
source files changed between ingestion runs and skip re-processing the ones
that haven't. Shared by both of ingest_code.py's tiers: tier 1 (mechanical
chunking) uses its own manifest under `.chunking-manifests/` to skip
re-chunking unchanged files, and tier 2 (ingestion/enrichment/enrich.py) uses
a separate one under `.enrichment-manifests/` to skip re-summarizing them --
same functions, two independent manifest namespaces, since "chunked" and
"summarized" are different done-states that can legitimately drift apart
(e.g. enrichment disabled for a run, or a forced re-chunk).

Manifests are stored at <manifests_root>/<sha256(repo_root)>.json, keyed by a
hash of the repository root path -- discoverable from the repo path alone,
with no per-job output directory to track (unlike CodeMind's original version
of this file, ported from com.jslogicextractor.incremental.ManifestService,
which existed to support per-job incremental re-runs against a job's own
flat-JSON output directory; that concept doesn't exist in the unified
ChromaDB-backed model, so this version is keyed purely on repo_root and drops
the output_directory field entirely).
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class FileChanges:
    added: list[str]
    modified: list[str]
    deleted: list[str]

    def changed_or_added(self) -> list[str]:
        return [*self.added, *self.modified]


def load(manifests_root: Path, repo_root: Path) -> dict[str, str] | None:
    file = _manifest_path(manifests_root, repo_root)
    if not file.exists():
        return None
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Could not read enrichment manifest at %s, treating as full run: %s", file, e)
        return None


def save(manifests_root: Path, repo_root: Path, file_hashes: dict[str, str]) -> None:
    file = _manifest_path(manifests_root, repo_root)
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(file_hashes, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning("Could not save enrichment manifest at %s: %s", file, e)


def compute_hash(absolute_path: Path) -> str | None:
    try:
        return hashlib.sha256(absolute_path.read_bytes()).hexdigest()
    except OSError as e:
        logger.warning("Cannot hash %s: %s", absolute_path, e)
        return None


def diff(previous: dict[str, str], current: dict[str, str]) -> FileChanges:
    added: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []

    for key, value in current.items():
        prev_hash = previous.get(key)
        if prev_hash is None:
            added.append(key)
        elif value != prev_hash:
            modified.append(key)
    for key in previous:
        if key not in current:
            deleted.append(key)
    return FileChanges(added, modified, deleted)


def _manifest_path(manifests_root: Path, repo_root: Path) -> Path:
    digest = hashlib.sha256(str(repo_root.absolute()).encode("utf-8")).hexdigest()
    return manifests_root / f"{digest}.json"
