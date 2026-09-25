"""Application settings loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEVCREW_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    """All runtime configuration. Every field is documented in .env.example."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Database ---------------------------------------------------------------------------
    database_url: str = Field(
        default="postgresql+asyncpg://devcrew:devcrew@localhost:5432/devcrew",
        description="SQLAlchemy async URL for app tables.",
    )

    # --- Ollama -----------------------------------------------------------------------------
    ollama_base_url: str = "http://localhost:11434"
    models_config_path: Path = BACKEND_DIR / "config" / "models.yaml"
    llm_request_timeout_s: float = 600.0

    # --- ChromaDB ---------------------------------------------------------------------------
    chroma_host: str = "localhost"
    chroma_port: int = 8000

    # --- RAG -------------------------------------------------------------------------------
    rag_enabled: bool = True
    rag_top_k: int = Field(default=6, ge=1, le=50)
    rag_chunk_max_lines: int = Field(default=120, ge=10)
    rag_window_lines: int = Field(default=60, ge=5)
    rag_window_overlap: int = Field(default=10, ge=0)
    rag_max_file_bytes: int = Field(default=200_000, ge=1_000)
    rag_embed_batch_size: int = Field(default=32, ge=1)

    # --- Execution limits -------------------------------------------------------------------
    max_parallel_devs: int = Field(default=2, ge=1)
    max_dev_iterations: int = Field(default=3, ge=1)
    max_questions_per_task: int = Field(default=3, ge=0)
    max_conflict_rounds: int = Field(default=2, ge=0, description="Merge-conflict fixes per task.")
    max_coordinator_actions: int = Field(
        default=2, ge=0, description="Automatic retry/replan/split decisions per task/node."
    )

    # --- Agents -----------------------------------------------------------------------------
    prompts_dir: Path = BACKEND_DIR / "prompts"
    rules_dir: Path = DEVCREW_DIR / "rules"
    templates_dir: Path = DEVCREW_DIR / "templates"
    max_agent_steps: int = Field(default=30, ge=1, description="Tool-loop steps per agent turn.")

    # --- Workspaces / sandbox ---------------------------------------------------------------
    workspaces_dir: Path = Path("/tmp/devcrew/workspaces")
    sandbox_command_timeout_s: int = Field(default=300, ge=1)
    sandbox_install_timeout_s: int = Field(default=900, ge=1)
    sandbox_cpus: float = Field(default=2.0, gt=0)
    sandbox_memory: str = "4g"
    sandbox_pids_limit: int = Field(default=1024, ge=64)
    sandbox_image_prefix: str = "devcrew-sandbox"
    sandbox_enabled: bool = Field(
        default=True, description="Run tests/commands in Docker. Off = tests are not executed."
    )

    # --- GitHub -----------------------------------------------------------------------------
    github_token: SecretStr | None = None
    github_api_url: str = "https://api.github.com"

    # --- Server -----------------------------------------------------------------------------
    cors_origins: list[str] = ["http://localhost:4200"]
    log_level: str = "INFO"
    startup_health_strict: bool = Field(
        default=True,
        description="Abort startup when a critical dependency (DB, Ollama, models, Docker) fails.",
    )

    @field_validator("ollama_base_url", "github_api_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("github_token", mode="before")
    @classmethod
    def _empty_token_is_none(cls, v: object) -> object:
        return None if v in ("", None) else v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
