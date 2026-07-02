"""Thin wrapper around the official ``notion-client`` SDK for pushing StoryForge
Epics into a Notion database.

Named ``notion_export`` (not ``notion``) to avoid shadowing the ``notion_client``
pip package's own import name, the same way ``ado_mcp`` avoids colliding with
the ``mcp`` package.
"""
from __future__ import annotations

from notion_client import AsyncClient

from config import settings

# The Notion API rejects requests with more than 100 children blocks, and any
# single rich_text segment longer than ~2000 characters.
MAX_CHILDREN_PER_REQUEST = 100
MAX_RICH_TEXT_CHARS = 2000


def chunk_blocks(blocks: list[dict]) -> list[list[dict]]:
    return [
        blocks[i : i + MAX_CHILDREN_PER_REQUEST]
        for i in range(0, len(blocks), MAX_CHILDREN_PER_REQUEST)
    ] or [[]]


class NotionExportClient:
    """Creates one Notion page per Epic in the configured StoryForge database."""

    def __init__(self) -> None:
        if not settings.NOTION_API_KEY:
            raise RuntimeError("NOTION_API_KEY is not configured")
        if not settings.NOTION_DATABASE_ID:
            raise RuntimeError(
                "NOTION_DATABASE_ID is not configured "
                "(run scripts/setup_notion_database.py once to create it)"
            )
        self._client = AsyncClient(auth=settings.NOTION_API_KEY)
        self._data_source_id: str | None = None

    async def _get_data_source_id(self) -> str:
        """Resolve and cache the database's data source ID.

        Notion API 2025-09-03+ requires pages to be parented by a data source,
        not a database directly -- a database is a container for one or more
        data sources, each of which actually holds the property schema and
        parents pages. NOTION_DATABASE_ID stays the one ID users configure;
        this resolves it to the data source underneath, once per process.
        """
        if self._data_source_id is None:
            database = await self._client.databases.retrieve(
                database_id=settings.NOTION_DATABASE_ID
            )
            data_sources = database.get("data_sources") or []
            if not data_sources:
                raise RuntimeError(
                    f"Database {settings.NOTION_DATABASE_ID} has no data sources -- "
                    "was it created successfully via scripts/setup_notion_database.py?"
                )
            self._data_source_id = data_sources[0]["id"]
        return self._data_source_id

    async def create_epic_page(self, properties: dict, blocks: list[dict]) -> dict:
        """Create a database page with the given properties, then append the
        remaining content blocks in batches of <=100 (the first batch goes in
        the create call itself)."""
        batches = chunk_blocks(blocks)
        first_batch, remaining_batches = batches[0], batches[1:]

        data_source_id = await self._get_data_source_id()
        page = await self._client.pages.create(
            parent={"type": "data_source_id", "data_source_id": data_source_id},
            properties=properties,
            children=first_batch,
        )

        for batch in remaining_batches:
            if not batch:
                continue
            await self._client.blocks.children.append(block_id=page["id"], children=batch)

        return {"id": page["id"], "url": page.get("url", "")}


_client_instance: NotionExportClient | None = None


def get_notion_export_client() -> NotionExportClient:
    """Return a singleton client connected to the Notion API."""
    global _client_instance
    if _client_instance is None:
        _client_instance = NotionExportClient()
    return _client_instance
