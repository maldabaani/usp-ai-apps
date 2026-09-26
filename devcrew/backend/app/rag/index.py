"""One ChromaDB collection per run (`run_<run_id>`): indexing, incremental updates, search.

The index mirrors the run's integration branch (tracked files only, source="workspace") plus the
stack rules files (source="rules"). Every chunk carries its file's content hash, so re-indexing
after a merge only re-embeds files whose content changed and drops chunks of deleted files.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.rag.chunking import Chunk, ChunkConfig, chunk_file
from app.rag.embeddings import Embedder

logger = logging.getLogger(__name__)

SKIP_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock"}
SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".svg",
    ".woff",
    ".woff2",
    ".jar",
    ".class",
    ".zip",
    ".gz",
    ".pdf",
    ".lock",
}
EMBED_CHAR_LIMIT = 6000  # stay well within nomic-embed-text's context


class ChromaCollection(Protocol):
    def upsert(self, **kwargs: Any) -> Any: ...
    def delete(self, **kwargs: Any) -> Any: ...
    def get(self, **kwargs: Any) -> Any: ...
    def query(self, **kwargs: Any) -> Any: ...
    def count(self) -> int: ...


class ChromaClient(Protocol):
    def get_or_create_collection(self, name: str, **kwargs: Any) -> Any: ...
    def delete_collection(self, name: str) -> Any: ...
    def list_collections(self) -> Any: ...


@dataclass(frozen=True)
class SearchHit:
    path: str
    start_line: int
    end_line: int
    text: str
    score: float  # cosine similarity (higher is better)
    symbol: str = ""
    source: str = "workspace"  # workspace | rules

    def render(self) -> str:
        sym = f"  ({self.symbol})" if self.symbol else ""
        return f"--- {self.path}:{self.start_line}-{self.end_line}{sym}\n{self.text}"


@dataclass
class IndexStats:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: int = 0
    chunks: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "added": self.added,
            "updated": self.updated,
            "removed": self.removed,
            "unchanged": self.unchanged,
            "chunks": self.chunks,
        }


def collection_name(run_id: str) -> str:
    return f"run_{run_id}"


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def indexable(path: str, data: bytes, max_bytes: int) -> bool:
    name = Path(path).name
    if name in SKIP_NAMES or Path(path).suffix.lower() in SKIP_SUFFIXES:
        return False
    if len(data) > max_bytes or b"\x00" in data[:8192]:
        return False
    return bool(data.strip())  # empty files have nothing to retrieve (and no chunks)


def matches_filter(path: str, path_filter: str | None) -> bool:
    if not path_filter:
        return True
    pattern = path_filter.strip().lstrip("./")
    if any(ch in pattern for ch in "*?["):
        return fnmatch.fnmatch(path, pattern)
    return path == pattern or path.startswith(pattern.rstrip("/") + "/")


class CodeIndex:
    def __init__(
        self,
        client: ChromaClient,
        embedder: Embedder,
        run_id: str,
        chunk_config: ChunkConfig | None = None,
        max_file_bytes: int = 200_000,
    ) -> None:
        self._client = client
        self._embedder = embedder
        self.run_id = run_id
        self.name = collection_name(run_id)
        self._cfg = chunk_config or ChunkConfig()
        self._max_file_bytes = max_file_bytes
        self._collection: ChromaCollection | None = None
        self._lock = asyncio.Lock()  # one writer at a time per run

    async def _col(self) -> ChromaCollection:
        if self._collection is None:
            created: ChromaCollection = await asyncio.to_thread(
                self._client.get_or_create_collection,
                self.name,
                metadata={"hnsw:space": "cosine", "run_id": self.run_id},
                embedding_function=None,
            )
            self._collection = created
            return created
        return self._collection

    async def _manifest(self, source: str) -> dict[str, str]:
        """path -> content hash of what is currently indexed for `source`."""
        col = await self._col()
        data = await asyncio.to_thread(col.get, where={"source": source}, include=["metadatas"])
        return {m["path"]: m["file_hash"] for m in data.get("metadatas") or [] if m}

    async def _embed_and_upsert(
        self, chunks: Sequence[Chunk], hashes: Mapping[str, str], sha: str, source: str
    ) -> None:
        if not chunks:
            return
        col = await self._col()
        texts = [f"{c.header}\n{c.text}"[:EMBED_CHAR_LIMIT] for c in chunks]
        vectors = await self._embedder.embed_documents(texts)
        await asyncio.to_thread(
            col.upsert,
            ids=[
                f"{source}:{c.path}:{c.start_line}-{c.end_line}:{hashes[c.path][:12]}:{i}"
                for i, c in enumerate(chunks)
            ],
            embeddings=vectors,
            documents=[c.text for c in chunks],
            metadatas=[
                {
                    "path": c.path,
                    "language": c.language,
                    "start_line": c.start_line,
                    "end_line": c.end_line,
                    "symbol": c.symbol,
                    "commit_sha": sha,
                    "file_hash": hashes[c.path],
                    "source": source,
                }
                for c in chunks
            ],
        )

    async def sync(
        self, files: Mapping[str, bytes], *, commit_sha: str, source: str = "workspace"
    ) -> IndexStats:
        """Make the `source` part of the index match `files` (path -> content)."""
        async with self._lock:
            col = await self._col()
            indexed = await self._manifest(source)
            wanted = {p: d for p, d in files.items() if indexable(p, d, self._max_file_bytes)}
            hashes = {p: file_hash(d) for p, d in wanted.items()}
            stats = IndexStats()
            changed: list[str] = []
            for path, digest in sorted(hashes.items()):
                if indexed.get(path) == digest:
                    stats.unchanged += 1
                    continue
                (stats.updated if path in indexed else stats.added).append(path)
                changed.append(path)
            stats.removed = sorted(set(indexed) - set(hashes))

            stale = [p for p in changed if p in indexed] + stats.removed
            if stale:
                where = {"$and": [{"source": source}, {"path": {"$in": stale}}]}
                await asyncio.to_thread(col.delete, where=where)
            chunks: list[Chunk] = []
            for path in changed:
                text = wanted[path].decode("utf-8", errors="replace")
                chunks.extend(chunk_file(path, text, self._cfg))
            await self._embed_and_upsert(chunks, hashes, commit_sha, source)
            stats.chunks = len(chunks)
            return stats

    async def search(
        self, query: str, k: int = 6, path_filter: str | None = None
    ) -> list[SearchHit]:
        col = await self._col()
        total = await asyncio.to_thread(col.count)
        if total == 0:
            return []
        vector = await self._embedder.embed_query(query)
        fetch = min(total, k if not path_filter else max(k * 8, 50))
        result = await asyncio.to_thread(
            col.query,
            query_embeddings=[vector],
            n_results=fetch,
            include=["documents", "metadatas", "distances"],
        )
        hits: list[SearchHit] = []
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        dists = (result.get("distances") or [[]])[0]
        for doc, meta, dist in zip(docs, metas, dists, strict=False):
            if not meta or not matches_filter(meta["path"], path_filter):
                continue
            hits.append(
                SearchHit(
                    path=meta["path"],
                    start_line=int(meta["start_line"]),
                    end_line=int(meta["end_line"]),
                    text=doc or "",
                    score=round(1.0 - float(dist), 4),
                    symbol=str(meta.get("symbol") or ""),
                    source=str(meta.get("source") or "workspace"),
                )
            )
            if len(hits) >= k:
                break
        return hits

    async def delete(self) -> None:
        self._collection = None
        names = {
            getattr(c, "name", c) for c in await asyncio.to_thread(self._client.list_collections)
        }
        if self.name in names:
            await asyncio.to_thread(self._client.delete_collection, self.name)
