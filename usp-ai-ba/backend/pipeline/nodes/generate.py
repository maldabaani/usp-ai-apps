"""Node 3: generate User Stories, Dev Tasks, and Unit Test Tasks via Claude Sonnet."""
from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

from config import settings
from pipeline.nodes.json_response import extract_json, extract_text
from pipeline.state import StoryForgeState
from prompts.system_prompt import SYSTEM_PROMPT as PRODUCTION_SYSTEM_PROMPT
from prompts.system_prompt_selftest import SYSTEM_PROMPT as SELFTEST_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 8192

SYSTEM_PROMPT = (
    SELFTEST_SYSTEM_PROMPT if settings.PROMPT_VARIANT == "selftest" else PRODUCTION_SYSTEM_PROMPT
)

_llm = ChatOllama(
    model=settings.OLLAMA_LLM_MODEL,
    base_url=settings.OLLAMA_BASE_URL,
    num_predict=MAX_OUTPUT_TOKENS,
)


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
        # Normalise common alternate key names from non-Claude models
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


async def generate_node(state: StoryForgeState) -> StoryForgeState:
    """Send the SDD + RAG context + clarification answers to the LLM and parse stories."""
    try:
        response = await _llm.ainvoke(
            [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=_build_user_message(state)),
            ]
        )
        raw_text = extract_text(response.content)
        logger.info("generate_node raw LLM output (first 500 chars): %s", raw_text[:500])
        stories = _parse_stories(raw_text)
    except Exception as exc:  # noqa: BLE001 - surfaced to caller via state errors
        logger.exception("generate_node failed")
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
