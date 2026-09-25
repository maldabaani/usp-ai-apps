from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import BACKEND_DIR, Settings

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://u:p@localhost:1/none",
        ollama_base_url="http://ollama.test",
        github_api_url="https://github.test",
        github_token=None,
        models_config_path=BACKEND_DIR / "config" / "models.yaml",
        _env_file=None,
    )


@pytest.fixture
async def pg_engine() -> AsyncIterator[AsyncEngine]:
    """A clean schema on TEST_DATABASE_URL; tests using it are skipped when unset."""
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    from app.db.base import Base
    from app.db.session import create_engine

    engine = create_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()
