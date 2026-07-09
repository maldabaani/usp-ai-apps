"""Settings screen endpoints: read/write the LLM and task-management-system
configuration that lives in backend/.env, taking effect on the running
backend immediately (see Settings.apply_updates in config.py) instead of
requiring a restart.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

import ado_mcp.ado_client as ado_client
import notion_export.client as notion_client
from api.deps import require_admin, require_auth
from config import settings
from config_store import update_env_file

router = APIRouter(prefix="/settings", tags=["settings"])

# Maps the lowercase field names this API speaks to the uppercase env var /
# Settings attribute names config.py speaks. Secrets (NOTION_API_KEY) are
# handled separately since they're masked on read and only conditionally
# written.
_FIELD_TO_ENV = {
    "ollama_base_url": "OLLAMA_BASE_URL",
    "ollama_llm_model": "OLLAMA_LLM_MODEL",
    "ollama_embed_model": "OLLAMA_EMBED_MODEL",
    "ollama_num_ctx": "OLLAMA_NUM_CTX",
    "ollama_embed_num_ctx": "OLLAMA_EMBED_NUM_CTX",
    "prompt_variant": "PROMPT_VARIANT",
    "output_mode": "OUTPUT_MODE",
    "ado_organization": "ADO_ORGANIZATION",
    "ado_project": "ADO_PROJECT",
    "mcp_server_path": "MCP_SERVER_PATH",
    "notion_database_id": "NOTION_DATABASE_ID",
    "notion_parent_page_id": "NOTION_PARENT_PAGE_ID",
    "notion_title_property": "NOTION_TITLE_PROPERTY",
    "notion_status_property": "NOTION_STATUS_PROPERTY",
    "notion_status_value": "NOTION_STATUS_VALUE",
    "anthropic_model": "CLAUDE_MODEL",
    "ingest_ollama_enabled": "INGEST_OLLAMA_ENABLED",
    "ingest_ollama_model": "INGEST_OLLAMA_MODEL",
    "ask_qa_model": "ASK_QA_MODEL",
    "assessment_model": "ASSESSMENT_MODEL",
    "llm_request_timeout_seconds": "LLM_REQUEST_TIMEOUT_SECONDS",
}

_ADO_FIELDS = {"ado_organization", "ado_project", "mcp_server_path"}
_NOTION_FIELDS = {
    "notion_database_id",
    "notion_parent_page_id",
    "notion_title_property",
    "notion_status_property",
    "notion_status_value",
    "notion_api_key",
}

# Fields with no consumer wired up to rebuild on a live settings change yet
# (see config.py's settings_generation docstring) -- unlike
# ollama_base_url/ollama_llm_model/ollama_embed_model and anthropic_api_key/
# anthropic_model, which do hot-reload via the generation-counter pattern.
RESTART_REQUIRED_FIELDS = {"ingest_ollama_enabled", "ingest_ollama_model"}


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "•" * len(value)
    return f"…{value[-4:]}"


class SettingsResponse(BaseModel):
    ollama_base_url: str
    ollama_llm_model: str
    ollama_embed_model: str
    ollama_num_ctx: int
    ollama_embed_num_ctx: int
    prompt_variant: str
    output_mode: str
    ado_organization: str
    ado_project: str
    mcp_server_path: str
    notion_database_id: str
    notion_parent_page_id: str
    notion_title_property: str
    notion_status_property: str
    notion_status_value: str
    notion_api_key_masked: str
    anthropic_api_key_masked: str
    anthropic_model: str
    ingest_ollama_enabled: bool
    ingest_ollama_model: str
    ask_qa_model: str
    assessment_model: str
    llm_request_timeout_seconds: int
    restart_required_fields: set[str]


class SettingsUpdate(BaseModel):
    ollama_base_url: Optional[str] = None
    ollama_llm_model: Optional[str] = None
    ollama_embed_model: Optional[str] = None
    ollama_num_ctx: Optional[int] = None
    ollama_embed_num_ctx: Optional[int] = None
    prompt_variant: Optional[str] = None
    output_mode: Optional[str] = None
    ado_organization: Optional[str] = None
    ado_project: Optional[str] = None
    mcp_server_path: Optional[str] = None
    notion_database_id: Optional[str] = None
    notion_parent_page_id: Optional[str] = None
    notion_title_property: Optional[str] = None
    notion_status_property: Optional[str] = None
    notion_status_value: Optional[str] = None
    # Omitted, or equal to the mask returned by GET /settings, means "leave
    # the current key unchanged" -- only a genuinely different value is
    # treated as a new secret to write.
    notion_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    anthropic_model: Optional[str] = None
    ingest_ollama_enabled: Optional[bool] = None
    ingest_ollama_model: Optional[str] = None
    ask_qa_model: Optional[str] = None
    assessment_model: Optional[str] = None
    llm_request_timeout_seconds: Optional[int] = None


def _current_settings() -> SettingsResponse:
    return SettingsResponse(
        ollama_base_url=settings.OLLAMA_BASE_URL,
        ollama_llm_model=settings.OLLAMA_LLM_MODEL,
        ollama_embed_model=settings.OLLAMA_EMBED_MODEL,
        ollama_num_ctx=settings.OLLAMA_NUM_CTX,
        ollama_embed_num_ctx=settings.OLLAMA_EMBED_NUM_CTX,
        prompt_variant=settings.PROMPT_VARIANT,
        output_mode=settings.OUTPUT_MODE,
        ado_organization=settings.ADO_ORGANIZATION,
        ado_project=settings.ADO_PROJECT,
        mcp_server_path=settings.MCP_SERVER_PATH,
        notion_database_id=settings.NOTION_DATABASE_ID,
        notion_parent_page_id=settings.NOTION_PARENT_PAGE_ID,
        notion_title_property=settings.NOTION_TITLE_PROPERTY,
        notion_status_property=settings.NOTION_STATUS_PROPERTY,
        notion_status_value=settings.NOTION_STATUS_VALUE,
        notion_api_key_masked=_mask_secret(settings.NOTION_API_KEY),
        anthropic_api_key_masked=_mask_secret(settings.ANTHROPIC_API_KEY),
        anthropic_model=settings.CLAUDE_MODEL,
        ingest_ollama_enabled=settings.INGEST_OLLAMA_ENABLED,
        ingest_ollama_model=settings.INGEST_OLLAMA_MODEL,
        ask_qa_model=settings.ASK_QA_MODEL,
        assessment_model=settings.ASSESSMENT_MODEL,
        llm_request_timeout_seconds=settings.LLM_REQUEST_TIMEOUT_SECONDS,
        restart_required_fields=RESTART_REQUIRED_FIELDS,
    )


@router.get("", response_model=SettingsResponse)
async def get_settings_view(user: dict = Depends(require_auth)) -> SettingsResponse:
    return _current_settings()


@router.put("", response_model=SettingsResponse)
async def update_settings_view(
    body: SettingsUpdate, user: dict = Depends(require_admin)
) -> SettingsResponse:
    current_notion_mask = _mask_secret(settings.NOTION_API_KEY)
    current_anthropic_mask = _mask_secret(settings.ANTHROPIC_API_KEY)
    provided = body.model_dump(exclude_unset=True, exclude_none=True)

    changed_fields: set[str] = set()
    env_updates: dict[str, str] = {}

    notion_api_key = provided.pop("notion_api_key", None)
    if notion_api_key is not None and notion_api_key != current_notion_mask:
        env_updates["NOTION_API_KEY"] = notion_api_key
        changed_fields.add("notion_api_key")

    anthropic_api_key = provided.pop("anthropic_api_key", None)
    if anthropic_api_key is not None and anthropic_api_key != current_anthropic_mask:
        env_updates["ANTHROPIC_API_KEY"] = anthropic_api_key
        changed_fields.add("anthropic_api_key")

    for field, value in provided.items():
        env_updates[_FIELD_TO_ENV[field]] = value
        changed_fields.add(field)

    if env_updates:
        update_env_file(env_updates)
        settings.apply_updates(env_updates)

    if changed_fields & _NOTION_FIELDS:
        notion_client.reset_client()
    if changed_fields & _ADO_FIELDS:
        ado_client.reset_client()

    return _current_settings()
