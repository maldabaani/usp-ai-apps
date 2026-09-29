"""Graph-facing RAG service: per-run indexes, workspace/rules sync, cleanup."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import cast

from devcrew.config import Settings
from devcrew.rag.chunking import ChunkConfig
from devcrew.rag.embeddings import Embedder
from devcrew.rag.index import ChromaClient, CodeIndex, IndexStats, SearchHit
from devcrew.tools.catalog import RulesCatalog
from devcrew.tools.git import GitRepo

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
        # run -> the last indexing/search error (None: the last call worked); the run page shows
        # "code search unavailable" while it is set (Phase 16, BL-013)
        self._health: dict[str, str | None] = {}
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

    def health(self, run_id: str) -> tuple[bool, str | None] | None:
        """(ok, last error) of this process's index/search calls for the run; None: no call yet."""
        if run_id not in self._health:
            return None
        error = self._health[run_id]
        return error is None, error

    def _mark(self, run_id: str, error: BaseException | None) -> None:
        self._health[run_id] = None if error is None else (str(error) or type(error).__name__)

    async def sync_workspace(self, run_id: str, root: Path) -> IndexStats:
        """Index the tracked files at the workspace's checked-out commit (incremental)."""
        try:
            stats = await self._sync_workspace(run_id, root)
        except Exception as exc:
            self._mark(run_id, exc)
            raise
        self._mark(run_id, None)
        return stats

    async def _sync_workspace(self, run_id: str, root: Path) -> IndexStats:
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
        try:
            hits = await self.index(run_id).search(
                query, k or self._settings.rag_top_k, path_filter
            )
        except Exception as exc:
            self._mark(run_id, exc)
            raise
        self._mark(run_id, None)
        return hits

    async def delete(self, run_id: str) -> None:
        """Drop the run's collection (no cross-run memory)."""
        await self.index(run_id).delete()
        self._indexes.pop(run_id, None)
        self._health.pop(run_id, None)


def chroma_http_client(settings: Settings) -> Callable[[], ChromaClient]:
    """Standalone-DevCrew wiring: a separate chromadb-client -> remote Chroma
    HTTP server (settings.chroma_host/chroma_port), matching its own
    docker-compose.yml's dedicated chromadb service. Kept for anyone running
    devcrew/backend/app/main.py's standalone create_app() outside the merge
    (see the merge plan's Phase 10 note); the merged app uses
    chroma_embedded_client below instead."""

    def factory() -> ChromaClient:
        import chromadb

        client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
        return cast(ChromaClient, client)

    return factory


def chroma_embedded_client() -> Callable[[], ChromaClient]:
    """Merged-app wiring (see the merge plan's Phase 4): reuses StoryForge's
    own already-installed chromadb package and its single embedded, on-disk
    chromadb.PersistentClient (ingestion/chroma_client.py's get_chroma_client(),
    persisted at settings.CHROMA_PERSIST_PATH) instead of DevCrew's original
    remote chromadb-client -> separate Chroma HTTP server. Same underlying
    library (both resolve to the same 1.5.9, confirmed during the port), same
    process, same on-disk store -- DevCrew's per-run collections
    (run_<run_id>) simply live alongside StoryForge's own sf_codebase/
    sf_jpa_entities/sf_user_manuals collections in that one store, with no
    naming collision (different prefixes) and no separate service to run."""

    def factory() -> ChromaClient:
        from ingestion.chroma_client import get_chroma_client

        return cast(ChromaClient, get_chroma_client())

    return factory
