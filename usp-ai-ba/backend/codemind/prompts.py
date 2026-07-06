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
    '"dependencies": ["..."]}\n\n'
    'For each entry in "rules", state the underlying decision, not the mechanism that carries it out:\n'
    '- "conditions" should capture the circumstance(s) that must hold for the rule to apply -- an input value, '
    "a configuration or permission state, a role, or the outcome of a prior step -- expressed in domain terms, "
    "not code syntax.\n"
    '- "actions" should capture the resulting behavior or constraint, likewise in domain terms.\n'
    '- "description" should read as a single self-contained sentence of the form "if/when <condition>, then '
    '<outcome>," understandable without knowing which class or function it came from.\n\n'
    "Before including a rule, check whether it would still be true regardless of implementation. If a rule "
    'only restates which method, library, or API is invoked (for example, "uses X to call Y"), it describes a '
    "mechanism, not logic -- omit it, or reframe it around the conditional behavior it encodes. Prioritize "
    "rules governing authorization and permissions, validation and eligibility, thresholds and limits, state "
    "transitions, and error or fallback handling, since this is where genuine business logic most often "
    'resides. If the file contains no such decisions, return an empty "rules" array rather than listing its '
    "mechanics."
)


def build_system_prompt(lang: Language) -> str:
    hint = _LANGUAGE_HINTS.get(lang, _DEFAULT_HINT)
    return (
        f"You are a senior {lang.display_name} engineer conducting a logic-extraction review of source code "
        "for technical documentation and system-migration purposes.\n\n"
        "Your objective is to explain the reasoning behind the code's behavior, not to catalogue what it "
        "calls or how it is structured. A business rule is a decision the code makes: a condition under which "
        "something happens, and the outcome that follows. Naming which function, library, or framework "
        "feature implements that decision is not, by itself, a business rule.\n\n"
        f"{hint}"
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
