"""FastAPI application entrypoint: app factory, CORS, lifespan, router registration."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routers import (
    ado,
    ask,
    assess,
    auth,
    clarify,
    conversations,
    corpus,
    export,
    ingest,
    monitoring,
    prompts,
    review,
    watch,
)
from api.routers import settings as settings_router
from api.user_store import ensure_default_admin
from config import settings
from devcrew.api import github as devcrew_github
from devcrew.api import health as devcrew_health
from devcrew.api import preview as devcrew_preview
from devcrew.api import runs as devcrew_runs
from devcrew.api import workspace as devcrew_workspace
from devcrew.config import get_settings as get_devcrew_settings
from devcrew.main import StartupHealthError, production_container
from ingestion.watcher import watcher
from monitoring.log_capture import install as install_error_capture
from pipeline.graph import close_graph, get_graph

logging.basicConfig(level=logging.INFO)
install_error_capture()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("StoryForge AI backend starting up")
    ensure_default_admin()
    await get_graph()  # open the persistent checkpoint DB now, not on first request
    await watcher.start_all()

    # DevCrew: assembled as its own Container (its own Postgres engine/
    # checkpointer, independent of StoryForge's SQLite one -- see the merge
    # plan's decision C) and stashed on app.state, exactly where
    # devcrew/api/deps.py's ContainerDep already expects to find it.
    # A DevCrew-side startup failure (e.g. Ollama/GitHub unreachable) is
    # logged loudly but must never take the whole merged backend down --
    # StoryForge's own features are unrelated and must keep working.
    devcrew_settings = get_devcrew_settings()
    app.state.container = None
    devcrew_watcher_task: asyncio.Task | None = None
    devcrew_container_cm = production_container(devcrew_settings)
    try:
        devcrew_container = await devcrew_container_cm.__aenter__()
        app.state.container = devcrew_container
        recovered = await devcrew_container.manager.recover()
        if recovered:
            logger.info("devcrew: resumed %d run(s) after restart: %s", len(recovered), recovered)
        if devcrew_container.watcher.github is not None:
            devcrew_watcher_task = asyncio.create_task(devcrew_container.watcher.run_forever())
    except StartupHealthError as exc:
        logger.error("devcrew: startup health check failed, DevCrew routes will 503: %s", exc)
    except Exception:
        logger.exception("devcrew: failed to start, DevCrew routes will 503")

    yield

    watcher.stop_all()
    await close_graph()
    if devcrew_watcher_task is not None:
        devcrew_watcher_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await devcrew_watcher_task
    if app.state.container is not None:
        await devcrew_container_cm.__aexit__(None, None, None)
    logger.info("StoryForge AI backend shutting down")


def create_app() -> FastAPI:
    app = FastAPI(title="StoryForge AI", version="1.0.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router, prefix="/api")
    app.include_router(ask.router, prefix="/api")
    app.include_router(ingest.router, prefix="/api")
    app.include_router(assess.router, prefix="/api")
    app.include_router(clarify.router, prefix="/api")
    app.include_router(review.router, prefix="/api")
    app.include_router(ado.router, prefix="/api")
    app.include_router(export.router, prefix="/api")
    app.include_router(settings_router.router, prefix="/api")
    app.include_router(monitoring.router, prefix="/api")
    app.include_router(corpus.router, prefix="/api")
    app.include_router(watch.router, prefix="/api")
    app.include_router(prompts.router, prefix="/api")
    app.include_router(conversations.router, prefix="/api")

    # DevCrew (merged sibling package, see usp-ai-ba/backend/devcrew/) --
    # namespaced under /api/devcrew so its /health, /runs, etc. never
    # collide with StoryForge's own same-named top-level routes.
    app.include_router(devcrew_health.router, prefix="/api/devcrew")
    app.include_router(devcrew_runs.router, prefix="/api/devcrew")
    app.include_router(devcrew_workspace.router, prefix="/api/devcrew")
    app.include_router(devcrew_github.router, prefix="/api/devcrew")
    app.include_router(devcrew_preview.router, prefix="/api/devcrew")

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        # Routes through the same logger install_error_capture() is attached
        # to, so an unhandled 500 lands in the monitoring store too, not just
        # the server console.
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    async def health():
        return {
            "status": "ok",
            "output_mode": settings.OUTPUT_MODE,
            "notion_configured": bool(settings.NOTION_API_KEY and settings.NOTION_DATABASE_ID),
            "ado_configured": bool(settings.ADO_ORGANIZATION and settings.ADO_PROJECT),
            "anthropic_configured": bool(settings.ANTHROPIC_API_KEY),
        }

    app.get("/health")(health)
    app.get("/api/health")(health)

    return app


app = create_app()
