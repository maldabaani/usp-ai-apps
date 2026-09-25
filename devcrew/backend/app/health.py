"""Dependency health checks: DB, ChromaDB, Ollama (+ pulled models), Docker, GitHub token.

Used by GET /health and by the startup fail-fast check. Every failure carries an
actionable `detail` telling the user how to fix it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings

logger = logging.getLogger(__name__)

CHECK_TIMEOUT_S = 5.0


class CheckResult(BaseModel):
    name: str
    ok: bool
    critical: bool
    detail: str


class HealthReport(BaseModel):
    ok: bool
    checks: list[CheckResult]

    @property
    def critical_failures(self) -> list[CheckResult]:
        return [c for c in self.checks if c.critical and not c.ok]


def normalize_model_name(name: str) -> str:
    """Ollama reports untagged models as `<name>:latest`."""
    return name if ":" in name else f"{name}:latest"


def missing_models(required: set[str], available: set[str]) -> list[str]:
    have = {normalize_model_name(n) for n in available}
    return sorted(m for m in required if normalize_model_name(m) not in have)


async def check_database(engine: AsyncEngine) -> str:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return "connected"


async def check_chroma(settings: Settings) -> str:
    import chromadb  # local import: the client pulls in heavy deps

    def _heartbeat() -> int:
        client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
        return int(client.heartbeat())

    await asyncio.to_thread(_heartbeat)
    return f"reachable at {settings.chroma_host}:{settings.chroma_port}"


async def fetch_ollama_models(http: httpx.AsyncClient, base_url: str) -> set[str]:
    resp = await http.get(f"{base_url}/api/tags")
    resp.raise_for_status()
    return {m["name"] for m in resp.json().get("models", [])}


async def check_docker() -> str:
    import docker

    def _ping() -> str:
        client = docker.from_env(timeout=int(CHECK_TIMEOUT_S))
        try:
            client.ping()
            return str(client.version().get("Version", "unknown"))
        finally:
            client.close()

    version = await asyncio.to_thread(_ping)
    return f"daemon reachable (version {version})"


async def check_sandbox_images(settings: Settings) -> str:
    from app.sandbox.docker_runner import REQUIRED_IMAGE_FAMILIES, DockerSandboxRunner

    missing = await asyncio.to_thread(DockerSandboxRunner(settings).missing_images)
    required = {f"{settings.sandbox_image_prefix}-{f}:latest" for f in REQUIRED_IMAGE_FAMILIES}
    if set(missing) & required:
        raise RuntimeError(f"missing sandbox images: {', '.join(sorted(missing))}")
    note = f" ({missing[0]} not built: only needed for mixed-stack projects)" if missing else ""
    return "python, java and node images present" + note


async def check_github(http: httpx.AsyncClient, settings: Settings) -> str:
    if settings.github_token is None:
        raise RuntimeError(
            "GITHUB_TOKEN is not set. Create a token with 'repo' scope and add it to .env."
        )
    resp = await http.get(
        f"{settings.github_api_url}/user",
        headers={
            "Authorization": f"Bearer {settings.github_token.get_secret_value()}",
            "Accept": "application/vnd.github+json",
        },
    )
    if resp.status_code == 401:
        raise RuntimeError("GITHUB_TOKEN was rejected (401). Regenerate it and update .env.")
    resp.raise_for_status()
    return f"authenticated as {resp.json().get('login', '?')}"


async def _run(
    name: str, critical: bool, fn: Callable[[], Awaitable[str]], hint: str
) -> CheckResult:
    try:
        detail = await asyncio.wait_for(fn(), timeout=CHECK_TIMEOUT_S)
        return CheckResult(name=name, ok=True, critical=critical, detail=detail)
    except TimeoutError:
        return CheckResult(
            name=name,
            ok=False,
            critical=critical,
            detail=f"timed out after {CHECK_TIMEOUT_S}s. {hint}",
        )
    except Exception as exc:
        message = str(exc).strip() or type(exc).__name__
        sep = " " if message.endswith((".", "?", "!")) else ". "
        return CheckResult(name=name, ok=False, critical=critical, detail=f"{message}{sep}{hint}")


async def run_health_checks(
    settings: Settings,
    engine: AsyncEngine,
    required_models: set[str],
    *,
    http: httpx.AsyncClient | None = None,
) -> HealthReport:
    own_http = http is None
    client = http or httpx.AsyncClient(timeout=CHECK_TIMEOUT_S)
    try:
        ollama_models: set[str] | None = None

        async def _ollama() -> str:
            nonlocal ollama_models
            ollama_models = await fetch_ollama_models(client, settings.ollama_base_url)
            return f"reachable at {settings.ollama_base_url} ({len(ollama_models)} models)"

        db, chroma, ollama, dock, images, gh = await asyncio.gather(
            _run(
                "database",
                True,
                lambda: check_database(engine),
                "Is Postgres running and DATABASE_URL correct? Did you run `alembic upgrade head`?",
            ),
            _run(
                "chroma",
                True,
                lambda: check_chroma(settings),
                "Start ChromaDB (docker compose up chromadb) and check CHROMA_HOST/CHROMA_PORT.",
            ),
            _run(
                "ollama",
                True,
                _ollama,
                "Start Ollama on the host (`ollama serve`) and check OLLAMA_BASE_URL.",
            ),
            _run(
                "docker",
                True,
                check_docker,
                "Start Docker and make /var/run/docker.sock available to the backend.",
            ),
            _run(
                "sandbox_images",
                settings.sandbox_enabled,
                lambda: check_sandbox_images(settings),
                "Build them with scripts/build_sandbox_images.sh.",
            ),
            _run(
                "github_token",
                False,
                lambda: check_github(client, settings),
                "Only needed for PR delivery.",
            ),
        )

        if ollama_models is None:
            models = CheckResult(
                name="ollama_models",
                ok=False,
                critical=True,
                detail="skipped: Ollama is unreachable.",
            )
        else:
            missing = missing_models(required_models, ollama_models)
            models = CheckResult(
                name="ollama_models",
                ok=not missing,
                critical=True,
                detail=(
                    "all pulled: " + ", ".join(sorted(required_models))
                    if not missing
                    else "missing models. Run: " + " && ".join(f"ollama pull {m}" for m in missing)
                ),
            )
    finally:
        if own_http:
            await client.aclose()

    checks = [db, chroma, ollama, models, dock, images, gh]
    return HealthReport(ok=all(c.ok for c in checks), checks=checks)


def format_failures(report: HealthReport) -> str:
    lines = [f"  - {c.name}: {c.detail}" for c in report.critical_failures]
    return "DevCrew startup health check failed:\n" + "\n".join(lines)
