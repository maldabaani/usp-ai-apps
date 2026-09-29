"""Code-aware chunking.

- Python: split by top-level functions/classes via `ast` (decorators included); large classes are
  split into their methods; module-level code between definitions becomes its own chunk.
- Java / TypeScript / JavaScript: brace matching (strings and comments aware) over lines that
  start a declaration; large classes are split into their members.
- Markdown: split by headings.
- Everything else, and any unit that is still too large: line windows with overlap.

Line numbers are 1-based and inclusive.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import PurePosixPath

LANGUAGES = {
    ".py": "python",
    ".java": "java",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".mjs": "javascript",
    ".html": "html",
    ".css": "css",
    ".scss": "css",
    ".md": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
    ".properties": "properties",
    ".sql": "sql",
    ".txt": "text",
}
BRACE_LANGUAGES = {"java", "typescript", "javascript"}


@dataclass(frozen=True)
class ChunkConfig:
    max_lines: int = 120
    window_lines: int = 60
    overlap_lines: int = 10


@dataclass(frozen=True)
class Chunk:
    path: str
    language: str
    start_line: int
    end_line: int
    text: str
    symbol: str = ""  # e.g. "class TodoService", "def create_todo"

    @property
    def header(self) -> str:
        sym = f" {self.symbol}" if self.symbol else ""
        return f"{self.path}:{self.start_line}-{self.end_line}{sym}"


def detect_language(path: str) -> str:
    name = PurePosixPath(path).name.lower()
    if name in ("dockerfile", "makefile"):
        return name
    return LANGUAGES.get(PurePosixPath(path).suffix.lower(), "text")


# --------------------------------------------------------------------------------------------
# Generic helpers
# --------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class _Span:
    start: int  # 1-based inclusive
    end: int
    symbol: str = ""


def _windows(start: int, end: int, cfg: ChunkConfig, symbol: str = "") -> Iterator[_Span]:
    size = max(1, cfg.window_lines)
    step = max(1, size - cfg.overlap_lines)
    line = start
    while True:
        stop = min(end, line + size - 1)
        yield _Span(line, stop, symbol)
        if stop >= end:
            return
        line += step


def _fill_gaps(spans: list[_Span], total: int) -> list[_Span]:
    """Cover lines not inside any unit (imports, module code) with 'module' spans."""
    out: list[_Span] = []
    cursor = 1
    for span in sorted(spans, key=lambda s: s.start):
        if span.start > cursor:
            out.append(_Span(cursor, span.start - 1, "module"))
        out.append(span)
        cursor = max(cursor, span.end + 1)
    if cursor <= total:
        out.append(_Span(cursor, total, "module"))
    return out


def _is_blank(lines: list[str], span: _Span) -> bool:
    return not any(line.strip() for line in lines[span.start - 1 : span.end])


def _trim(lines: list[str], span: _Span) -> _Span | None:
    """Drop blank lines at both ends; None if nothing is left."""
    start, end = span.start, span.end
    while start <= end and not lines[start - 1].strip():
        start += 1
    while end >= start and not lines[end - 1].strip():
        end -= 1
    return _Span(start, end, span.symbol) if start <= end else None


def _finalize(
    path: str, language: str, lines: list[str], spans: list[_Span], cfg: ChunkConfig
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for raw in _fill_gaps(spans, len(lines)):
        span = _trim(lines, raw)
        if span is None:
            continue
        pieces = (
            [span]
            if span.end - span.start + 1 <= cfg.max_lines
            else list(_windows(span.start, span.end, cfg, span.symbol))
        )
        for piece in pieces:
            text = "\n".join(lines[piece.start - 1 : piece.end])
            if text.strip():
                symbol = "" if piece.symbol == "module" else piece.symbol
                chunks.append(Chunk(path, language, piece.start, piece.end, text, symbol))
    return chunks


# --------------------------------------------------------------------------------------------
# Python
# --------------------------------------------------------------------------------------------
def _py_node_span(node: ast.AST, prefix: str = "") -> _Span:
    decorators = getattr(node, "decorator_list", [])
    start = min([node.lineno, *(d.lineno for d in decorators)])  # type: ignore[attr-defined]
    kind = "class" if isinstance(node, ast.ClassDef) else "def"
    name = getattr(node, "name", "")
    return _Span(start, node.end_lineno or node.lineno, f"{kind} {prefix}{name}")  # type: ignore[attr-defined]


def _python_spans(source: str, cfg: ChunkConfig) -> list[_Span] | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    spans: list[_Span] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        span = _py_node_span(node)
        if isinstance(node, ast.ClassDef) and span.end - span.start + 1 > cfg.max_lines:
            members = [
                _py_node_span(child, f"{node.name}.")
                for child in node.body
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            ]
            if members:
                # Class header (decorators, docstring, fields) up to the first method.
                spans.append(_Span(span.start, members[0].start - 1, f"class {node.name}"))
                spans.extend(members)
                if members[-1].end < span.end:
                    spans.append(_Span(members[-1].end + 1, span.end, f"class {node.name}"))
                continue
        spans.append(span)
    return spans


# --------------------------------------------------------------------------------------------
# Brace languages (Java / TypeScript / JavaScript)
# --------------------------------------------------------------------------------------------
_JAVA_DECL = re.compile(
    r"^\s*(?:@\w+\b(?:\([^)]*\))?\s*)*"
    r"(?:(?:public|protected|private|static|final|abstract|sealed|non-sealed|synchronized|"
    r"default|native|strictfp)\s+)*"
    r"(?:(class|interface|enum|record)\s+(\w+)|(?:[\w<>\[\],.?\s]+?\s+)?(\w+)\s*\([^;]*$)"
)
_TS_DECL = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?(?:async\s+)?"
    r"(?:(class|interface|enum|function\*?|type)\s+(\w+)|(?:const|let|var)\s+(\w+)\s*[:=])"
)
_TS_MEMBER = re.compile(
    r"^\s*(?:(?:public|private|protected|static|readonly|async|override|get|set)\s+)*"
    r"(\w+)\s*(?:<[^>]*>)?\s*\(.*\)\s*(?::\s*[^{]+)?\{?\s*$"
)
_TS_TEST = re.compile(r"""^\s*(describe|it|test)\s*\(\s*['"`]([^'"`]{1,60})""")
_DECORATOR = re.compile(r"^\s*@\w+")
_CLOSER = re.compile(r"^\s*[})\]]+[;,]?\s*$")
_CONTAINER_KINDS = ("class", "interface", "enum", "record", "describe")
_CONTROL_WORDS = {"if", "for", "while", "switch", "catch", "return", "new", "else", "do", "try"}


def _brace_depths(lines: list[str]) -> list[tuple[int, int]]:
    """(depth at line start, depth at line end) per line, skipping strings and comments."""
    depths: list[tuple[int, int]] = []
    depth = 0
    in_block_comment = False
    for line in lines:
        start_depth = depth
        i = 0
        quote: str | None = None
        while i < len(line):
            ch = line[i]
            nxt = line[i + 1] if i + 1 < len(line) else ""
            if in_block_comment:
                if ch == "*" and nxt == "/":
                    in_block_comment = False
                    i += 1
            elif quote:
                if ch == "\\":
                    i += 1
                elif ch == quote:
                    quote = None
            elif ch == "/" and nxt == "/":
                break
            elif ch == "/" and nxt == "*":
                in_block_comment = True
                i += 1
            elif ch in "\"'`":
                quote = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth = max(0, depth - 1)
            i += 1
        depths.append((start_depth, depth))
    return depths


def _decl_symbol(line: str, language: str) -> str | None:
    if language == "java":
        m = _JAVA_DECL.match(line)
        if not m:
            return None
        if m.group(1):
            return f"{m.group(1)} {m.group(2)}"
        name = m.group(3)
        return None if name in _CONTROL_WORDS else f"method {name}"
    m = _TS_TEST.match(line)
    if m:
        return f"{m.group(1)} '{m.group(2)}'"
    m = _TS_DECL.match(line)
    if m:
        if m.group(1):
            return f"{m.group(1).rstrip('*')} {m.group(2)}"
        return f"const {m.group(3)}"
    m = _TS_MEMBER.match(line)
    if m and m.group(1) not in _CONTROL_WORDS:
        return f"method {m.group(1)}"
    return None


def _block_end(depths: list[tuple[int, int]], start_idx: int, base: int) -> int:
    """Index of the line where the block opened at/after start_idx closes back to `base`."""
    opened = False
    for i in range(start_idx, len(depths)):
        _, end_depth = depths[i]
        if end_depth > base:
            opened = True
        if opened and end_depth <= base:
            return i
        if not opened and i > start_idx + 3:  # declaration without a body (e.g. `type X = ...;`)
            return start_idx
    return len(depths) - 1


def _brace_units(
    lines: list[str], depths: list[tuple[int, int]], lo: int, hi: int, base: int, language: str
) -> list[_Span]:
    """Declarations starting at brace depth `base` within lines[lo:hi]."""
    spans: list[_Span] = []
    decorator_start: int | None = None  # first line of pending (possibly multi-line) decorators
    i = lo
    while i < hi:
        start_depth, _ = depths[i]
        line = lines[i]
        if start_depth != base:
            if start_depth < base:  # e.g. the enclosing class line itself
                decorator_start = None
            i += 1  # deeper lines: decorator arguments or bodies
            continue
        symbol = _decl_symbol(line, language)
        if symbol is None:
            if _DECORATOR.match(line):
                decorator_start = i if decorator_start is None else decorator_start
            elif line.strip() and not _CLOSER.match(line) and not line.strip().startswith("//"):
                decorator_start = None
            i += 1
            continue
        first = decorator_start if decorator_start is not None else i
        decorator_start = None
        end = _block_end(depths, i, base)
        spans.append(_Span(first + 1, end + 1, symbol))
        i = end + 1
    return spans


def _brace_spans(lines: list[str], language: str, cfg: ChunkConfig) -> list[_Span]:
    depths = _brace_depths(lines)
    top = _brace_units(lines, depths, 0, len(lines), 0, language)
    spans: list[_Span] = []
    for span in top:
        kind = span.symbol.split(" ", 1)[0]
        too_big = span.end - span.start + 1 > cfg.max_lines
        if too_big and kind in _CONTAINER_KINDS:
            members = _brace_units(lines, depths, span.start - 1, span.end, 1, language)
            if members:
                spans.append(_Span(span.start, members[0].start - 1, span.symbol))
                spans.extend(
                    _Span(m.start, m.end, f"{span.symbol.split(' ', 1)[1]} > {m.symbol}")
                    for m in members
                )
                if members[-1].end < span.end:
                    spans.append(_Span(members[-1].end + 1, span.end, span.symbol))
                continue
        spans.append(span)
    return spans


# --------------------------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------------------------
_HEADING = re.compile(r"^(#{1,4})\s+(.+)$")


def _markdown_spans(lines: list[str]) -> list[_Span]:
    heads = [(i, m.group(2).strip()) for i, line in enumerate(lines) if (m := _HEADING.match(line))]
    spans = []
    for n, (i, title) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        spans.append(_Span(i + 1, end, f"section {title[:60]}"))
    return spans


# --------------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------------
def chunk_file(path: str, text: str, cfg: ChunkConfig | None = None) -> list[Chunk]:
    cfg = cfg or ChunkConfig()
    language = detect_language(path)
    lines = text.splitlines()
    if not lines:
        return []
    spans: list[_Span] | None = None
    if language == "python":
        spans = _python_spans(text, cfg)
    elif language in BRACE_LANGUAGES:
        spans = _brace_spans(lines, language, cfg)
    elif language == "markdown":
        spans = _markdown_spans(lines)
    if not spans:
        spans = list(_windows(1, len(lines), cfg))
        return [
            Chunk(path, language, s.start, s.end, "\n".join(lines[s.start - 1 : s.end]))
            for s in spans
            if not _is_blank(lines, s)
        ]
    return _finalize(path, language, lines, spans, cfg)
