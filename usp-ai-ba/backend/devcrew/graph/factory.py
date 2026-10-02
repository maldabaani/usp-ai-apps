"""Builds GraphDeps from settings (shared by the API and the CLI scripts)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from devcrew.config import Settings
from devcrew.db.steering import InMemorySteeringStore, SteeringStore
from devcrew.events.bus import EventBus
from devcrew.github.client import GitHubClient
from devcrew.github.delivery import GitHubDelivery
from devcrew.graph.runtime import GraphDeps
from devcrew.llm.client import LLMGateway
from devcrew.llm.models_config import ModelSpec, load_models_config
from devcrew.prompts import PromptLibrary
from devcrew.rag.embeddings import Embedder
from devcrew.rag.service import RagService, chroma_embedded_client
from devcrew.sandbox.docker_preview import DockerPreviewBackend
from devcrew.sandbox.docker_runner import DockerSandboxRunner
from devcrew.sandbox.preview import PreviewManager
from devcrew.sandbox.service import Sandbox
from devcrew.tools.catalog import RulesCatalog, TemplatesCatalog


def build_llm(settings: Settings) -> LLMGateway:
    cloud_spec = None
    anthropic_api_key = None
    if settings.anthropic_api_key is not None:
        anthropic_api_key = settings.anthropic_api_key.get_secret_value()
        cloud_spec = ModelSpec(
            model=settings.anthropic_model,
            provider="anthropic",
            num_ctx=200_000,
            num_predict=8192,
            # temperature left at its default: _default_chat_factory() never passes it to
            # ChatAnthropic for the "anthropic" provider -- current-generation Claude models
            # reject an explicit temperature outright, so this field is simply unused here.
            structured_format="none",
        )
    return LLMGateway(
        load_models_config(settings.models_config_path),
        base_url=settings.ollama_base_url,
        max_parallel=settings.max_parallel_devs,
        request_timeout_s=settings.llm_request_timeout_s,
        cloud_spec=cloud_spec,
        anthropic_api_key=anthropic_api_key,
    )


async def _storyforge_retrieval(query: str, top_k: int) -> dict[str, list[dict]]:
    # Deferred import, same reasoning as chroma_embedded_client() above:
    # devcrew/ must stay importable standalone (outside the merge, where
    # StoryForge's ingestion package doesn't exist on the path) -- this
    # only actually runs if an agent calls the retrieve_storyforge_context
    # tool, which only happens inside the merged app.
    from ingestion.retrieval import retrieve_all_collections

    return await retrieve_all_collections(query, top_k)


def build_deps(
    settings: Settings,
    events: EventBus,
    *,
    llm: LLMGateway | None = None,
    sandbox: Sandbox | None = None,
    rag: RagService | None = None,
    github: GitHubDelivery | None = None,
    steering: SteeringStore | None = None,
    storyforge_retrieval: (
        Callable[[str, int], Awaitable[dict[str, list[dict]]]] | None
    ) = _storyforge_retrieval,
) -> GraphDeps:
    preview = None
    if sandbox is None and settings.sandbox_enabled:
        runner = DockerSandboxRunner(settings)
        sandbox = Sandbox(runner, state_dir=settings.workspaces_dir / ".sandbox-state")
        if settings.preview_enabled:
            preview = PreviewManager(DockerPreviewBackend(runner), sandbox, settings)
    llm = llm or build_llm(settings)
    if rag is None and settings.rag_enabled:
        rag = RagService(
            chroma_embedded_client(),
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
        steering=steering if steering is not None else InMemorySteeringStore(),
        preview=preview,
        storyforge_retrieval=storyforge_retrieval,
    )
