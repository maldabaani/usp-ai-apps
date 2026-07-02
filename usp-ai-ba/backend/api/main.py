"""FastAPI application entrypoint: app factory, CORS, lifespan, router registration."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import ado, assess, clarify, export, ingest, review
from config import settings
from pipeline.graph import close_graph, get_graph

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("StoryForge AI backend starting up")
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

    app.include_router(ingest.router, prefix="/api")
    app.include_router(assess.router, prefix="/api")
    app.include_router(clarify.router, prefix="/api")
    app.include_router(review.router, prefix="/api")
    app.include_router(ado.router, prefix="/api")
    app.include_router(export.router, prefix="/api")

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
