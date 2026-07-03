"""Shared value types used across the codemind package: Language and SourceFile.

Ported from com.jslogicextractor.scanner.{Language,SourceFile}. Kept in their
own module (rather than living in scanner.py, as in the Java package
structure) since chunker.py, filter.py, prompts.py, and manifest.py all need
them without needing scanner.py's directory-walking logic.
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
