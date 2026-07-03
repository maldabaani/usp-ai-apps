"""Shared ChromaDB client setup. Three persistent collections are exposed:

- ``sf_user_manuals`` — chunks of User Manual PDFs
- ``sf_codebase``     — chunks of the indexed codebase (all languages/layers)
- ``sf_jpa_entities`` — JPA Entity classes only (used as the DB schema proxy)
"""
from __future__ import annotations

import chromadb
from chromadb.config import Settings as ChromaSettings
from langchain_chroma import Chroma
from langchain_ollama import OllamaEmbeddings

from config import settings

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
