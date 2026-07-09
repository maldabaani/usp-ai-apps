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
    explicitly in .env."""
    secret_path = Path(jobs_dir) / ".jwt_secret"
    if secret_path.exists():
        return secret_path.read_text().strip()
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_hex(32)
    secret_path.write_text(secret)
    logger.warning(
        "JWT_SECRET not set -- generated and saved one to %s for this install only.",
        secret_path,
    )
    return secret


class Settings:
    # Blank by default -- Claude is opt-in only. A previous hardcoded
    # placeholder key here made "ANTHROPIC_API_KEY is non-blank" silently true
    # on every install regardless of intent, which is why Claude kept getting
    # selected (ingestion enrichment, Ask Technical/Business) even when the
    # user had configured Ollama everywhere else.
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_EMBED_MODEL: str = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
    OLLAMA_LLM_MODEL: str = os.getenv("OLLAMA_LLM_MODEL", "qwen2.5:14b")

    # generate_node/clarify_node never set num_ctx before this field existed,
    # so Ollama silently truncated any prompt exceeding whatever context size
    # the model happened to already be loaded with (observed in production:
    # a 16k+-token prompt -- full SDD text plus up to 30 RAG chunks -- getting
    # cut down to ~4k tokens with no error, just silently dropped content).
    # 32768 comfortably covers that plus generate_node's own MAX_OUTPUT_TOKENS
    # (8192) within the same window; lower this if your hardware can't hold
    # that much KV cache without a heavy slowdown.
    OLLAMA_NUM_CTX: int = int(os.getenv("OLLAMA_NUM_CTX", "32768"))

    # get_embeddings()'s OllamaEmbeddings never set num_ctx before this field
    # existed, so nomic-embed-text loaded with Ollama's own default context
    # (2048 tokens) -- observed in production via the llama.cpp server's own
    # log during a large-file code ingestion: several embedding requests
    # showed task.n_tokens == 2048 exactly (the model's context ceiling),
    # meaning those chunks were silently truncated before being embedded.
    # ingest_code.py's chunk-size heuristic (CHARS_PER_TOKEN=4) targets
    # ~1500 tokens per chunk, but that ratio is tuned loosely for prose --
    # dense code (heavy punctuation, short identifiers) commonly tokenizes
    # worse than 4 chars/token, so a "1500-token" code chunk can genuinely
    # exceed 2048 real tokens. Rather than fight that with an ever-more-
    # conservative char-to-token guess, explicitly raise the embedding
    # model's own context window instead -- 8192 is nomic-embed-text's real
    # supported max, comfortably covering every chunk size this app produces
    # regardless of language/token-density. Lower this only if your hardware
    # can't hold that much KV cache for the embedding model specifically.
    OLLAMA_EMBED_NUM_CTX: int = int(os.getenv("OLLAMA_EMBED_NUM_CTX", "8192"))

    # ingestion/enrichment/'s optional per-file LLM-summary tier. INGEST_OLLAMA_MODEL
    # is deliberately separate from OLLAMA_LLM_MODEL above (StoryForge's own
    # story-generation model) -- the two could reasonably diverge (a
    # smaller/faster model for high-volume file-by-file summarization vs. a
    # stronger one for one-shot story generation) even though their defaults
    # happen to coincide today. OLLAMA_BASE_URL is shared (every local model
    # hits the same physical Ollama server -- two independently-configurable
    # URLs for one server would be a bug, not a feature). On by default so a
    # fresh install has at least one enrichment agent configured without
    # touching .env -- Claude is opt-in (see ANTHROPIC_API_KEY above), so
    # Ollama is the only agent that can sensibly default to "on".
    INGEST_OLLAMA_ENABLED: bool = os.getenv("INGEST_OLLAMA_ENABLED", "true").lower() == "true"
    INGEST_OLLAMA_MODEL: str = os.getenv("INGEST_OLLAMA_MODEL", "qwen2.5:14b")

    # api/routers/ask.py's standing Ask Technical/Business endpoints: "ollama"
    # (default) uses OLLAMA_BASE_URL/OLLAMA_LLM_MODEL/OLLAMA_NUM_CTX, "claude"
    # uses ANTHROPIC_API_KEY/CLAUDE_MODEL above -- opt-in only, since Claude
    # needs a real key configured first.
    ASK_QA_MODEL: str = os.getenv("ASK_QA_MODEL", "ollama")

    # pipeline/nodes/clarify.py's clarify_node and generate.py's generate_node
    # -- StoryForge's assessment pipeline previously had no equivalent of
    # ASK_QA_MODEL at all (both nodes hardcoded ChatOllama, no ChatAnthropic
    # import anywhere); this brings assessment generation up to the same
    # configurability Ask already had. One setting covers both nodes, same
    # "ollama" (default) / "claude" values as ASK_QA_MODEL. When set to
    # "claude", a Claude failure (after its own retries) falls back to local
    # Ollama for that call rather than failing the whole node -- see
    # pipeline/nodes/llm_retry.py's invoke_and_parse_with_fallback.
    ASSESSMENT_MODEL: str = os.getenv("ASSESSMENT_MODEL", "ollama")

    # Shared HTTP request timeout for every LLM call in this app (Ask
    # Technical/Business's chat client, and ingestion/enrichment/'s Claude and
    # Ollama extraction agents) -- previously three separate hardcoded
    # REQUEST_TIMEOUT_SECONDS = 120 module constants (api/routers/ask.py,
    # ingestion/enrichment/agents/{claude,ollama}_agent.py), which silently
    # killed the connection before a slow local model (CPU-bound Ollama can
    # run well under 15 tokens/sec) finished responding, discarding a real,
    # already-in-progress answer. Centralized here so raising it for slow
    # hardware is a one-line change (or a Settings-page edit) that every
    # consumer picks up, instead of three files to hunt down and edit in sync.
    # Default raised 300 -> 900s after a real local run showed individual
    # Ollama calls taking 379-413s (6-7 min, confirmed in the Ollama server's
    # own log) under just 2 concurrent slots -- comfortably longer than the
    # old 300s ceiling, which would abort a call before Ollama's slower (but
    # otherwise successful) response ever arrived.
    LLM_REQUEST_TIMEOUT_SECONDS: int = int(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "900"))

    # Character budget for how much prior conversation history
    # api/conversation_store.py's messages get folded into a follow-up Ask
    # Technical/Business request (api/routers/ask.py's
    # _build_conversation_context) -- a character, not token, ceiling since
    # this codebase's one existing precedent for this kind of budget
    # (ingestion/ingest_code.py's CHARS_PER_TOKEN) is itself a rough
    # characters-per-token estimate. Trimmed from the oldest turns first,
    # silently, rather than erroring -- an unbounded history would otherwise
    # risk exceeding OLLAMA_NUM_CTX/the model's context window on a long-running
    # conversation.
    CONVERSATION_HISTORY_CHAR_BUDGET: int = int(os.getenv("CONVERSATION_HISTORY_CHAR_BUDGET", "8000"))

    CHROMA_PERSIST_PATH: str = os.getenv("CHROMA_PERSIST_PATH", "./chroma_db")

    # ingest_code.py's optional LLM-summary enrichment tier (ingestion/enrichment/
    # enrich.py) -- makes ingestion a per-file LLM-cost operation for the first
    # time, so it's a real knob, not just a hardcoded default. On by default;
    # enrich.py itself degrades gracefully (skips tier 2, logs a warning) rather
    # than failing the whole ingestion run when no agent is configured (no
    # ANTHROPIC_API_KEY and INGEST_OLLAMA_ENABLED off).
    INGEST_LLM_SUMMARY_ENABLED: bool = os.getenv("INGEST_LLM_SUMMARY_ENABLED", "true").lower() == "true"

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
    # per-install random secret persisted under JOBS_DIR/.jwt_secret if unset.
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

    # Bumped once per apply_updates() call (regardless of which fields
    # changed). Modules that build an LLM/embeddings client once at import
    # time (pipeline/nodes/generate.py, clarify.py, ingestion/chroma_client.py)
    # check this before reusing their cached client, rebuilding only when it's
    # advanced -- the same generation-counter pattern CodeMind's Java
    # RuntimeSettings used for its own hot-reload (see that class's
    # docstring), applied here to close a StoryForge-side gap that predates
    # this port: settings.apply_updates() already mutated these values, but
    # nothing previously rebuilt the already-constructed clients, so a
    # settings-screen change silently had no effect until a process restart.
    settings_generation: int = 0

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
        self.settings_generation += 1


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
