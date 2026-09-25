"""Application settings loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


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

    # --- Execution limits -------------------------------------------------------------------
    max_parallel_devs: int = Field(default=2, ge=1)
    max_dev_iterations: int = Field(default=3, ge=1)
    max_questions_per_task: int = Field(default=3, ge=0)

    # --- Workspaces / sandbox ---------------------------------------------------------------
    workspaces_dir: Path = Path("/tmp/devcrew/workspaces")
    sandbox_command_timeout_s: int = Field(default=300, ge=1)
    sandbox_cpus: float = Field(default=2.0, gt=0)
    sandbox_memory: str = "4g"

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
