"""One-off script: create the "StoryForge Epics" Notion database.

Run this once, manually, after creating a Notion internal integration and
sharing a parent page with it:

    cd backend && python -m scripts.setup_notion_database

It prints the resulting database ID — paste that into NOTION_DATABASE_ID in
.env. Re-running this script creates a second, separate database; it does not
look for or reuse an existing one.
"""
from __future__ import annotations

import asyncio

from notion_client import AsyncClient

from config import settings

DATABASE_TITLE = "StoryForge Epics"

PROPERTIES = {
    "Name": {"title": {}},
    "PPM Number": {"rich_text": {}},
    "PPM Name": {"rich_text": {}},
    "System Name": {"rich_text": {}},
    "Status": {
        "select": {
            "options": [
                {"name": "Generated", "color": "blue"},
                {"name": "In Progress", "color": "yellow"},
                {"name": "Done", "color": "green"},
            ]
        }
    },
    "Created": {"date": {}},
}


async def main() -> None:
    if not settings.NOTION_API_KEY:
        raise RuntimeError("NOTION_API_KEY is not configured in .env")
    if not settings.NOTION_PARENT_PAGE_ID:
        raise RuntimeError("NOTION_PARENT_PAGE_ID is not configured in .env")

    client = AsyncClient(auth=settings.NOTION_API_KEY)
    database = await client.databases.create(
        parent={"type": "page_id", "page_id": settings.NOTION_PARENT_PAGE_ID},
        title=[{"type": "text", "text": {"content": DATABASE_TITLE}}],
        properties=PROPERTIES,
    )

    print(f"Created database '{DATABASE_TITLE}'")
    print(f"NOTION_DATABASE_ID={database['id']}")


if __name__ == "__main__":
    asyncio.run(main())
