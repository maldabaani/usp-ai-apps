"""Application settings loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
# Merged into StoryForge's backend (usp-ai-ba/backend/) as a sibling package --
# BACKEND_DIR is now the *shared* backend root, which already has its own
# top-level "prompts/" directory (StoryForge's system prompts), so DevCrew's
# own non-Python resources (prompts/config/rules/templates/sandbox, formerly
# siblings of "app/"/"backend/") live under a namespaced "devcrew_resources/"
# directory instead, to avoid colliding with StoryForge's existing "prompts/".
DEVCREW_DIR = BACKEND_DIR / "devcrew_resources"


class Settings(BaseSettings):
    """All runtime configuration. Every field is documented in .env.example."""

    # Merged into StoryForge's backend, sharing its one .env file -- prefixed
    # so DevCrew's own env vars (e.g. DEVCREW_DATABASE_URL, DEVCREW_CORS_ORIGINS)
    # never collide with StoryForge's own same-named-but-differently-typed vars
    # (confirmed collision during the merge: both defined CORS_ORIGINS, but
    # StoryForge's is a plain comma-ish string while pydantic-settings expects
    # JSON for a list[str] field with no prefix disambiguation).
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_prefix="DEVCREW_"
    )

    # --- Database ---------------------------------------------------------------------------
    database_url: str = Field(
        default="postgresql+asyncpg://devcrew:devcrew@localhost:5432/devcrew",
        description="SQLAlchemy async URL for app tables.",
    )

    # --- Ollama -----------------------------------------------------------------------------
    ollama_base_url: str = "http://localhost:11434"
    models_config_path: Path = DEVCREW_DIR / "config" / "models.yaml"
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
    # The Planner and Architect always receive the whole request, so it must fit their context
    # window: ~20k characters with the default num_ctx 16384. Raise together with num_ctx.
    max_request_chars: int = Field(default=20_000, ge=100)
    # Longer documents (up to this) are condensed for the Planner/Architect (Phase 15).
    max_document_chars: int = Field(default=200_000, ge=1000)
    # Quality gates (Phase 11): secret scanning, dependency vulnerabilities, test coverage.
    gates_enabled: bool = True
    gate_coverage_min: float = Field(default=70.0, ge=0, le=100)
    gate_coverage_tolerance: float = Field(default=0.5, ge=0)
    max_gate_fix_rounds: int = Field(default=1, ge=0)
    # GitHub automation (Phase 12): issue intake and PR follow-up, driven by polling.
    watch_prs: bool = True
    max_pr_rounds: int = Field(default=3, ge=0)
    github_poll_tick_s: float = Field(default=30.0, gt=0)
    default_poll_interval_s: int = Field(default=300, ge=30)
    max_issue_runs: int = Field(default=2, ge=1)
    # Quiet PRs are checked less often: the wait doubles up to this (0 = every tick).
    pr_poll_max_interval_s: float = Field(default=300.0, ge=0)
    # Removing the devcrew label cancels the issue's run while its plan is not approved yet.
    cancel_on_unlabel: bool = True
    # Phase 14: default budgets per run (0 = no limit; a run can set its own), the project's
    # tests after every wave of tasks, and the size limit of the human's notes in prompts.
    run_token_budget: int = Field(default=0, ge=0)
    run_time_budget_min: int = Field(default=0, ge=0)
    wave_tests_enabled: bool = True
    max_wave_fix_tasks: int = Field(default=2, ge=0)
    max_notes_chars: int = Field(default=3000, ge=200)
    max_conflict_rounds: int = Field(default=2, ge=0, description="Merge-conflict fixes per task.")
    max_coordinator_actions: int = Field(
        default=2, ge=0, description="Automatic retry/replan/split decisions per task/node."
    )
    # A transient Ollama hiccup (model reload, momentary overload) can make the Coordinator's own
    # decision call return empty on every attempt of a single conversation; generate_structured's
    # own retries stay within that same conversation and won't survive it. These start a fresh
    # conversation instead, with a short delay, before giving up and escalating to a human.
    coordinator_decision_retries: int = Field(
        default=2, ge=1, description="Outer attempts for the Coordinator's own LLM decision."
    )
    coordinator_decision_retry_delay_s: float = Field(
        default=5.0, ge=0, description="Delay between coordinator decision attempts."
    )

    # --- Agents -----------------------------------------------------------------------------
    prompts_dir: Path = DEVCREW_DIR / "prompts"
    rules_dir: Path = DEVCREW_DIR / "rules"
    templates_dir: Path = DEVCREW_DIR / "templates"
    max_agent_steps: int = Field(default=30, ge=1, description="Tool-loop steps per agent turn.")
    log_prompts: bool = Field(
        default=False,
        description="Record every LLM call's full messages + reply as PROMPT events "
        "(debugging; off by default -- can be large and includes raw model input/output).",
    )

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
    # Live preview (Phase 16): the app runs in the sandbox on an internal network; a proxy
    # publishes one port on 127.0.0.1. PREVIEW_HOST is the host name the UI links to.
    preview_enabled: bool = True
    preview_host: str = "localhost"

    # --- GitHub -----------------------------------------------------------------------------
    github_token: SecretStr | None = None
    github_api_url: str = "https://api.github.com"
    github_git_url: str = "https://github.com"
    github_delivery_enabled: bool = Field(
        default=True, description="Push + open a PR after final approval (off for benchmarks)."
    )

    # --- Server -----------------------------------------------------------------------------
    cors_origins: list[str] = ["http://localhost:4400"]
    log_level: str = "INFO"
    # Default flipped True -> False when DevCrew was merged into StoryForge's
    # backend: in standalone DevCrew this correctly aborted its ONLY app when
    # a critical dependency (DB, Ollama, models, Docker) was down. Merged, an
    # abort here would also take down StoryForge's own unrelated, otherwise-
    # working features -- so the default is now to log and continue (the
    # merged lifespan still surfaces the failure loudly, not silently, just
    # without crashing the whole process). Set true explicitly for a fully-
    # provisioned deployment that wants the old strict behavior.
    startup_health_strict: bool = Field(
        default=False,
        description="Abort startup when a critical dependency (DB, Ollama, models, Docker) fails.",
    )

    @field_validator("ollama_base_url", "github_api_url", "github_git_url")
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
