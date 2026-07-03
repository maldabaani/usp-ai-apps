"""Repository/single-file scanning.

Ported from com.jslogicextractor.scanner.RepositoryScannerService. Language
and SourceFile live in codemind.models (see that module's docstring for why).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from codemind import chunker as _chunker
from codemind.models import SourceFile

logger = logging.getLogger(__name__)


def scan(
    repository_root: Path,
    *,
    included_extensions: set[str],
    excluded_directory_names: set[str],
    max_file_size_bytes: int,
    chunking_enabled: bool,
    max_lines_per_chunk: int,
) -> list[SourceFile]:
    if not repository_root.is_dir():
        raise ValueError(f"Not a directory: {repository_root}")

    results: list[SourceFile] = []
    for dirpath, dirnames, filenames in os.walk(repository_root):
        # Prune descent into excluded directories for efficiency; the per-file
        # check below still covers every path segment (including the
        # filename itself) to match the Java original's isExcluded exactly.
        dirnames[:] = [d for d in dirnames if d not in excluded_directory_names]
        for filename in filenames:
            path = Path(dirpath) / filename
            if _is_excluded(repository_root, path, excluded_directory_names):
                continue
            if not _has_included_extension(path, included_extensions):
                continue
            results.extend(
                _read_source_files(
                    repository_root,
                    path,
                    max_file_size_bytes=max_file_size_bytes,
                    chunking_enabled=chunking_enabled,
                    max_lines_per_chunk=max_lines_per_chunk,
                )
            )
    return results


def scan_file(
    file: Path,
    *,
    included_extensions: set[str],
    max_file_size_bytes: int,
    chunking_enabled: bool,
    max_lines_per_chunk: int,
) -> list[SourceFile]:
    """Single-file counterpart to scan(), used by the input-directory watcher:
    each dropped file becomes its own job scanning exactly that one file
    rather than a whole directory."""
    if not file.is_file():
        raise ValueError(f"Not a file: {file}")
    if not _has_included_extension(file, included_extensions):
        return []
    return _read_source_files(
        file.parent,
        file,
        max_file_size_bytes=max_file_size_bytes,
        chunking_enabled=chunking_enabled,
        max_lines_per_chunk=max_lines_per_chunk,
    )


def _is_excluded(root: Path, file: Path, excluded_directory_names: set[str]) -> bool:
    relative = file.relative_to(root)
    return any(part in excluded_directory_names for part in relative.parts)


def _has_included_extension(file: Path, included_extensions: set[str]) -> bool:
    name = file.name.lower()
    return any(name.endswith(ext) for ext in included_extensions)


def _read_source_files(
    root: Path,
    file: Path,
    *,
    max_file_size_bytes: int,
    chunking_enabled: bool,
    max_lines_per_chunk: int,
) -> list[SourceFile]:
    try:
        size = file.stat().st_size
        relative_path = str(file.relative_to(root)).replace(os.sep, "/")
        if size > max_file_size_bytes:
            if not chunking_enabled:
                logger.warning(
                    "Skipping %s (%d bytes exceeds max-file-size-bytes=%d)", file, size, max_file_size_bytes
                )
                return []
            content = file.read_text(encoding="utf-8")
            chunks = _chunker.chunk(file, relative_path, content, max_lines_per_chunk)
            logger.info("Split %s (%d bytes) into %d chunk(s)", file, size, len(chunks))
            return chunks
        content = file.read_text(encoding="utf-8")
        return [SourceFile(file, relative_path, content, size)]
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("Skipping unreadable file %s: %s", file, e)
        return []
