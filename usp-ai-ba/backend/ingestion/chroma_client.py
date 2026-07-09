"""Shared ChromaDB client setup. Three persistent collections are exposed:

- ``sf_user_manuals`` — chunks of User Manual PDFs
- ``sf_codebase``     — chunks of the indexed codebase (all languages/layers)
- ``sf_jpa_entities`` — JPA Entity classes only (used as the DB schema proxy)

Also exposes ``keyword_search`` — a literal-substring search over a
collection's chunks, unioned with vector search in ``ingestion/retrieval.py``
to guarantee an exact identifier/term mentioned in a question surfaces even
when it isn't semantically close enough to the question's phrasing to rank
in the vector search's own top-k.
"""
from __future__ import annotations

import asyncio
import re

import chromadb
from chromadb.config import Settings as ChromaSettings
from langchain_chroma import Chroma
from langchain_ollama import OllamaEmbeddings

from config import settings
from ingestion import ingestion_generation

COLLECTIONS = {
    "manuals": "sf_user_manuals",
    "codebase": "sf_codebase",
    "entities": "sf_jpa_entities",
}

_client: chromadb.ClientAPI | None = None
_embeddings: OllamaEmbeddings | None = None
_embeddings_generation = -1
_vector_stores: dict[str, Chroma] = {}
_vector_stores_generation = -1


def get_chroma_client() -> chromadb.ClientAPI:
    """Return a singleton persistent ChromaDB client."""
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(
            path=settings.CHROMA_PERSIST_PATH,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
    return _client


def get_embeddings() -> OllamaEmbeddings:
    """Return an Ollama embeddings client using the local nomic-embed-text
    model, rebuilt only when settings.settings_generation has advanced (i.e.
    OLLAMA_BASE_URL/OLLAMA_EMBED_MODEL changed via the settings screen) --
    previously a plain module-level singleton, so a settings change silently
    had no effect until a process restart."""
    global _embeddings, _embeddings_generation
    if _embeddings is None or _embeddings_generation != settings.settings_generation:
        _embeddings = OllamaEmbeddings(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.OLLAMA_EMBED_MODEL,
            num_ctx=settings.OLLAMA_EMBED_NUM_CTX,
        )
        _embeddings_generation = settings.settings_generation
    return _embeddings


def get_vector_store(collection_key: str) -> Chroma:
    """Return a LangChain Chroma vector store wrapper for one of the three collections.

    ``collection_key`` must be one of: "manuals", "codebase", "entities".

    Cached per collection, but the whole cache is dropped (not just
    individually rebuilt) when settings.settings_generation advances, since a
    cached Chroma wrapper bakes in whatever get_embeddings() returned at
    construction time -- a stale wrapper would otherwise keep using the old
    embedding model even after get_embeddings() itself starts returning a
    freshly-rebuilt client.
    """
    if collection_key not in COLLECTIONS:
        raise ValueError(
            f"Unknown collection key '{collection_key}'. Expected one of {list(COLLECTIONS)}."
        )

    global _vector_stores_generation
    if _vector_stores_generation != settings.settings_generation:
        _vector_stores.clear()
        _vector_stores_generation = settings.settings_generation

    if collection_key not in _vector_stores:
        _vector_stores[collection_key] = Chroma(
            client=get_chroma_client(),
            collection_name=COLLECTIONS[collection_key],
            embedding_function=get_embeddings(),
            persist_directory=settings.CHROMA_PERSIST_PATH,
        )
    return _vector_stores[collection_key]


def get_all_vector_stores() -> dict[str, Chroma]:
    """Return all three vector stores keyed by "manuals", "codebase", "entities"."""
    return {key: get_vector_store(key) for key in COLLECTIONS}


async def delete_by_source(collection_key: str, relative_path: str) -> None:
    """Deletes every existing chunk for one source file/document (matched by
    its "source" metadata field) from a collection, before that file's fresh
    chunks are re-added on a re-ingestion run. Deterministic per-chunk IDs
    (see ingest_code.py/ingest_documents.py) make an *unchanged* chunk's re-add a
    no-op upsert, but they can't clean up a chunk whose symbol/method was
    removed entirely (its old ID simply never appears in the new run) --
    this clears the file's whole prior chunk set first so removed methods/
    files don't leave stale, undiscoverable chunks behind forever.
    """
    vector_store = get_vector_store(collection_key)
    await vector_store.adelete(where={"source": relative_path})


async def delete_by_source_and_type(collection_key: str, relative_path: str, doc_type: str) -> None:
    """Like delete_by_source, but scoped to one "type" metadata value only --
    used by ingestion/enrichment/enrich.py to clear just its own prior
    "llm_summary" document(s) for a file before re-adding, without touching
    that same file's mechanically-chunked documents (a different "type")
    that ingest_code.py's raw-chunk tier separately owns via
    delete_by_source_excluding_type below. The two tiers must never delete
    each other's documents just because they share the same "source".
    """
    vector_store = get_vector_store(collection_key)
    await vector_store.adelete(where={"$and": [{"source": relative_path}, {"type": doc_type}]})


async def delete_by_source_excluding_type(collection_key: str, relative_path: str, exclude_type: str) -> None:
    """The complementary half of delete_by_source_and_type: clears every
    document for a file EXCEPT ones of exclude_type. ingest_code.py's raw-
    chunking tier uses this instead of a blanket delete_by_source once
    LLM-summary enrichment is enabled, so re-chunking a file's mechanical
    structure never wipes out its separately-managed llm_summary document
    (which may not be getting re-written this run at all, if enrichment's
    own incremental-skip decided the file's content hasn't changed).
    """
    vector_store = get_vector_store(collection_key)
    await vector_store.adelete(where={"$and": [{"source": relative_path}, {"type": {"$ne": exclude_type}}]})


async def list_distinct_sources(collection_key: str) -> set[str]:
    """Every distinct "source" metadata value currently stored in a
    collection -- lets a re-ingestion run detect files that existed in a
    prior run but no longer exist on disk (Chroma itself acts as the
    manifest here; no separate persisted state needed) so their stale
    chunks can be purged via delete_by_source too, not just chunks
    belonging to files the current run actually visits. Chroma's own
    .get() has no async variant, so this runs it off the event loop
    thread."""
    vector_store = get_vector_store(collection_key)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: vector_store.get(include=["metadatas"]))
    return {m["source"] for m in result.get("metadatas") or [] if m and m.get("source")}


async def source_metadata(collection_key: str) -> list[dict]:
    """Per-source summary rows for the corpus browser -- one entry per
    distinct "source" metadata value in the collection, reusing the same
    vector_store.get(include=["metadatas"]) call list_distinct_sources
    already makes rather than pulling the whole collection a second time.

    Each row: {"source", "chunk_count", "has_llm_summary", "format",
    "ingested_at"}. "chunk_count" excludes llm_summary rows (those are a
    derived enrichment artifact, not a mechanical chunk). "format"/
    "ingested_at" are None when every row for that source predates Phase
    L-A's metadata additions -- must not crash on a missing key.
    """
    vector_store = get_vector_store(collection_key)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: vector_store.get(include=["metadatas"]))

    by_source: dict[str, dict] = {}
    for metadata in result.get("metadatas") or []:
        source = (metadata or {}).get("source")
        if not source:
            continue
        row = by_source.setdefault(
            source,
            {"source": source, "chunk_count": 0, "has_llm_summary": False, "format": None, "ingested_at": None},
        )
        if metadata.get("type") == "llm_summary":
            row["has_llm_summary"] = True
        else:
            row["chunk_count"] += 1
        if metadata.get("format") is not None:
            row["format"] = metadata["format"]
        ingested_at = metadata.get("ingested_at")
        if ingested_at is not None and (row["ingested_at"] is None or ingested_at > row["ingested_at"]):
            row["ingested_at"] = ingested_at

    return sorted(by_source.values(), key=lambda row: row["source"])


def collection_counts() -> dict[str, int]:
    """Document count per collection key -- lets api/routers/ask.py's
    GET /status report an empty-corpus state (no ingestion has run yet)
    without needing a full retrieval call."""
    return {key: get_vector_store(key)._collection.count() for key in COLLECTIONS}


KEYWORD_TOP_K_PER_COLLECTION = 5

_IDENTIFIER_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")

_keyword_doc_cache: dict[str, tuple[int, list[tuple[str, dict]]]] = {}


async def _cached_documents(collection_key: str) -> list[tuple[str, dict]]:
    """Every (content, metadata) pair currently in a collection, cached and
    keyed by ingestion_generation.current() so a full collection pull only
    happens once per ingestion run rather than once per Ask question --
    mirrors api/ask_cache.py's own use of the same counter as an
    invalidation signal, just for a different cache."""
    generation = ingestion_generation.current()
    cached = _keyword_doc_cache.get(collection_key)
    if cached is not None and cached[0] == generation:
        return cached[1]

    vector_store = get_vector_store(collection_key)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: vector_store.get(include=["metadatas", "documents"]))
    documents = result.get("documents") or []
    metadatas = result.get("metadatas") or []
    docs = list(zip(documents, metadatas))
    _keyword_doc_cache[collection_key] = (generation, docs)
    return docs


async def keyword_search(collection_key: str, query: str, limit: int = KEYWORD_TOP_K_PER_COLLECTION) -> list[dict]:
    """Literal keyword/substring search over a collection's chunks, unioned
    with vector search in ingestion/retrieval.py -- vector similarity alone
    doesn't guarantee an exact identifier mentioned in the question (e.g. a
    variable/function name) surfaces in the top-k results, since embedding-
    space closeness to the question's phrasing is a different signal than
    "this exact string is in the chunk." Not a real relevance ranking --
    just enough scoring to prioritize an exact whole-query substring match
    over a partial-token match, since this only needs to guarantee recall of
    literal terms, not rank them well.
    """
    query_lower = query.lower()
    tokens = {match.group(0).lower() for match in _IDENTIFIER_TOKEN_RE.finditer(query)}

    scored: list[tuple[int, dict]] = []
    for content, metadata in await _cached_documents(collection_key):
        content_lower = (content or "").lower()
        score = 10 if query_lower and query_lower in content_lower else 0
        score += sum(1 for token in tokens if token in content_lower)
        if score > 0:
            scored.append((score, {"content": content, "metadata": metadata or {}}))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [doc for _, doc in scored[:limit]]


def reset_collection(collection_key: str) -> None:
    """Delete and recreate a collection, used before a fresh one-time ingestion run."""
    if collection_key not in COLLECTIONS:
        raise ValueError(
            f"Unknown collection key '{collection_key}'. Expected one of {list(COLLECTIONS)}."
        )

    client = get_chroma_client()
    name = COLLECTIONS[collection_key]
    try:
        client.delete_collection(name)
    except Exception:
        pass
    _vector_stores.pop(collection_key, None)
