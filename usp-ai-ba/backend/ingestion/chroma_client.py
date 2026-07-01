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
_vector_stores: dict[str, Chroma] = {}


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
    """Return a singleton Ollama embeddings client using the local nomic-embed-text model."""
    global _embeddings
    if _embeddings is None:
        _embeddings = OllamaEmbeddings(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.OLLAMA_EMBED_MODEL,
        )
    return _embeddings


def get_vector_store(collection_key: str) -> Chroma:
    """Return a LangChain Chroma vector store wrapper for one of the three collections.

    ``collection_key`` must be one of: "manuals", "codebase", "entities".
    """
    if collection_key not in COLLECTIONS:
        raise ValueError(
            f"Unknown collection key '{collection_key}'. Expected one of {list(COLLECTIONS)}."
        )

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
