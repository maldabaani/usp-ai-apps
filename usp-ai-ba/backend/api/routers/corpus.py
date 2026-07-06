"""Corpus browser: per-source metadata (chunk count, whether an LLM summary
exists, format, last-ingested time) for the manuals/codebase collections --
file list + metadata only, no full chunk-content drill-down. The entities
collection is intentionally excluded: it's a derived re-indexing of Java
@Entity files already counted under codebase, not a distinct source
population.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import require_auth
from ingestion.chroma_client import source_metadata

router = APIRouter(prefix="/corpus", tags=["corpus"])


@router.get("/sources")
async def get_corpus_sources(user: dict = Depends(require_auth)):
    return {
        "manuals": await source_metadata("manuals"),
        "codebase": await source_metadata("codebase"),
    }
