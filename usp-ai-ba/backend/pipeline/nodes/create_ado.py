"""Node 5: create the Epic -> User Story -> (Dev Tasks + Unit Test Tasks) hierarchy on ADO."""
from __future__ import annotations

import logging

from ado_mcp.ado_client import get_ado_mcp_client
from pipeline.state import StoryForgeState

logger = logging.getLogger(__name__)


def _join_tags(*tags: str) -> str:
    """ADO requires tags to be semicolon-separated, not comma-separated."""
    return "; ".join(tag for tag in tags if tag)


def _story_title(system_name: str, ppm_number: str, ppm_name: str) -> str:
    return f"{system_name} || {ppm_number} || {ppm_name}"


def _story_description(story: dict) -> str:
    acceptance_criteria = "<br>".join(story.get("acceptance_criteria", []))
    return (
        f"<b>User Story:</b><br>{story.get('user_story', '')}<br><br>"
        f"<b>Acceptance Criteria:</b><br>{acceptance_criteria}"
    )


def _render_dict_section(value) -> str:
    if isinstance(value, dict):
        return "<br>".join(f"<b>{key}:</b> {val}" for key, val in value.items())
    if isinstance(value, list):
        return "<br>".join(str(item) for item in value)
    return str(value)


def _dev_task_description(task: dict) -> str:
    sections = [
        ("User Story", task.get("user_story", "")),
        ("Acceptance Criteria", "<br>".join(task.get("acceptance_criteria", []))),
        ("Technical Approach", "<br>".join(task.get("technical_approach", []))),
        ("Affected Components", _render_dict_section(task.get("affected_components", {}))),
        ("API Contract", _render_dict_section(task.get("api_contract", {}))),
        ("Business Rules", "<br>".join(task.get("business_rules", []))),
        ("Error Handling", "<br>".join(task.get("error_handling", []))),
    ]
    return "<br><br>".join(f"<b>{title}:</b><br>{body}" for title, body in sections)


def _unit_test_description(test: dict) -> str:
    test_scenarios = test.get("test_scenarios", {})
    scenarios_html = "<br>".join(
        f"<b>{category}:</b><br>" + "<br>".join(items)
        for category, items in test_scenarios.items()
    )
    sections = [
        ("Test Objective", test.get("test_objective", "")),
        ("Test Scenarios", scenarios_html),
        ("Test Data", _render_dict_section(test.get("test_data", {}))),
        ("Mock Setup", "<br>".join(test.get("mock_setup", []))),
        ("Assertions", "<br>".join(test.get("assertions", []))),
    ]
    return "<br><br>".join(f"<b>{title}:</b><br>{body}" for title, body in sections)


async def create_ado_node(state: StoryForgeState) -> StoryForgeState:
    """Create Epic/User Story/Dev Task/Unit Test Task work items via the ADO MCP server."""
    ado_results: list[dict] = []
    new_errors: list[str] = []

    try:
        client = get_ado_mcp_client()
    except Exception as exc:
        logger.exception("Failed to initialise ADO MCP client")
        return {
            **state,
            "ado_results": [],
            "errors": state["errors"] + [f"create_ado_node: client init failed: {exc}"],
            "status": "error",
        }

    system_name = state["system_name"]
    ppm_number = state["ppm_number"]
    ppm_name = state["ppm_name"]

    for story in state["approved_stories"]:
        try:
            epic = await client.create_epic(
                title=story.get("epic_title", _story_title(system_name, ppm_number, ppm_name)),
                description=_story_description(story),
                tags=_join_tags(system_name, "USP-Project"),
            )

            user_story = await client.create_user_story(
                parent_id=epic["id"],
                title=_story_title(system_name, ppm_number, ppm_name),
                description=_story_description(story),
                tags=_join_tags(system_name, "USP-Project"),
            )

            tasks_result: list[dict] = []

            for dev_task in story.get("dev_tasks", []):
                created = await client.create_task(
                    parent_id=user_story["id"],
                    title=dev_task.get("title", ""),
                    description=_dev_task_description(dev_task),
                    tags=_join_tags(system_name, "Development"),
                    activity="Development",
                )
                tasks_result.append(
                    {"id": created["id"], "url": created["url"], "type": "dev_task"}
                )

            for unit_test_task in story.get("unit_test_tasks", []):
                created = await client.create_task(
                    parent_id=user_story["id"],
                    title=unit_test_task.get("title", ""),
                    description=_unit_test_description(unit_test_task),
                    tags=_join_tags(system_name, "Testing"),
                    activity="Testing",
                )
                tasks_result.append(
                    {"id": created["id"], "url": created["url"], "type": "unit_test_task"}
                )

            ado_results.append(
                {
                    "epic_id": epic["id"],
                    "epic_url": epic["url"],
                    "story_id": user_story["id"],
                    "story_url": user_story["url"],
                    "tasks": tasks_result,
                }
            )
        except Exception as exc:  # noqa: BLE001 - one story failing must not abort the rest
            logger.exception("Failed to create ADO hierarchy for story %s", story.get("epic_title"))
            new_errors.append(f"create_ado_node: {story.get('epic_title', 'unknown')}: {exc}")

    return {
        **state,
        "ado_results": ado_results,
        "errors": state["errors"] + new_errors,
        "status": "done" if not new_errors else "error",
    }
