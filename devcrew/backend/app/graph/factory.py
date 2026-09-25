"""Builds GraphDeps from settings (shared by the API and the CLI scripts)."""

from __future__ import annotations

from app.config import Settings
from app.events.bus import EventBus
from app.graph.runtime import GraphDeps
from app.llm.client import LLMGateway
from app.llm.models_config import load_models_config
from app.prompts import PromptLibrary
from app.sandbox.docker_runner import DockerSandboxRunner
from app.sandbox.service import Sandbox
from app.tools.catalog import RulesCatalog, TemplatesCatalog


def build_llm(settings: Settings) -> LLMGateway:
    return LLMGateway(
        load_models_config(settings.models_config_path),
        base_url=settings.ollama_base_url,
        max_parallel=settings.max_parallel_devs,
        request_timeout_s=settings.llm_request_timeout_s,
    )


def build_deps(
    settings: Settings,
    events: EventBus,
    *,
    llm: LLMGateway | None = None,
    sandbox: Sandbox | None = None,
) -> GraphDeps:
    if sandbox is None and settings.sandbox_enabled:
        sandbox = Sandbox(DockerSandboxRunner(settings))
    return GraphDeps(
        settings=settings,
        llm=llm or build_llm(settings),
        events=events,
        prompts=PromptLibrary(settings.prompts_dir),
        rules=RulesCatalog(settings.rules_dir),
        templates=TemplatesCatalog(settings.templates_dir),
        sandbox=sandbox,
    )
