"""Builds the system/user prompt for a single-document business-rule
extraction call -- the document-tier counterpart to prompts.py's code-oriented
build_extraction_messages, used by ingestion/enrichment/enrich_documents.py
(see plan file section Q) via agents/{claude_agent,ollama_agent}.py's
``build_messages`` override.

Flattened relative to prompts.py: there's only one kind of document content
(extracted manual prose), so no per-Language dispatch, and no code-only
"dependencies" field in the output schema.
"""
from __future__ import annotations

from ingestion.enrichment.models import SourceFile

_SYSTEM_PROMPT = (
    "You are a business analyst conducting a policy-extraction review of an "
    "internal business/user manual, for technical documentation and "
    "system-migration purposes.\n\n"
    "Your objective is to extract the conditional business rules and "
    "procedures the document describes, not to summarize its narrative "
    "structure or restate its section headings. A business rule is a "
    "decision the document specifies: a condition or circumstance under "
    "which something happens, and the outcome or required action that "
    "follows. Restating which section, form, or system screen something "
    "appears on is not, by itself, a business rule.\n\n"
    "Focus on policies, eligibility/validation criteria, thresholds and "
    "limits, approval/escalation procedures, and exception or fallback "
    "handling described in the text."
)

_OUTPUT_INSTRUCTION = (
    "Extract the business rules of the document above and respond with JSON only "
    "(no markdown fences, no commentary) using this shape:\n"
    '{"file": "<relativePath>", "summary": "one paragraph summary", '
    '"rules": [{"name": "...", "description": "...", "conditions": ["..."], "actions": ["..."]}]}\n\n'
    'For each entry in "rules", state the underlying policy, not the document mechanics that describe it: '
    '"conditions" capture the circumstance(s) that must hold (a customer/account state, an input value, a '
    "threshold, an approval level, or a prior step's outcome) in domain terms, not document formatting; "
    '"actions" capture the resulting behavior/requirement/constraint, likewise in domain terms; "description" '
    'reads as one self-contained "if/when <condition>, then <outcome>" sentence, understandable without '
    "knowing which page or section it came from. Before including a rule, check whether it would still be "
    "true regardless of which document describes it -- if a rule only restates which form/field/screen is "
    "involved, it describes document structure, not a rule; omit it or reframe it around the conditional "
    'behavior it encodes. If the document contains no such rules, return an empty "rules" array rather than '
    "summarizing its layout."
)


def build_system_prompt() -> str:
    return _SYSTEM_PROMPT


def build_user_content(file: SourceFile) -> str:
    return f"Document: {file.relative_path}\n\nContent:\n{file.content}\n\n{_OUTPUT_INSTRUCTION}"


def build_extraction_messages(file: SourceFile) -> tuple[str, str]:
    """Returns (system_message, user_message) for a single-document extraction call."""
    return build_system_prompt(), build_user_content(file)
