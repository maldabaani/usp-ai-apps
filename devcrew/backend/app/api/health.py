from __future__ import annotations

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app.api.deps import ContainerDep
from app.health import HealthReport, run_health_checks
from app.llm.models_config import Role

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
    max_document_chars: int
    max_dev_iterations: int
    max_parallel_devs: int
    github_enabled: bool
    max_pr_rounds: int
    run_token_budget: int
    run_time_budget_min: int


@router.get("/config", response_model=ClientConfig)
async def client_config(container: ContainerDep) -> ClientConfig:
    s = container.settings
    return ClientConfig(
        max_request_chars=s.max_request_chars,
        max_document_chars=s.max_document_chars,
        max_dev_iterations=s.max_dev_iterations,
        max_parallel_devs=s.max_parallel_devs,
        github_enabled=container.deps.github is not None,
        max_pr_rounds=s.max_pr_rounds,
        run_token_budget=s.run_token_budget,
        run_time_budget_min=s.run_time_budget_min,
    )


class RoleModel(BaseModel):
    role: Role
    model: str  # the default from config/models.yaml


class ModelChoices(BaseModel):
    """What the New run page offers: each role's default and the models Ollama has."""

    roles: list[RoleModel]
    installed: list[str] | None  # None: Ollama could not be asked (any name is accepted)


@router.get("/models", response_model=ModelChoices)
async def models(container: ContainerDep) -> ModelChoices:
    llm = container.llm
    installed = await llm.installed_models()
    return ModelChoices(
        roles=[RoleModel(role=r, model=llm.models.for_role(r).model) for r in Role],
        installed=sorted(installed) if installed is not None else None,
    )
