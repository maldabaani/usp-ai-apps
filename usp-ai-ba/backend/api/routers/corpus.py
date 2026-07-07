"""Corpus browser: per-source metadata (chunk count, whether an LLM summary
exists, format, last-ingested time) for the manuals/codebase collections --
file list + metadata only, no full chunk-content drill-down. The entities
collection is intentionally excluded: it's a derived re-indexing of Java
@Entity files already counted under codebase, not a distinct source
population.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.deps import require_admin, require_auth
from ingestion.chroma_client import delete_by_source, source_metadata

router = APIRouter(prefix="/corpus", tags=["corpus"])


class DeleteSourceRequest(BaseModel):
    collection_key: Literal["manuals", "codebase"]
    source: str


@router.get("/sources")
async def get_corpus_sources(user: dict = Depends(require_auth)):
    return {
        "manuals": await source_metadata("manuals"),
        "codebase": await source_metadata("codebase"),
    }


@router.post("/sources/delete")
async def delete_corpus_source(request: DeleteSourceRequest, user: dict = Depends(require_admin)):
    """Removes one source's chunks from the corpus. Idempotent -- deleting an
    already-gone or never-ingested source is not an error, matching
    delete_by_source's own no-op-if-nothing-matches semantics.

    Deleting a "codebase" source also clears "entities", mirroring
    ingest_code.py's own per-file loop (a Java @Entity class is written to
    both collections, and its entities-collection chunks must never be left
    behind as an orphaned entry once "codebase" no longer has it).

    Known v1 limitation: this does not touch either tier's per-repo
    content-hash manifest (ingest_code.py's own chunking-skip manifest, nor
    enrichment's separate summarization-skip manifest). If the source file
    still exists on disk and its parent repo is later fully re-ingested
    without that file's content changing, BOTH tiers' incremental-skip logic
    still treats it as unchanged and neither the mechanical chunks nor the
    LLM-summary chunk come back -- the source stays gone from the corpus
    until either its content actually changes or that ingestion run passes
    force_full_rechunk=true. Not solved here: nothing in the corpus browser
    tracks which repo's manifests a given "codebase" source belongs to.
    """
    await delete_by_source(request.collection_key, request.source)
    if request.collection_key == "codebase":
        await delete_by_source("entities", request.source)
    return {"status": "deleted"}
