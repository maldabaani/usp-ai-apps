from __future__ import annotations

from pydantic import BaseModel, Field

from app.llm.tokens import truncate_to_tokens
from app.rag.service import RagService
from app.tools.base import ToolSpec

OUTPUT_TOKENS = 2500


class SearchCodebaseArgs(BaseModel):
    query: str = Field(min_length=2, description="What you are looking for, in words or code.")
    k: int = Field(default=6, ge=1, le=20, description="Number of chunks to return.")
    path_filter: str | None = Field(
        default=None, description="Optional directory prefix or glob, e.g. 'app/' or '*.spec.ts'."
    )


def search_codebase_tool(rag: RagService, run_id: str) -> ToolSpec:
    async def handler(args: SearchCodebaseArgs) -> str:
        hits = await rag.search(run_id, args.query, args.k, args.path_filter)
        if not hits:
            return "(no matching code)"
        return truncate_to_tokens("\n\n".join(h.render() for h in hits), OUTPUT_TOKENS)

    return ToolSpec(
        "search_codebase",
        "Semantic search over the merged project code, design doc and stack rules. Returns "
        "chunks with path and line ranges (code from other in-flight tasks is not included).",
        SearchCodebaseArgs,
        handler,
    )
