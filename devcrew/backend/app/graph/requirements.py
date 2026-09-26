"""Long requirements documents (Phase 15, BL-100).

Requests up to MAX_REQUEST_CHARS go to the Planner and Architect as they are. Longer documents
(up to MAX_DOCUMENT_CHARS) are condensed once, part by part, into a digest that fits; the
Planner can still look things up in the full document with `search_requirements`.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.events.types import EventType
from app.graph.runtime import GraphDeps
from app.llm.models_config import Role
from app.tools.base import ToolSpec

TRUNCATED = "\n\n[digest truncated to fit; use search_requirements for the rest]"


def effective_request(state: dict[str, Any]) -> str:
    """What the Planner and Architect read: the digest of a long document, else the request."""
    return str(state.get("request_digest") or state["request"])


def split_parts(text: str, size: int) -> list[str]:
    """Split at paragraph boundaries into parts of at most `size` characters."""
    parts: list[str] = []
    current = ""
    for block in re.split(r"\n\s*\n", text):
        while len(block) > size:  # a single huge paragraph
            parts.append(block[:size])
            block = block[size:]
        if current and len(current) + len(block) + 2 > size:
            parts.append(current)
            current = ""
        current = f"{current}\n\n{block}" if current else block
    if current:
        parts.append(current)
    return parts


async def condense(deps: GraphDeps, run_id: str, request: str) -> str:
    limit = deps.settings.max_request_chars
    parts = split_parts(request, limit)
    budget = max(200, int(limit * 0.85 / len(parts)))
    system = deps.prompts.get("condense_requirements")
    await deps.emit(
        run_id,
        EventType.TOOL_CALL,
        node="planner",
        tool="condense_requirements",
        args={"step": f"{len(request):,} characters in {len(parts)} parts"},
    )
    digests = []
    for i, part in enumerate(parts, start=1):
        reply = await deps.llm.ainvoke(
            Role.PLANNER,
            [
                SystemMessage(content=system),
                HumanMessage(
                    content=f"Part {i} of {len(parts)}. Stay under {budget} characters.\n\n{part}"
                ),
            ],
        )
        digests.append(str(reply.content).strip())
    digest = (
        f"(Condensed from a {len(request):,}-character requirements document; the full text "
        "is searchable with search_requirements.)\n\n" + "\n\n".join(digests)
    )
    if len(digest) > limit:
        digest = digest[: limit - len(TRUNCATED)] + TRUNCATED
    await deps.emit(
        run_id,
        EventType.TOOL_RESULT,
        node="planner",
        tool="condense_requirements",
        ok=True,
        result=f"digest of {len(digest):,} characters",
    )
    return digest


def with_digest(
    deps: GraphDeps, node: Callable[[dict[str, Any]], Awaitable[Command[str]]]
) -> Callable[[dict[str, Any]], Awaitable[Command[str]]]:
    """Condense a long request once (before the Planner's first turn)."""

    async def wrapped(state: dict[str, Any]) -> Command[str]:
        if state.get("request_digest") or len(state["request"]) <= deps.settings.max_request_chars:
            return await node(state)
        digest = await condense(deps, state["run_id"], state["request"])
        command = await node({**state, "request_digest": digest})
        return Command(
            goto=command.goto, update={"request_digest": digest, **(command.update or {})}
        )

    return wrapped


class SearchRequirementsArgs(BaseModel):
    query: str = Field(min_length=2, description="Words to look for in the full document.")


def search_requirements_tool(request: str, *, k: int = 3, window: int = 1500) -> ToolSpec:
    """Keyword search over the full requirements document (paragraphs, with their heading)."""
    heading = ""
    sections: list[tuple[str, str]] = []
    for block in re.split(r"\n\s*\n", request):
        if block.lstrip().startswith("#"):
            heading = block.strip().splitlines()[0]
        sections.append((heading, block.strip()))

    async def handler(args: SearchRequirementsArgs) -> str:
        terms = [t for t in re.findall(r"\w+", args.query.lower()) if len(t) > 2]
        scored = []
        for i, (head, text) in enumerate(sections):
            low = text.lower()
            score = sum(low.count(t) for t in terms)
            if score:
                scored.append((score, i, head, text))
        if not scored:
            return "No part of the document mentions that."
        best = sorted(scored, key=lambda s: (-s[0], s[1]))[:k]
        return "\n\n---\n\n".join(
            (f"[{head}]\n" if head and head != text.splitlines()[0] else "") + text[:window]
            for _, _, head, text in sorted(best, key=lambda s: s[1])
        )

    return ToolSpec(
        "search_requirements",
        "Search the FULL requirements document (you see a condensed digest). Use it to check "
        "details: field names, rules, limits, examples.",
        SearchRequirementsArgs,
        handler,
    )
