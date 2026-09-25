from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.api.deps import ContainerDep
from app.health import HealthReport, run_health_checks

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthReport)
async def health(container: ContainerDep, response: Response) -> HealthReport:
    report = await run_health_checks(
        container.settings, container.engine, container.llm.models.required_models()
    )
    if not report.ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report
