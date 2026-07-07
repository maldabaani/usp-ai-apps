"""Node 3: generate User Stories, Dev Tasks, and Unit Test Tasks via a local Ollama model."""
from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

from config import settings
from pipeline.nodes.json_response import extract_json, extract_text
from pipeline.nodes.llm_retry import invoke_and_parse_with_retry
from pipeline.state import StoryForgeState
from prompts.system_prompt import SYSTEM_PROMPT as PRODUCTION_SYSTEM_PROMPT
from prompts.system_prompt_selftest import SYSTEM_PROMPT as SELFTEST_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 8192

# temperature=0 + a fixed seed makes the same SDD produce the same epics/
# stories on every run instead of a different set each time.
BASE_SEED = 42

SYSTEM_PROMPT = (
    SELFTEST_SYSTEM_PROMPT if settings.PROMPT_VARIANT == "selftest" else PRODUCTION_SYSTEM_PROMPT
)

_llm: ChatOllama | None = None
_llm_generation = -1


def _get_llm() -> ChatOllama:
    """Rebuilds only when settings.settings_generation has advanced (i.e. the
    settings screen changed OLLAMA_LLM_MODEL/OLLAMA_BASE_URL) rather than on
    every call -- previously this was a module-level singleton built once at
    import time, so a settings change silently had no effect until a process
    restart."""
    global _llm, _llm_generation
    if _llm is None or _llm_generation != settings.settings_generation:
        _llm = ChatOllama(
            model=settings.OLLAMA_LLM_MODEL,
            base_url=settings.OLLAMA_BASE_URL,
            num_predict=MAX_OUTPUT_TOKENS,
            num_ctx=settings.OLLAMA_NUM_CTX,
            temperature=0,
            seed=BASE_SEED,
            timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS,
        )
        _llm_generation = settings.settings_generation
    return _llm


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
    try:
        stories = await invoke_and_parse_with_retry(
            _get_llm(),
            [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=_build_user_message(state)),
            ],
            _log_and_parse_stories,
            extract_text,
            base_seed=BASE_SEED,
            node_name="generate_node",
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
