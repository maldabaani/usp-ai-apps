"""Central configuration for StoryForge AI, loaded from environment variables / .env."""
from __future__ import annotations

import logging
import os
import secrets
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load backend/.env specifically, not whatever load_dotenv()'s default
# upward search from the current working directory happens to find first.
# Without an explicit path, running uvicorn/python from any directory other
# than backend/ (an IDE run config, a different terminal tab, etc.) silently
# loads the wrong .env -- or none at all -- and every setting below quietly
# falls back to its hardcoded default instead of erroring.
load_dotenv(Path(__file__).resolve().parent / ".env")


def _split_origins(raw: str) -> list[str]:
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _default_jwt_secret(jobs_dir: str) -> str:
    """A random secret persisted to disk under JOBS_DIR, so restarts don't
    invalidate every login session -- used only when JWT_SECRET isn't set
    explicitly in .env. Note: for CodeMind's login (SSO) to work, both apps
    must share the SAME secret -- this auto-generated one only exists for
    StoryForge's own convenience; set JWT_SECRET explicitly in both apps'
    environments to the same value to make cross-app auth work."""
    secret_path = Path(jobs_dir) / ".jwt_secret"
    if secret_path.exists():
        return secret_path.read_text().strip()
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_hex(32)
    secret_path.write_text(secret)
    logger.warning(
        "JWT_SECRET not set -- generated and saved one to %s for this install only. "
        "Set JWT_SECRET explicitly (same value in both apps' environments) for "
        "CodeMind's login to accept StoryForge-issued tokens.",
        secret_path,
    )
    return secret


class Settings:
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "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")
    CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_EMBED_MODEL: str = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
    OLLAMA_LLM_MODEL: str = os.getenv("OLLAMA_LLM_MODEL", "qwen2.5:14b")

    CHROMA_PERSIST_PATH: str = os.getenv("CHROMA_PERSIST_PATH", "./chroma_db")

    MCP_SERVER_PATH: str = os.getenv("MCP_SERVER_PATH", "")
    ADO_ORGANIZATION: str = os.getenv("ADO_ORGANIZATION", "")
    ADO_PROJECT: str = os.getenv("ADO_PROJECT", "")

    # Notion integration token (internal integration secret) and the database it
    # writes Epic pages into. NOTION_PARENT_PAGE_ID is only needed once, to run
    # scripts/setup_notion_database.py, which creates the database and prints the
    # NOTION_DATABASE_ID to put here.
    NOTION_API_KEY: str = os.getenv("NOTION_API_KEY", "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")
    NOTION_DATABASE_ID: str = os.getenv("NOTION_DATABASE_ID", "ai-ba")
    NOTION_PARENT_PAGE_ID: str = os.getenv("NOTION_PARENT_PAGE_ID", "https://app.notion.com/p/c45ed9a6cb4f46a9b59823b0a73198ee?v=f187017a5e9b4e0ea1220a6107402931&source=copy_link")

    # create_notion_node writes one page per Epic into NOTION_DATABASE_ID, mapping
    # onto whatever schema that database already uses. Defaults match a standard
    # sprint board: a title property named "Task" and a *select*-type property
    # named "Status" whose options include "To Do" -- scripts/setup_notion_database.py
    # provisions Status as select rather than Notion's status type, since the
    # public API can create a new select option on write but not a new status
    # option. PPM metadata is written into the page body (not as DB properties),
    # so no extra columns are required. Set NOTION_STATUS_PROPERTY="" to skip
    # writing the status entirely.
    NOTION_TITLE_PROPERTY: str = os.getenv("NOTION_TITLE_PROPERTY", "Task")
    NOTION_STATUS_PROPERTY: str = os.getenv("NOTION_STATUS_PROPERTY", "Status")
    NOTION_STATUS_VALUE: str = os.getenv("NOTION_STATUS_VALUE", "To Do")

    CORS_ORIGINS: list[str] = _split_origins(
        os.getenv("CORS_ORIGINS", "http://localhost:4200")
    )

    JOBS_DIR: str = os.getenv("JOBS_DIR", "./jobs")
    UPLOADS_DIR: str = os.getenv("UPLOADS_DIR", "./uploads")
    EXPORTS_DIR: str = os.getenv("EXPORTS_DIR", "./exports")

    # Signs/verifies login JWTs (api/routers/auth.py). Falls back to a
    # per-install random secret persisted under JOBS_DIR/.jwt_secret if unset
    # -- see _default_jwt_secret's docstring for why that alone isn't enough
    # for CodeMind's SSO to work.
    JWT_SECRET: str = os.getenv("JWT_SECRET") or _default_jwt_secret(os.getenv("JOBS_DIR", "./jobs"))
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))

    # "production" (default) uses prompts/system_prompt.py. "selftest" swaps in
    # prompts/system_prompt_selftest.py, used only when assessing SDDs that
    # describe changes to StoryForge AI's own codebase.
    PROMPT_VARIANT: str = os.getenv("PROMPT_VARIANT", "production")

    # "document" (default, for now) writes approved stories to a .docx. Set to
    # "ado" to push to Azure DevOps, or "notion" to push to a Notion database
    # instead — create_ado_node/export_document_node/create_notion_node are each
    # unchanged either way; only the routing in pipeline/graph.py picks one.
    OUTPUT_MODE: str = os.getenv("OUTPUT_MODE", "document")

    def apply_updates(self, updates: dict) -> None:
        """Mutate this singleton's attributes in place so every module holding
        a ``from config import settings`` reference sees the change
        immediately -- no restart, no re-import needed. Only ever called with
        values freshly written to .env (see config_store.update_env_file);
        never touches the os.getenv(..., "...") fallback expressions above,
        which stay fixed regardless of what's edited at runtime.
        """
        for key, value in updates.items():
            setattr(self, key, value)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
