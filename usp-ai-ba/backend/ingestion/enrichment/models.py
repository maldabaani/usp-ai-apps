"""Shared value types used across the ingestion enrichment package: Language
and SourceFile.

Moved verbatim from codemind/models.py (which was itself ported from
com.jslogicextractor.scanner.{Language,SourceFile}) as part of unifying
CodeMind's per-file LLM extraction into the ChromaDB ingestion pipeline --
see plan file section I. Kept in their own module (rather than living in
enrich.py) since chunker.py, filter.py, prompts.py, and manifest.py all need
them without needing enrich.py's own orchestration logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Language(Enum):
    JAVASCRIPT = ("JavaScript", "javascript", (".js", ".jsx", ".mjs", ".cjs"))
    TYPESCRIPT = ("TypeScript", "typescript", (".ts", ".tsx"))
    PYTHON = ("Python", "python", (".py", ".pyw"))
    JAVA = ("Java", "java", (".java",))
    KOTLIN = ("Kotlin", "kotlin", (".kt", ".kts"))
    GO = ("Go", "go", (".go",))
    CSHARP = ("C#", "csharp", (".cs",))
    RUBY = ("Ruby", "ruby", (".rb",))
    RUST = ("Rust", "rust", (".rs",))
    PHP = ("PHP", "php", (".php",))
    UNKNOWN = ("Unknown", "text", ())

    def __init__(self, display_name: str, code_fence: str, extensions: tuple[str, ...]):
        self.display_name = display_name
        self.code_fence = code_fence
        self.extensions = extensions

    @staticmethod
    def from_path(relative_path: str | None) -> "Language":
        if not relative_path:
            return Language.UNKNOWN
        lower = relative_path.lower()
        for lang in Language:
            for ext in lang.extensions:
                if lower.endswith(ext):
                    return lang
        return Language.UNKNOWN


@dataclass(frozen=True)
class SourceFile:
    absolute_path: Path
    relative_path: str
    content: str
    size_bytes: int
