from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import BACKEND_DIR
from app.llm.models_config import ModelsConfig, ModelSpec, Role, load_models_config


def test_default_config_shares_one_chat_model() -> None:
    cfg = load_models_config(BACKEND_DIR / "config" / "models.yaml")
    assert {cfg.for_role(r).model for r in Role} == {"qwen2.5-coder:14b"}
    assert cfg.required_models() == {"qwen2.5-coder:14b", "nomic-embed-text"}


def test_role_temperatures() -> None:
    cfg = load_models_config(BACKEND_DIR / "config" / "models.yaml")
    assert cfg.for_role(Role.PLANNER).temperature == 0.3
    assert cfg.for_role(Role.ARCHITECT).temperature == 0.3
    for role in (Role.DEVELOPER, Role.REVIEWER, Role.QA):
        assert cfg.for_role(role).temperature == 0.1
    assert cfg.for_role(Role.DEVELOPER).num_ctx == 16384


def test_override_merges_with_defaults() -> None:
    cfg = ModelsConfig.model_validate(
        {
            "defaults": {"model": "a", "num_ctx": 8192, "num_predict": 1024},
            "roles": {"reviewer": {"model": "b"}},
        }
    )
    spec = cfg.for_role(Role.REVIEWER)
    assert spec == ModelSpec(model="b", num_ctx=8192, num_predict=1024, temperature=0.1)
    assert spec.prompt_budget == 8192 - 1024
    assert cfg.required_models() == {"a", "b", "nomic-embed-text"}


def test_num_predict_must_fit_context() -> None:
    with pytest.raises(ValidationError):
        ModelSpec(model="a", num_ctx=2048, num_predict=4096)


def test_unknown_role_rejected() -> None:
    with pytest.raises(ValidationError):
        ModelsConfig.model_validate({"defaults": {"model": "a"}, "roles": {"designer": {}}})


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="MODELS_CONFIG_PATH"):
        load_models_config(tmp_path / "nope.yaml")
