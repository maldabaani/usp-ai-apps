from __future__ import annotations

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app.api.deps import ContainerDep
from app.health import HealthReport, run_health_checks

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthReport)
async def health(container: ContainerDep, response: Response) -> HealthReport:
    if container.engine is None:  # in-memory container (tests / headless)
        return HealthReport(ok=True, checks=[])
    report = await run_health_checks(
        container.settings, container.engine, container.llm.models.required_models()
    )
    if not report.ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report


class ClientConfig(BaseModel):
    """Limits the UI shows and enforces before submitting."""

    max_request_chars: int
    max_dev_iterations: int
    max_parallel_devs: int
    github_enabled: bool
    max_pr_rounds: int


@router.get("/config", response_model=ClientConfig)
async def client_config(container: ContainerDep) -> ClientConfig:
    s = container.settings
    return ClientConfig(
        max_request_chars=s.max_request_chars,
        max_dev_iterations=s.max_dev_iterations,
        max_parallel_devs=s.max_parallel_devs,
        github_enabled=container.deps.github is not None,
        max_pr_rounds=s.max_pr_rounds,
    )
