"""Builds the system/user prompt for a single-file extraction call.

Ported from com.jslogicextractor.prompt.LogicExtractionPromptTemplates --
despite the Java package name, that source never actually loads a
StringTemplate (.st) file; the prompt is built via plain string
concatenation, so this port is a like-for-like string-formatter translation,
not a templating-engine port.
"""
from __future__ import annotations

from codemind.models import Language, SourceFile

_LANGUAGE_HINTS: dict[Language, str] = {
    Language.JAVASCRIPT: "Focus on event handlers, async flows, module exports, and business rules embedded in callbacks or promises.",
    Language.TYPESCRIPT: "Focus on typed interfaces, generics, decorators, async flows, and business rules expressed through types and class methods.",
    Language.PYTHON: "Focus on class methods, decorators, async/await patterns, data transformations, and domain logic in functions or modules.",
    Language.JAVA: "Focus on class hierarchies, design patterns (repository, service, factory), annotations, exception handling, and domain methods.",
    Language.KOTLIN: "Focus on data classes, extension functions, coroutines, sealed classes, and domain-layer logic.",
    Language.GO: "Focus on function signatures, interfaces, goroutine coordination, error handling patterns, and business rules in handlers or services.",
    Language.CSHARP: "Focus on class hierarchies, LINQ expressions, async/await patterns, attributes, and business rules in services or controllers.",
    Language.RUBY: "Focus on modules, mixins, blocks/procs, model callbacks and associations, and domain logic in service objects or models.",
    Language.RUST: "Focus on trait implementations, ownership patterns, error handling with Result/Option, and business logic in structs and enums.",
    Language.PHP: "Focus on class hierarchies, framework conventions, model associations, middleware, and business rules in service classes.",
}

_DEFAULT_HINT = "Focus on the business logic, data flows, domain rules, and key abstractions expressed in the code."

_OUTPUT_INSTRUCTION = (
    "Extract the business logic of the file above and respond with JSON only (no markdown fences, no commentary) using this shape:\n"
    '{"file": "<filePath>", "summary": "one paragraph summary", '
    '"rules": [{"name": "...", "description": "...", "conditions": ["..."], "actions": ["..."]}], '
    '"dependencies": ["..."]}'
)


def build_system_prompt(lang: Language) -> str:
    hint = _LANGUAGE_HINTS.get(lang, _DEFAULT_HINT)
    return (
        f"You are a senior {lang.display_name} engineer extracting business logic "
        f"from source code for documentation and migration purposes.\n\n{hint}"
    )


def build_user_content(file: SourceFile, lang: Language) -> str:
    return (
        f"File name: {_file_name(file)}\n"
        f"File path: {file.relative_path}\n\n"
        f"Source:\n```{lang.code_fence}\n{file.content}\n```\n\n"
        f"{_OUTPUT_INSTRUCTION}"
    )


def build_extraction_messages(file: SourceFile) -> tuple[str, str]:
    """Returns (system_message, user_message) for a single-file extraction call."""
    lang = Language.from_path(file.relative_path)
    return build_system_prompt(lang), build_user_content(file, lang)


def render_static_system_skeleton(lang: Language = Language.JAVASCRIPT) -> str:
    return build_system_prompt(lang)


def render_user_content(file: SourceFile) -> str:
    lang = Language.from_path(file.relative_path)
    return build_user_content(file, lang)


def _file_name(file: SourceFile) -> str:
    idx = file.relative_path.rfind("/")
    return file.relative_path[idx + 1 :] if idx >= 0 else file.relative_path
