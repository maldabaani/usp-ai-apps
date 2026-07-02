"""Node 2: detect ambiguities in the SDD and pause the graph for human clarification."""
from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

from config import settings
from pipeline.nodes.json_response import extract_json, extract_text
from pipeline.nodes.llm_retry import invoke_and_parse_with_retry
from pipeline.state import StoryForgeState

logger = logging.getLogger(__name__)

# temperature=0 + a fixed seed makes the same SDD produce the same
# clarification questions on every run instead of a different set each time.
BASE_SEED = 42

_llm = ChatOllama(
    model=settings.OLLAMA_LLM_MODEL,
    base_url=settings.OLLAMA_BASE_URL,
    num_predict=2048,
    temperature=0,
    seed=BASE_SEED,
)

CLARIFY_SYSTEM_PROMPT = """You are a senior business analyst. You will be given a Solution Design Document (SDD) along with retrieved context from the existing codebase, user manuals, and JPA entities.

Read the SDD carefully and identify questions that a developer MUST have answered before writing code. Think like a skeptical developer who needs every detail to be explicit.

Look for:
1. Missing field values — are all status codes, error codes, enums, and constants explicitly named?
2. Incomplete API specs — does every mentioned endpoint have a clear path, method, request body, and response structure?
3. Vague behavior — are there requirements where two developers could reasonably make different implementation choices?
4. Unresolved conflicts — does anything in the SDD contradict what you see in the retrieved codebase or user manual context?
5. Missing data details — are database fields, data types, or constraints assumed but not stated?

For every gap you find, write a specific question referencing the exact requirement or section. Do not ask about things that are already clearly defined in the document.

Respond ONLY with a JSON object — no explanations outside the JSON:
{"ambiguities": ["your question 1", "your question 2", "your question 3"]}

If the document is genuinely complete and unambiguous, respond with:
{"ambiguities": []}"""


def _build_user_message(state: StoryForgeState) -> str:
    context = state["retrieved_context"]

    def _format_chunks(chunks: list[dict]) -> str:
        if not chunks:
            return "(none retrieved)"
        return "\n---\n".join(
            f"Source: {c['metadata'].get('source', 'unknown')}\n{c['content']}"
            for c in chunks
        )

    return (
        f"## Solution Design Document\n{state['solution_doc_text']}\n\n"
        f"## Retrieved User Manual Context\n{_format_chunks(context.get('manuals', []))}\n\n"
        f"## Retrieved Codebase Context\n{_format_chunks(context.get('codebase', []))}\n\n"
        f"## Retrieved JPA Entity Context\n{_format_chunks(context.get('entities', []))}\n"
    )


def _parse_ambiguities(raw_text: str) -> list[str]:
    parsed = extract_json(raw_text)
    # Direct list: ["q1", "q2"]
    if isinstance(parsed, list):
        return [str(item) for item in parsed if item]
    if isinstance(parsed, dict):
        # Expected: {"ambiguities": [...]}
        value = parsed.get("ambiguities")
        if isinstance(value, list):
            return [str(item) for item in value if item]
        # Wrapped: {"result": {"ambiguities": [...]}} etc.
        for key in ("result", "response", "output", "data"):
            inner = parsed.get(key)
            if isinstance(inner, dict):
                value = inner.get("ambiguities")
                if isinstance(value, list):
                    return [str(item) for item in value if item]
    return []


def _log_and_parse_ambiguities(raw_text: str) -> list[str]:
    logger.info("clarify_node raw LLM output (first 500 chars): %s", raw_text[:500])
    return _parse_ambiguities(raw_text)


async def clarify_node(state: StoryForgeState) -> StoryForgeState:
    """Ask the LLM to flag in-scope ambiguities; pause the graph if any are found."""
    try:
        ambiguities = await invoke_and_parse_with_retry(
            _llm,
            [
                SystemMessage(content=CLARIFY_SYSTEM_PROMPT),
                HumanMessage(content=_build_user_message(state)),
            ],
            _log_and_parse_ambiguities,
            extract_text,
            base_seed=BASE_SEED,
            node_name="clarify_node",
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to caller via state errors
        logger.exception("clarify_node failed after retries; proceeding without clarification")
        return {
            **state,
            "clarification_needed": False,
            "clarification_questions": [],
            "errors": state["errors"] + [f"clarify_node: {exc}"],
        }

    if ambiguities:
        return {
            **state,
            "clarification_needed": True,
            "clarification_questions": ambiguities,
            "status": "clarifying",
        }

    return {
        **state,
        "clarification_needed": False,
        "clarification_questions": [],
        "status": "generating",
    }
