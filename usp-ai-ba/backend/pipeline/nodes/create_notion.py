"""Node 5 (notion mode): create one Notion page per task (dev task or unit test
task) in the configured sprint board database.

Each page title is prefixed with the parent user story name so the board stays
scannable. PPM/system metadata is written into the page body — no extra
database columns required.

Error handling mirrors create_ado_node: one task failing doesn't abort the rest.
"""
from __future__ import annotations

import datetime
import logging

from config import settings
from notion_export.client import get_notion_export_client
from pipeline.nodes.naming import build_epic_title
from pipeline.state import StoryForgeState

logger = logging.getLogger(__name__)

MAX_RICH_TEXT_CHARS = 2000


def _rich_text(text: str) -> list[dict]:
    text = str(text)
    chunks = [text[i : i + MAX_RICH_TEXT_CHARS] for i in range(0, len(text), MAX_RICH_TEXT_CHARS)]
    chunks = chunks or [""]
    return [{"type": "text", "text": {"content": chunk}} for chunk in chunks]


def _heading_block(text: str, level: int) -> dict:
    key = f"heading_{level}"
    return {"object": "block", "type": key, key: {"rich_text": _rich_text(text)}}


def _paragraph_block(text: str) -> dict:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rich_text(text)}}


def _bullet_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": _rich_text(text)},
    }


def _list_blocks(items: list) -> list[dict]:
    return [_bullet_block(str(item)) for item in items]


def _dict_or_list_blocks(value) -> list[dict]:
    if isinstance(value, dict):
        return [_bullet_block(f"{key}: {val}") for key, val in value.items()]
    if isinstance(value, list):
        return _list_blocks(value)
    return [_paragraph_block(str(value))]


def _task_properties(task_title: str) -> dict:
    title = task_title[:2000]
    properties: dict = {
        settings.NOTION_TITLE_PROPERTY: {"title": [{"text": {"content": title}}]},
    }
    if settings.NOTION_STATUS_PROPERTY:
        # A *select*-type payload, not *status* -- Notion's public API can create
        # a new select option on write but cannot do the same for a status-type
        # property, so scripts/setup_notion_database.py provisions Status as select.
        properties[settings.NOTION_STATUS_PROPERTY] = {
            "select": {"name": settings.NOTION_STATUS_VALUE}
        }
    return properties


def _context_blocks(
    epic_title: str, user_story: str, ppm_number: str, ppm_name: str, system_name: str
) -> list[dict]:
    return [
        _heading_block("Context", 2),
        _bullet_block(f"User Story: {epic_title}"),
        _bullet_block(f"PPM Number: {ppm_number}"),
        _bullet_block(f"PPM Name: {ppm_name}"),
        _bullet_block(f"System Name: {system_name}"),
        _bullet_block(f"Created: {datetime.date.today().isoformat()}"),
        _heading_block("User Story Description", 2),
        _paragraph_block(user_story),
    ]


def _dev_task_blocks(task: dict) -> list[dict]:
    blocks: list[dict] = [
        _heading_block("Acceptance Criteria", 2),
    ]
    blocks.extend(_list_blocks(task.get("acceptance_criteria", [])))

    blocks.append(_heading_block("Technical Approach", 2))
    blocks.extend(_list_blocks(task.get("technical_approach", [])))

    blocks.append(_heading_block("Affected Components", 2))
    blocks.extend(_dict_or_list_blocks(task.get("affected_components", {})))

    blocks.append(_heading_block("API Contract", 2))
    blocks.extend(_dict_or_list_blocks(task.get("api_contract", {})))

    blocks.append(_heading_block("Business Rules", 2))
    blocks.extend(_list_blocks(task.get("business_rules", [])))

    blocks.append(_heading_block("Error Handling", 2))
    blocks.extend(_list_blocks(task.get("error_handling", [])))

    return blocks


def _unit_test_blocks(task: dict) -> list[dict]:
    blocks: list[dict] = [
        _heading_block("Test Objective", 2),
        _paragraph_block(task.get("test_objective", "")),
        _heading_block("Test Scenarios", 2),
    ]
    for category, items in task.get("test_scenarios", {}).items():
        blocks.append(_bullet_block(category))
        blocks.extend(_list_blocks(items))

    blocks.append(_heading_block("Test Data", 2))
    blocks.extend(_dict_or_list_blocks(task.get("test_data", {})))

    blocks.append(_heading_block("Mock Setup", 2))
    blocks.extend(_list_blocks(task.get("mock_setup", [])))

    blocks.append(_heading_block("Assertions", 2))
    blocks.extend(_list_blocks(task.get("assertions", [])))

    return blocks


async def create_notion_node(state: StoryForgeState) -> StoryForgeState:
    """Create one Notion page per dev task and unit test task -- or, when
    state["notion_update_mode"] is set (by pipeline/runner.py's
    update_tasks()), update the job's existing pages in place instead.

    Update mode matches old to new by position: the task at flattened index
    i (dev tasks then unit tests, per story, same order this function always
    iterates in) updates state["notion_results"][i] if that index exists,
    otherwise (more new tasks than old) it's created fresh. Any old entries
    left over beyond the new count (fewer new tasks than old) are archived.
    notion_update_mode is always cleared before returning -- it's a one-shot
    flag for the single recreate/update action that set it, not a persistent
    job setting.
    """
    update_mode = bool(state.get("notion_update_mode"))
    logger.info(
        "create_notion_node: job=%s stories=%d mode=%s",
        state.get("job_id"),
        len(state.get("approved_stories", [])),
        "update" if update_mode else "create",
    )
    notion_results: list[dict] = []
    new_errors: list[str] = []
    new_warnings: list[str] = []

    try:
        client = get_notion_export_client()
    except Exception as exc:
        logger.exception("Failed to initialise Notion client")
        return {
            **state,
            "notion_results": [],
            "errors": state["errors"] + [f"create_notion_node: client init failed: {exc}"],
            "status": "error",
            "notion_update_mode": False,
        }

    ppm_number = state["ppm_number"]
    ppm_name = state["ppm_name"]
    system_name = state["system_name"]
    epic_name = build_epic_title(ppm_number, ppm_name, system_name)
    old_results = state.get("notion_results") or [] if update_mode else []

    async def _create_or_update(task_title: str, properties: dict, blocks: list[dict]) -> dict:
        flat_index = len(notion_results)
        if update_mode and flat_index < len(old_results):
            old_page_id = old_results[flat_index]["page_id"]
            updated = await client.update_page(old_page_id, properties, blocks)
            if updated is not None:
                return updated
            logger.info("Old Notion page %s is gone -- creating fresh instead", old_page_id)
        return await client.create_epic_page(properties, blocks)

    for story in state["approved_stories"]:
        epic_title = story.get("epic_title", "Untitled Epic")
        user_story = story.get("user_story", "")
        context = _context_blocks(epic_title, user_story, ppm_number, ppm_name, system_name)

        for dev_task in story.get("dev_tasks", []):
            task_title = f"[{epic_name}] {dev_task.get('title', 'Dev Task')}"
            try:
                properties = _task_properties(task_title)
                blocks = context + _dev_task_blocks(dev_task)
                created = await _create_or_update(task_title, properties, blocks)
                notion_results.append(
                    {"task_title": task_title, "page_id": created["id"], "page_url": created["url"]}
                )
                logger.info("%s Notion task: %s", "Updated" if update_mode else "Created", task_title)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to create Notion page for dev task %s", task_title)
                new_errors.append(f"create_notion_node: {task_title}: {exc}")

        for unit_test in story.get("unit_test_tasks", []):
            task_title = f"[{epic_name}] Test: {unit_test.get('title', 'Unit Test')}"
            try:
                properties = _task_properties(task_title)
                blocks = context + _unit_test_blocks(unit_test)
                created = await _create_or_update(task_title, properties, blocks)
                notion_results.append(
                    {"task_title": task_title, "page_id": created["id"], "page_url": created["url"]}
                )
                logger.info("%s Notion task: %s", "Updated" if update_mode else "Created", task_title)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to create Notion page for unit test %s", task_title)
                new_errors.append(f"create_notion_node: {task_title}: {exc}")

    # Fewer new tasks than old: archive the leftover old pages that no longer
    # correspond to anything (position-matched, so these are the tail end).
    for leftover in old_results[len(notion_results):]:
        try:
            await client.archive_page(leftover["page_id"])
        except Exception as exc:  # noqa: BLE001 - one page failing to archive shouldn't block the rest
            logger.exception("Failed to archive leftover Notion page %s", leftover.get("page_id"))
            new_warnings.append(f"Could not archive old Notion page {leftover.get('page_id')}: {exc}")

    return {
        **state,
        "notion_results": notion_results,
        "errors": state["errors"] + new_errors,
        "warnings": (state.get("warnings") or []) + new_warnings,
        "status": "done" if not new_errors else "error",
        "notion_update_mode": False,
    }
