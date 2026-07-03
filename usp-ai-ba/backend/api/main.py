"""FastAPI application entrypoint: app factory, CORS, lifespan, router registration."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routers import ado, assess, auth, clarify, codemind_jobs, export, ingest, monitoring, review
from api.routers import settings as settings_router
from api.user_store import ensure_default_admin
from codemind import job_registry
from config import settings
from monitoring.log_capture import install as install_error_capture
from pipeline.graph import close_graph, get_graph

logging.basicConfig(level=logging.INFO)
install_error_capture()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("StoryForge AI backend starting up")
    ensure_default_admin()
    job_registry.load_persisted_jobs()
    await get_graph()  # open the persistent checkpoint DB now, not on first request
    yield
    await close_graph()
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
    app.include_router(ingest.router, prefix="/api")
    app.include_router(assess.router, prefix="/api")
    app.include_router(clarify.router, prefix="/api")
    app.include_router(review.router, prefix="/api")
    app.include_router(ado.router, prefix="/api")
    app.include_router(export.router, prefix="/api")
    app.include_router(settings_router.router, prefix="/api")
    app.include_router(monitoring.router, prefix="/api")
    app.include_router(codemind_jobs.router, prefix="/api")

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
