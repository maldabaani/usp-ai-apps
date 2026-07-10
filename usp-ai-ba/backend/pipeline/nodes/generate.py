"""Node 3: generate User Stories, Dev Tasks, and Unit Test Tasks -- via local
Ollama by default, or Claude when settings.ASSESSMENT_MODEL == "claude" (with
an automatic fallback to Ollama if Claude fails; see llm_retry.py)."""
from __future__ import annotations

import logging

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

from config import settings
from pipeline.nodes import assessment_llm
from pipeline.nodes.json_response import extract_json, extract_text
from pipeline.nodes.llm_retry import invoke_and_parse_with_fallback
from pipeline.state import StoryForgeState
from prompts.system_prompt import SYSTEM_PROMPT as PRODUCTION_SYSTEM_PROMPT
from prompts.system_prompt_selftest import SYSTEM_PROMPT as SELFTEST_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# 8192 was observed in production to be too small: the schema requires 7
# populated sections per dev_task (incl. pseudocode) and 5 per unit_test_task,
# so a single fully-detailed epic can consume most or all of this budget --
# the model then gets cut off mid-array, and llm_retry.py's truncation check
# (added alongside this bump) now makes that a genuine retry/error instead of
# json_repair silently discarding every epic after the one being written.
# 16384 gives real headroom for multiple epics; if OLLAMA_NUM_CTX is left at
# its own default (32768), this leaves less room for RAG/SDD input context,
# so raise OLLAMA_NUM_CTX too if truncated-*input*-prompt warnings appear.
MAX_OUTPUT_TOKENS = 16384

# temperature=0 + a fixed seed makes the same SDD produce the same epics/
# stories on every run instead of a different set each time (Ollama only --
# Claude has no seed parameter; see llm_retry.py).
BASE_SEED = 42

SYSTEM_PROMPT = (
    SELFTEST_SYSTEM_PROMPT if settings.PROMPT_VARIANT == "selftest" else PRODUCTION_SYSTEM_PROMPT
)

_llm: ChatOllama | ChatAnthropic | None = None
_llm_generation = -1
_llm_model_kind: str | None = None

_fallback_llm: ChatOllama | None = None
_fallback_llm_generation = -1


def _get_llm() -> ChatOllama | ChatAnthropic:
    """Rebuilds only when settings.settings_generation has advanced (i.e. the
    settings screen changed OLLAMA_LLM_MODEL/OLLAMA_BASE_URL/ASSESSMENT_MODEL)
    rather than on every call -- previously this was a module-level singleton
    built once at import time, so a settings change silently had no effect
    until a process restart."""
    global _llm, _llm_generation, _llm_model_kind
    model_kind = settings.ASSESSMENT_MODEL
    if _llm is None or _llm_generation != settings.settings_generation or _llm_model_kind != model_kind:
        _llm = assessment_llm.build_llm(
            model_kind, ollama_num_predict=MAX_OUTPUT_TOKENS, claude_max_tokens=MAX_OUTPUT_TOKENS, seed=BASE_SEED
        )
        _llm_generation = settings.settings_generation
        _llm_model_kind = model_kind
    return _llm


def _get_ollama_fallback_llm() -> ChatOllama:
    """A guaranteed-Ollama client for when ASSESSMENT_MODEL == "claude" and
    the Claude call fails -- independent of _get_llm()'s own cache, which
    tracks whichever model is currently configured as primary."""
    global _fallback_llm, _fallback_llm_generation
    if _fallback_llm is None or _fallback_llm_generation != settings.settings_generation:
        _fallback_llm = assessment_llm.build_ollama_llm(num_predict=MAX_OUTPUT_TOKENS, seed=BASE_SEED)
        _fallback_llm_generation = settings.settings_generation
    return _fallback_llm


def _format_chunks(chunks: list[dict]) -> str:
    if not chunks:
        return "(none retrieved)"
    return "\n---\n".join(
        f"Source: {c['metadata'].get('source', 'unknown')} "
        f"[type={c['metadata'].get('type', 'unknown')}, "
        f"layer={c['metadata'].get('layer', 'unknown')}, "
        f"module={c['metadata'].get('module', 'unknown')}]\n{c['content']}"
        for c in chunks
    )


def _format_clarifications(answers: dict) -> str:
    if not answers:
        return "(no clarifications were needed)"
    return "\n".join(f"Q: {question}\nA: {answer}" for question, answer in answers.items())


def _build_user_message(state: StoryForgeState) -> str:
    context = state["retrieved_context"]
    return (
        f"## Project Metadata\n"
        f"PPM Number: {state['ppm_number']}\n"
        f"PPM Name: {state['ppm_name']}\n"
        f"System Name: {state['system_name']}\n\n"
        f"## Solution Design Document\n{state['solution_doc_text']}\n\n"
        f"## Retrieved User Manual Context\n{_format_chunks(context.get('manuals', []))}\n\n"
        f"## Retrieved Codebase Context\n{_format_chunks(context.get('codebase', []))}\n\n"
        f"## Retrieved JPA Entity Context\n{_format_chunks(context.get('entities', []))}\n\n"
        f"## Clarification Answers\n{_format_clarifications(state['clarification_answers'])}\n"
    )


_REQUIRED_KEYS = {"epic_title", "user_story", "acceptance_criteria", "dev_tasks", "unit_test_tasks"}


def _parse_stories(raw_text: str) -> list[dict]:
    parsed = extract_json(raw_text)
    # Some models wrap the list: {"stories": [...]}
    if isinstance(parsed, dict):
        for key in ("stories", "epics", "user_stories", "results"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
    if not isinstance(parsed, list):
        raise ValueError("Expected a JSON array of stories")
    stories = []
    for item in parsed:
        if not isinstance(item, dict):
            logger.warning("Skipping non-dict story item: %r", item)
            continue
        # Normalise common alternate key names the local model sometimes uses
        if "title" in item and "epic_title" not in item:
            item["epic_title"] = item.pop("title")
        if "story" in item and "user_story" not in item:
            item["user_story"] = item.pop("story")
        # Fill in any missing required keys with safe defaults
        item.setdefault("epic_title", "")
        item.setdefault("user_story", "")
        item.setdefault("acceptance_criteria", [])
        item.setdefault("dev_tasks", [])
        item.setdefault("unit_test_tasks", [])
        stories.append(item)
    return stories


def _log_and_parse_stories(raw_text: str) -> list[dict]:
    logger.info("generate_node raw LLM output (first 500 chars): %s", raw_text[:500])
    return _parse_stories(raw_text)


async def generate_node(state: StoryForgeState) -> StoryForgeState:
    """Send the SDD + RAG context + clarification answers to the LLM and parse stories."""
    fallback_llm = _get_ollama_fallback_llm() if settings.ASSESSMENT_MODEL == "claude" else None
    try:
        stories = await invoke_and_parse_with_fallback(
            _get_llm(),
            fallback_llm,
            [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=_build_user_message(state)),
            ],
            _log_and_parse_stories,
            extract_text,
            base_seed=BASE_SEED,
            node_name="generate_node",
            supports_seed=settings.ASSESSMENT_MODEL != "claude",
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to caller via state errors
        logger.exception("generate_node failed after retries")
        return {
            **state,
            "errors": state["errors"] + [f"generate_node: {exc}"],
            "status": "error",
        }

    return {
        **state,
        "generated_stories": stories,
        "status": "reviewing" if state["review_mode"] else "creating",
    }
