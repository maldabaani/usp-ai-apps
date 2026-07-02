"""One-off script: create the "StoryForge Epics" Notion database.

Run this once, manually, after creating a Notion internal integration and
sharing a parent page with it:

    cd backend && python -m scripts.setup_notion_database

NOTION_PARENT_PAGE_ID may be the raw page ID or a full Notion page URL
(pasted straight from the browser address bar / "Copy link") -- it's
normalized automatically. It must point to a plain page, not a database:
Notion doesn't allow creating a database inside another database.

It prints the resulting database ID -- paste that into NOTION_DATABASE_ID in
.env. Re-running this script creates a second, separate database; it does not
look for or reuse an existing one.
"""
from __future__ import annotations

import asyncio
import re

from notion_client import AsyncClient
from notion_client.errors import APIResponseError

from config import settings

DATABASE_TITLE = "StoryForge Epics"

DEFAULT_STATUS_OPTIONS = [
    {"name": "To Do", "color": "gray"},
    {"name": "In Progress", "color": "yellow"},
    {"name": "Done", "color": "green"},
]


def _properties() -> dict:
    """Build the database schema to match config.py's NOTION_TITLE_PROPERTY /
    NOTION_STATUS_PROPERTY / NOTION_STATUS_VALUE defaults, and create_notion.py's
    use of a *select*-type status property (Notion's public API can't add new
    values to a *status*-type property, so create_notion.py writes {"select":
    ...} -- this schema must stay a select type to match).
    """
    status_options = list(DEFAULT_STATUS_OPTIONS)
    if not any(opt["name"] == settings.NOTION_STATUS_VALUE for opt in status_options):
        status_options.append({"name": settings.NOTION_STATUS_VALUE, "color": "gray"})

    properties: dict = {
        settings.NOTION_TITLE_PROPERTY: {"title": {}},
        "PPM Number": {"rich_text": {}},
        "PPM Name": {"rich_text": {}},
        "System Name": {"rich_text": {}},
        "Created": {"date": {}},
    }
    if settings.NOTION_STATUS_PROPERTY:
        properties[settings.NOTION_STATUS_PROPERTY] = {"select": {"options": status_options}}
    return properties

_ID_RE = re.compile(
    r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}"
)


def _normalize_notion_id(raw: str) -> str:
    """Accept a raw Notion ID (dashed or not) or a full Notion URL and return a
    dashed UUID string suitable for the API.
    """
    match = _ID_RE.search(raw.strip())
    if not match:
        raise RuntimeError(
            f"Could not find a Notion page ID in NOTION_PARENT_PAGE_ID ({raw!r}). "
            "Set it to either the raw page ID or the full Notion page URL "
            "(copied from the browser address bar or the page's \"Copy link\" action)."
        )
    hex_id = match.group(0).replace("-", "")
    return f"{hex_id[0:8]}-{hex_id[8:12]}-{hex_id[12:16]}-{hex_id[16:20]}-{hex_id[20:32]}"


async def _resolve_and_validate_parent_page(client: AsyncClient, raw_parent_id: str) -> str:
    """Normalize NOTION_PARENT_PAGE_ID and confirm it resolves to a page your
    integration can see -- and that it's a page, not a database -- before
    attempting to create anything under it, so failures are clear instead of
    surfacing a raw Notion API traceback.
    """
    page_id = _normalize_notion_id(raw_parent_id)

    try:
        await client.pages.retrieve(page_id=page_id)
        return page_id
    except APIResponseError as page_exc:
        try:
            await client.databases.retrieve(database_id=page_id)
        except APIResponseError:
            raise RuntimeError(
                f"NOTION_PARENT_PAGE_ID ({raw_parent_id!r}) doesn't resolve to a page "
                "your integration can see. Make sure it's a real page ID and that the "
                'page is shared with your integration (page "•••" menu -> Connections '
                "-> add the integration)."
            ) from page_exc
        else:
            raise RuntimeError(
                f"NOTION_PARENT_PAGE_ID ({raw_parent_id!r}) points to a Notion "
                "*database*, not a page. Notion doesn't allow creating a database "
                "inside another database -- pick a plain page instead, share it with "
                "your integration, and use that page's ID/URL."
            ) from page_exc


async def main() -> None:
    if not settings.NOTION_API_KEY:
        raise RuntimeError("NOTION_API_KEY is not configured in .env")
    if not settings.NOTION_PARENT_PAGE_ID:
        raise RuntimeError("NOTION_PARENT_PAGE_ID is not configured in .env")

    client = AsyncClient(auth=settings.NOTION_API_KEY)
    parent_page_id = await _resolve_and_validate_parent_page(
        client, settings.NOTION_PARENT_PAGE_ID
    )

    # Notion API 2025-09-03+: databases.create() no longer takes a top-level
    # "properties" kwarg (the SDK silently drops it -- it's not in its picked
    # request fields) -- the schema goes under initial_data_source instead.
    database = await client.databases.create(
        parent={"type": "page_id", "page_id": parent_page_id},
        title=[{"type": "text", "text": {"content": DATABASE_TITLE}}],
        initial_data_source={"properties": _properties()},
    )

    print(f"Created database '{DATABASE_TITLE}'")
    print(f"NOTION_DATABASE_ID={database['id']}")


if __name__ == "__main__":
    asyncio.run(main())
