"""Exposes StoryForge's own persistent, ingested corpus (sf_codebase,
sf_jpa_entities, sf_user_manuals -- built by usp-ai-ba/backend/ingestion/,
covering the whole repo plus PDF/Word manuals) to DevCrew's agents as an
extra tool, alongside (not replacing) their own per-run, code-only,
ephemeral search_codebase tool (devcrew/tools/search.py). Different
purposes: search_codebase is commit-accurate for this run's in-flight
edits; this tool is broader (includes manuals, JPA entities) but can be
stale relative to a run's uncommitted changes -- an agent decides which
one answers its question. See the merge plan's Phase 4 (RAG unification).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from pydantic import BaseModel, Field

from devcrew.llm.tokens import truncate_to_tokens
from devcrew.tools.base import ToolSpec

OUTPUT_TOKENS = 2500

RetrievalFn = Callable[[str, int], Awaitable[dict[str, list[dict]]]]


class RetrieveStoryForgeContextArgs(BaseModel):
    query: str = Field(
        min_length=2, description="What you're looking for, in words -- a question or topic."
    )
    top_k: int = Field(
        default=5, ge=1, le=15, description="Chunks to return per collection (manuals/code/entities)."
    )


def _render_collection(name: str, chunks: list[dict]) -> str:
    if not chunks:
        return ""
    rendered = "\n\n".join(
        f"--- {c['metadata'].get('source', 'unknown')} "
        f"[type={c['metadata'].get('type', 'unknown')}]\n{c['content']}"
        for c in chunks
    )
    return f"## {name}\n{rendered}"


def retrieve_storyforge_context_tool(retrieve: RetrievalFn) -> ToolSpec:
    async def handler(args: RetrieveStoryForgeContextArgs) -> str:
        result = await retrieve(args.query, args.top_k)
        sections = [
            _render_collection(label, result.get(key, []))
            for key, label in (
                ("manuals", "User Manuals"),
                ("codebase", "Codebase (StoryForge's full ingested corpus)"),
                ("entities", "JPA Entities"),
            )
        ]
        rendered = "\n\n".join(s for s in sections if s)
        if not rendered:
            return "(no matching content in StoryForge's ingested corpus)"
        return truncate_to_tokens(rendered, OUTPUT_TOKENS)

    return ToolSpec(
        "retrieve_storyforge_context",
        "Search StoryForge's persistent, already-ingested corpus for this project: the full "
        "codebase (not just this run's checked-out copy), JPA entities, and user manuals/business "
        "documents. Broader than search_codebase (includes manuals) but may be stale relative to "
        "this run's own in-flight, uncommitted edits -- prefer search_codebase for code you or "
        "another task in this run just wrote.",
        RetrieveStoryForgeContextArgs,
        handler,
    )
