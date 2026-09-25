"""Builds GraphDeps from settings (shared by the API and the CLI scripts)."""

from __future__ import annotations

from app.config import Settings
from app.events.bus import EventBus
from app.github.client import GitHubClient
from app.github.delivery import GitHubDelivery
from app.graph.runtime import GraphDeps
from app.llm.client import LLMGateway
from app.llm.models_config import load_models_config
from app.prompts import PromptLibrary
from app.rag.embeddings import Embedder
from app.rag.service import RagService, chroma_http_client
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
    rag: RagService | None = None,
    github: GitHubDelivery | None = None,
) -> GraphDeps:
    if sandbox is None and settings.sandbox_enabled:
        sandbox = Sandbox(DockerSandboxRunner(settings))
    llm = llm or build_llm(settings)
    if rag is None and settings.rag_enabled:
        rag = RagService(
            chroma_http_client(settings),
            Embedder(
                llm.embeddings(),
                model_name=llm.models.embeddings.model,
                batch_size=settings.rag_embed_batch_size,
            ),
            settings,
        )
    if github is None and settings.github_delivery_enabled:
        token = settings.github_token.get_secret_value() if settings.github_token else ""
        github = GitHubDelivery(
            lambda: GitHubClient(token, settings.github_api_url),
            token=token,
            git_url=settings.github_git_url,
        )
    return GraphDeps(
        settings=settings,
        llm=llm,
        events=events,
        prompts=PromptLibrary(settings.prompts_dir),
        rules=RulesCatalog(settings.rules_dir),
        templates=TemplatesCatalog(settings.templates_dir),
        sandbox=sandbox,
        rag=rag,
        github=github,
    )
