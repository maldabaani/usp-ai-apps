"""Persists a per-repository content-hash manifest so the orchestrator can
detect which source files changed between runs and process only those
(incremental mode).

Manifests are stored at <default_output_directory>/.manifests/<sha256(repo_root)>.json,
keyed by a hash of the repository root path. This makes them discoverable
from the repo path alone, independent of per-job output directories.

Ported from com.jslogicextractor.incremental.ManifestService.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from codemind.models import SourceFile

logger = logging.getLogger(__name__)


@dataclass
class Manifest:
    output_directory: Path
    file_hashes: dict[str, str]


@dataclass
class FileChanges:
    added: list[str]
    modified: list[str]
    deleted: list[str]

    def changed_or_added(self) -> list[str]:
        return [*self.added, *self.modified]


def load(default_output_directory: Path, repo_root: Path) -> Manifest | None:
    file = _manifest_path(default_output_directory, repo_root)
    if not file.exists():
        return None
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
        return Manifest(Path(data["outputDirectory"]).absolute(), data["fileHashes"])
    except (OSError, ValueError, KeyError) as e:
        logger.warning("Could not read manifest at %s, treating as full run: %s", file, e)
        return None


def save(default_output_directory: Path, repo_root: Path, manifest: Manifest) -> None:
    file = _manifest_path(default_output_directory, repo_root)
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"outputDirectory": str(manifest.output_directory), "fileHashes": manifest.file_hashes}
        file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning("Could not save manifest at %s: %s", file, e)


def compute_hashes(repo_root: Path, files: list[SourceFile]) -> dict[str, str]:
    """Computes SHA-256 content hashes for the distinct original source files.
    Chunked SourceFiles share the same absolute_path, so they are
    deduplicated before hashing -- the whole original file is always hashed
    from disk."""
    seen: set[Path] = set()
    hashes: dict[str, str] = {}
    for file in files:
        if file.absolute_path not in seen:
            seen.add(file.absolute_path)
            rel_path = str(file.absolute_path.relative_to(repo_root)).replace("\\", "/")
            digest = _sha256_file(file.absolute_path)
            if digest is not None:
                hashes[rel_path] = digest
    return hashes


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


def _manifest_path(default_output_directory: Path, repo_root: Path) -> Path:
    manifests_dir = default_output_directory.absolute() / ".manifests"
    digest = hashlib.sha256(str(repo_root.absolute()).encode("utf-8")).hexdigest()
    return manifests_dir / f"{digest}.json"


def _sha256_file(file: Path) -> str | None:
    try:
        return hashlib.sha256(file.read_bytes()).hexdigest()
    except OSError as e:
        logger.warning("Cannot hash %s: %s", file, e)
        return None
