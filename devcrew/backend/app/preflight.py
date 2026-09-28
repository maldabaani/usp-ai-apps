"""Pre-run checks for the headless CLI scripts (run_local.py, run_benchmark.py)."""

from __future__ import annotations

from collections.abc import Iterable

import httpx

from app.config import Settings
from app.health import check_sandbox_images, fetch_ollama_models, missing_models


class PreflightError(RuntimeError):
    """Something a run needs is missing; the message says how to fix it."""


async def preflight(settings: Settings, required_models: Iterable[str]) -> None:
    try:
        async with httpx.AsyncClient(timeout=5) as http:
            available = await fetch_ollama_models(http, settings.ollama_base_url)
    except httpx.HTTPError as exc:
        raise PreflightError(
            f"Ollama is not reachable at {settings.ollama_base_url} ({exc}). "
            "Start `ollama serve` or set OLLAMA_BASE_URL."
        ) from exc
    missing = missing_models(set(required_models), available)
    if missing:
        raise PreflightError(
            "Missing models. Run: " + " && ".join(f"ollama pull {m}" for m in missing)
        )
    if settings.sandbox_enabled:
        try:
            await check_sandbox_images(settings)
        except Exception as exc:
            raise PreflightError(
                f"Sandbox not ready: {exc}. Run scripts/build_sandbox_images.sh "
                "(or set SANDBOX_ENABLED=false to skip executing tests)."
            ) from exc
