"""Embedding wrapper: batching, bounded concurrency, and nomic task prefixes."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol


class EmbeddingModel(Protocol):
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def aembed_query(self, text: str) -> list[float]: ...


class Embedder:
    """nomic-embed-text expects `search_document:` / `search_query:` task prefixes."""

    def __init__(
        self, model: EmbeddingModel, *, model_name: str, batch_size: int = 32, max_parallel: int = 1
    ) -> None:
        self._model = model
        self._batch_size = batch_size
        self._semaphore = asyncio.Semaphore(max_parallel)
        nomic = "nomic" in model_name
        self._doc_prefix = "search_document: " if nomic else ""
        self._query_prefix = "search_query: " if nomic else ""

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = [self._doc_prefix + t for t in texts[i : i + self._batch_size]]
            async with self._semaphore:
                vectors.extend(await self._model.aembed_documents(batch))
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        async with self._semaphore:
            return await self._model.aembed_query(self._query_prefix + text)
