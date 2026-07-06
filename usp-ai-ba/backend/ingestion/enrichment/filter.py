"""Cheap, regex-only pre-pass applied to every file before it burns an LLM
call: skips files with no real business logic to extract. Deliberately
conservative -- a false negative just costs one wasted call, but a false
positive silently drops a file's extraction entirely, so each rule only
matches unambiguous cases.

Moved verbatim from codemind/filter.py (itself ported from
com.jslogicextractor.filter.NonSubstantiveFileFilter) as part of unifying
CodeMind's per-file LLM extraction into the ChromaDB ingestion pipeline --
see plan file section I.
"""
from __future__ import annotations

import re

from ingestion.enrichment.models import SourceFile

_TEST_FILENAME = re.compile(r".*\.(test|spec)\.[jt]sx?$", re.IGNORECASE)
_TEST_PATH_SEGMENT = re.compile(r"(^|/)(__tests__|tests?)(/|$)", re.IGNORECASE)
_IMPORT_LINE = re.compile(r"^import\b.*$")
_EXPORT_FROM_LINE = re.compile(
    r"^export\s+(type\s+)?(\*(?:\s+as\s+\w+)?|\{[^}]*\})\s+from\s+['\"][^'\"]+['\"];?$"
)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def skip_reason(file: SourceFile) -> str | None:
    path = file.relative_path

    if path.endswith(".d.ts"):
        return "type-declaration file (.d.ts)"
    if _TEST_FILENAME.fullmatch(path) or _TEST_PATH_SEGMENT.search(path):
        return "test/spec file"
    if _is_barrel_file(file.content):
        return "barrel file (re-exports only)"
    return None


def _is_barrel_file(content: str) -> bool:
    without_block_comments = _BLOCK_COMMENT.sub("", content)
    saw_import_or_export_line = False
    for raw_line in without_block_comments.split("\n"):
        line = _strip_line_comment(raw_line).strip()
        if not line:
            continue
        if not _IMPORT_LINE.fullmatch(line) and not _EXPORT_FROM_LINE.fullmatch(line):
            return False
        saw_import_or_export_line = True
    return saw_import_or_export_line


def _strip_line_comment(line: str) -> str:
    idx = line.find("//")
    return line[:idx] if idx >= 0 else line
