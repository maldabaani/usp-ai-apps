"""MultiServerMCPClient wrapper for the local Node.js Azure DevOps MCP Server.

The MCP server is expected to expose three stdio tools that mirror the ADO
work item hierarchy this platform creates (Epic -> User Story -> Task):

- ``create_epic(title, description, tags)`` -> ``{"id": ..., "url": ...}``
- ``create_user_story(parent_id, title, description, tags)`` -> ``{"id": ..., "url": ...}``
- ``create_task(parent_id, title, description, tags, activity)`` -> ``{"id": ..., "url": ...}``
"""
from __future__ import annotations

import json
import logging

from langchain_mcp_adapters.client import MultiServerMCPClient

from config import settings

logger = logging.getLogger(__name__)

TOOL_CREATE_EPIC = "create_epic"
TOOL_CREATE_USER_STORY = "create_user_story"
TOOL_CREATE_TASK = "create_task"


def _server_config() -> dict:
    if not settings.MCP_SERVER_PATH:
        raise RuntimeError("MCP_SERVER_PATH is not configured")
    return {
        "ado": {
            "command": "node",
            "args": [settings.MCP_SERVER_PATH],
            "transport": "stdio",
            "env": {
                "ADO_ORGANIZATION": settings.ADO_ORGANIZATION,
                "ADO_PROJECT": settings.ADO_PROJECT,
            },
        }
    }


def _parse_tool_result(result) -> dict:
    """MCP tools return text content; expect a JSON object with id/url fields."""
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        return json.loads(result)
    if isinstance(result, list) and result:
        first = result[0]
        text = first.get("text") if isinstance(first, dict) else str(first)
        return json.loads(text)
    raise ValueError(f"Unexpected MCP tool result shape: {result!r}")


class AdoMcpClient:
    """Thin wrapper exposing the three ADO work item creation tools."""

    def __init__(self) -> None:
        self._client = MultiServerMCPClient(_server_config())
        self._tools_by_name: dict | None = None

    async def _get_tool(self, name: str):
        if self._tools_by_name is None:
            tools = await self._client.get_tools()
            self._tools_by_name = {tool.name: tool for tool in tools}
        tool = self._tools_by_name.get(name)
        if tool is None:
            raise RuntimeError(
                f"MCP server does not expose required tool '{name}'. "
                f"Available tools: {list(self._tools_by_name)}"
            )
        return tool

    async def create_epic(self, title: str, description: str, tags: str) -> dict:
        tool = await self._get_tool(TOOL_CREATE_EPIC)
        result = await tool.ainvoke(
            {"title": title, "description": description, "tags": tags}
        )
        return _parse_tool_result(result)

    async def create_user_story(
        self, parent_id: str, title: str, description: str, tags: str
    ) -> dict:
        tool = await self._get_tool(TOOL_CREATE_USER_STORY)
        result = await tool.ainvoke(
            {
                "parent_id": parent_id,
                "title": title,
                "description": description,
                "tags": tags,
            }
        )
        return _parse_tool_result(result)

    async def create_task(
        self,
        parent_id: str,
        title: str,
        description: str,
        tags: str,
        activity: str,
    ) -> dict:
        tool = await self._get_tool(TOOL_CREATE_TASK)
        result = await tool.ainvoke(
            {
                "parent_id": parent_id,
                "title": title,
                "description": description,
                "tags": tags,
                "activity": activity,
            }
        )
        return _parse_tool_result(result)


_client_instance: AdoMcpClient | None = None


def get_ado_mcp_client() -> AdoMcpClient:
    """Return a singleton MCP client connected to the local Node.js ADO server."""
    global _client_instance
    if _client_instance is None:
        _client_instance = AdoMcpClient()
    return _client_instance


def reset_client() -> None:
    """Drop the cached client so the next get_ado_mcp_client() call rebuilds
    it (and respawns the MCP server subprocess) from current settings --
    call this after the Settings screen changes any ADO field, since
    _server_config() is only read once at construction time."""
    global _client_instance
    _client_instance = None
