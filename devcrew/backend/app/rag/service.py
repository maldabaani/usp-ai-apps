"""Graph-facing RAG service: per-run indexes, workspace/rules sync, cleanup."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import cast

from app.config import Settings
from app.rag.chunking import ChunkConfig
from app.rag.embeddings import Embedder
from app.rag.index import ChromaClient, CodeIndex, IndexStats, SearchHit
from app.tools.catalog import RulesCatalog
from app.tools.git import GitRepo

logger = logging.getLogger(__name__)


class RagService:
    def __init__(
        self,
        client_factory: Callable[[], ChromaClient],
        embedder: Embedder,
        settings: Settings,
    ) -> None:
        self._client_factory = client_factory
        self._client: ChromaClient | None = None
        self._embedder = embedder
        self._settings = settings
        self._indexes: dict[str, CodeIndex] = {}
        self._cfg = ChunkConfig(
            max_lines=settings.rag_chunk_max_lines,
            window_lines=settings.rag_window_lines,
            overlap_lines=settings.rag_window_overlap,
        )

    def _chroma(self) -> ChromaClient:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def index(self, run_id: str) -> CodeIndex:
        if run_id not in self._indexes:
            self._indexes[run_id] = CodeIndex(
                self._chroma(), self._embedder, run_id, self._cfg, self._settings.rag_max_file_bytes
            )
        return self._indexes[run_id]

    async def sync_workspace(self, run_id: str, root: Path) -> IndexStats:
        """Index the tracked files at the workspace's checked-out commit (incremental)."""
        repo = GitRepo(root)
        tracked = [p for p in (await repo.run("ls-files", "-z")).split("\0") if p]
        sha = await repo.head()

        def _read() -> dict[str, bytes]:
            files: dict[str, bytes] = {}
            for rel in tracked:
                path = root / rel
                if path.is_file() and not path.is_symlink():
                    files[rel] = path.read_bytes()
            return files

        files = await asyncio.to_thread(_read)
        return await self.index(run_id).sync(files, commit_sha=sha, source="workspace")

    async def sync_rules(
        self, run_id: str, rules: RulesCatalog, stacks: Iterable[str]
    ) -> IndexStats:
        files = {}
        for stack in sorted(set(stacks)):
            path = rules.rules_dir / f"{stack}.md"
            if path.is_file():
                files[f"rules/{stack}.md"] = path.read_bytes()
        return await self.index(run_id).sync(files, commit_sha="rules", source="rules")

    async def search(
        self, run_id: str, query: str, k: int | None = None, path_filter: str | None = None
    ) -> list[SearchHit]:
        return await self.index(run_id).search(query, k or self._settings.rag_top_k, path_filter)

    async def delete(self, run_id: str) -> None:
        """Drop the run's collection (no cross-run memory)."""
        await self.index(run_id).delete()
        self._indexes.pop(run_id, None)


def chroma_http_client(settings: Settings) -> Callable[[], ChromaClient]:
    def factory() -> ChromaClient:
        import chromadb

        client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
        return cast(ChromaClient, client)

    return factory
