from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request, status

from devcrew.container import Container


def get_container(request: Request) -> Container:
    container = request.app.state.container
    if container is None:
        # Merged into StoryForge's backend: a DevCrew-specific startup
        # failure (e.g. Ollama/Postgres/GitHub unreachable) is logged loudly
        # by api/main.py's lifespan but never aborts the whole process --
        # StoryForge's own unrelated routes must keep working. DevCrew's own
        # routes surface that failure here as a clean 503 instead of an
        # AttributeError crash on first container.* access.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "DevCrew failed to start (see server logs) -- its routes are unavailable.",
        )
    return cast(Container, container)


ContainerDep = Annotated[Container, Depends(get_container)]
