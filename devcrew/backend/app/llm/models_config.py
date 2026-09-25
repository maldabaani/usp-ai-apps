"""Per-role model configuration loaded from config/models.yaml."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Role(StrEnum):
    PLANNER = "planner"
    ARCHITECT = "architect"
    DEVELOPER = "developer"
    REVIEWER = "reviewer"
    QA = "qa"
    COORDINATOR = "coordinator"  # non-persona exception handler


class ModelSpec(BaseModel):
    """Fully resolved settings for one chat model invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    num_ctx: int = Field(default=16384, ge=1024)
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    num_predict: int = Field(default=4096, ge=1)
    # How structured outputs are constrained: Ollama JSON-schema decoding, plain JSON mode,
    # or unconstrained (prompt + validator only).
    structured_format: Literal["schema", "json", "none"] = "schema"

    @model_validator(mode="after")
    def _answer_fits_context(self) -> ModelSpec:
        if self.num_predict >= self.num_ctx:
            raise ValueError("num_predict must be smaller than num_ctx")
        return self

    @property
    def prompt_budget(self) -> int:
        """Max prompt tokens so that prompt + answer stays within num_ctx."""
        return self.num_ctx - self.num_predict


class ModelOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = None
    num_ctx: int | None = None
    temperature: float | None = None
    num_predict: int | None = None
    structured_format: Literal["schema", "json", "none"] | None = None


class EmbeddingSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str = "nomic-embed-text"


class ModelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    embeddings: EmbeddingSpec = EmbeddingSpec()
    defaults: ModelSpec
    roles: dict[Role, ModelOverride] = Field(default_factory=dict)

    def for_role(self, role: Role) -> ModelSpec:
        override = self.roles.get(role)
        if override is None:
            return self.defaults
        merged: dict[str, Any] = self.defaults.model_dump()
        merged.update(override.model_dump(exclude_none=True))
        return ModelSpec.model_validate(merged)

    def required_models(self) -> set[str]:
        """Every model name that must be pulled in Ollama."""
        names = {self.for_role(role).model for role in Role}
        names.add(self.embeddings.model)
        return names


def load_models_config(path: Path) -> ModelsConfig:
    if not path.is_file():
        raise FileNotFoundError(f"Models config not found: {path} (set MODELS_CONFIG_PATH)")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ModelsConfig.model_validate(data)
