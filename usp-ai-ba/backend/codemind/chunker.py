"""Splits a single oversized source file into multiple smaller SourceFile
chunks so it can flow through the existing per-file extraction pipeline
unchanged. Cuts are only ever made at line boundaries, and preferentially at
"depth-0" boundaries (outside any bracket/paren/brace nesting, string, or
block comment) so a chunk rarely splits a function/class/block in half.

Bracket depth is tracked with a single combined counter across {}, (), and [].
Template literals are treated as one opaque string region (interpolation
internals are not tracked), and regex literals are not specially detected, so
their characters are scanned at face value. Both are accepted
simplifications: a miscounted depth can only ever delay a cut to a later
line, never corrupt chunk content, since every cut lands exactly on a line
boundary.

Ported from com.jslogicextractor.scanner.LargeFileChunker.
"""
from __future__ import annotations

import logging
from pathlib import Path

from codemind.models import SourceFile

logger = logging.getLogger(__name__)

_HARD_CAP_MULTIPLIER = 2


class _ScanState:
    __slots__ = ("depth", "string_delim", "in_block_comment")

    def __init__(self) -> None:
        self.depth = 0
        self.string_delim = ""
        self.in_block_comment = False

    def reset(self) -> None:
        self.depth = 0
        self.string_delim = ""
        self.in_block_comment = False


def chunk(absolute_path: Path, relative_path: str, content: str, max_lines_per_chunk: int) -> list[SourceFile]:
    lines = content.split("\n")
    if len(lines) <= 1:
        logger.warning(
            "%s has no line breaks to split on (%d chars); sending as a single oversized chunk",
            relative_path,
            len(content),
        )
        return [_to_chunk_source_file(absolute_path, relative_path, content, 1)]

    hard_cap_lines = max_lines_per_chunk * _HARD_CAP_MULTIPLIER
    chunks: list[SourceFile] = []
    buffer: list[str] = []
    state = _ScanState()
    lines_in_chunk = 0

    for idx, line in enumerate(lines):
        buffer.append(line)
        lines_in_chunk += 1

        _scan_line(line, state)

        at_safe_boundary = state.depth <= 0 and state.string_delim == "" and not state.in_block_comment
        reached_target = lines_in_chunk >= max_lines_per_chunk
        reached_hard_cap = lines_in_chunk >= hard_cap_lines
        is_last_line = idx == len(lines) - 1

        if is_last_line or (reached_target and at_safe_boundary) or reached_hard_cap:
            if reached_hard_cap and not at_safe_boundary and not is_last_line:
                logger.warning(
                    "Force-cutting %s chunk %d after %d lines without reaching a safe boundary (bracket depth=%d)",
                    relative_path,
                    len(chunks) + 1,
                    lines_in_chunk,
                    state.depth,
                )
            chunks.append(
                _to_chunk_source_file(absolute_path, relative_path, "\n".join(buffer), len(chunks) + 1)
            )
            buffer = []
            lines_in_chunk = 0
            state.reset()

    return chunks


def _scan_line(line: str, state: _ScanState) -> None:
    n = len(line)
    i = 0
    while i < n:
        c = line[i]

        if state.string_delim:
            if c == "\\":
                i += 2
                continue
            if c == state.string_delim:
                state.string_delim = ""
            i += 1
            continue

        if state.in_block_comment:
            if c == "*" and i + 1 < n and line[i + 1] == "/":
                state.in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        if c == "/" and i + 1 < n and line[i + 1] == "/":
            return
        if c == "/" and i + 1 < n and line[i + 1] == "*":
            state.in_block_comment = True
            i += 2
            continue
        if c in ("'", '"', "`"):
            state.string_delim = c
            i += 1
            continue
        if c in ("{", "(", "["):
            state.depth += 1
            i += 1
            continue
        if c in ("}", ")", "]"):
            state.depth -= 1
            i += 1
            continue
        i += 1


def _to_chunk_source_file(absolute_path: Path, relative_path: str, content: str, part_number: int) -> SourceFile:
    chunk_relative_path = f"{relative_path}/part-{part_number:04d}{_extract_extension(relative_path)}"
    size_bytes = len(content.encode("utf-8"))
    return SourceFile(absolute_path, chunk_relative_path, content, size_bytes)


def _extract_extension(relative_path: str) -> str:
    slash = relative_path.rfind("/")
    dot = relative_path.rfind(".")
    return relative_path[dot:] if dot > slash else ""
