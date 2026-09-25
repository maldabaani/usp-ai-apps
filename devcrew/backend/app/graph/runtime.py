"""Dependencies shared by graph nodes, plus node instrumentation and agent-turn helpers."""

from __future__ import annotations

import functools
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    SystemMessage,
    messages_from_dict,
    messages_to_dict,
)
from langgraph.errors import GraphBubbleUp

from app.config import Settings
from app.events.bus import EventBus
from app.events.types import EventType
from app.github.delivery import GitHubDelivery
from app.graph.state import PendingQuestion, QAEntry
from app.llm.agent import AgentOutcome, ToolEventHook, run_agent
from app.llm.client import LLMGateway
from app.llm.models_config import Role
from app.prompts import PromptLibrary
from app.rag.service import RagService
from app.sandbox.service import Sandbox
from app.tools.base import ToolSpec
from app.tools.catalog import RulesCatalog, TemplatesCatalog

logger = logging.getLogger(__name__)

NodeFn = Callable[[Any], Awaitable[Any]]


@dataclass
class GraphDeps:
    settings: Settings
    llm: LLMGateway
    events: EventBus
    prompts: PromptLibrary
    rules: RulesCatalog
    templates: TemplatesCatalog
    sandbox: Sandbox | None = None  # None: commands/tests are not executed
    rag: RagService | None = None  # None: no retrieval
    github: GitHubDelivery | None = None  # None: no push / PR (benchmarks)

    async def emit(
        self,
        run_id: str,
        type: EventType,
        *,
        node: str | None = None,
        task_id: str | None = None,
        **payload: Any,
    ) -> None:
        await self.events.publish(run_id, type, node=node, task_id=task_id, payload=payload)

    def tool_hook(self, run_id: str, node: str, task_id: str | None) -> ToolEventHook:
        async def hook(kind: Literal["tool_call", "tool_result"], payload: dict[str, Any]) -> None:
            await self.events.publish(
                run_id, EventType(kind), node=node, task_id=task_id, payload=payload
            )

        return hook


async def release_run_resources(deps: GraphDeps, run_id: str) -> None:
    """End of a run (completed, failed or cancelled): drop containers, volumes and the run's
    Chroma collection. Nothing survives into the next run."""
    if deps.sandbox is not None:
        try:
            await deps.sandbox.cleanup_run(run_id)
        except Exception as exc:
            logger.warning("sandbox cleanup failed for %s: %s", run_id, exc)
    if deps.rag is not None:
        try:
            await deps.rag.delete(run_id)
        except Exception as exc:
            logger.warning("RAG cleanup failed for %s: %s", run_id, exc)


async def retrieve(
    deps: GraphDeps,
    run_id: str,
    query: str,
    *,
    k: int | None = None,
    path_filter: str | None = None,
) -> str:
    """Retrieved chunks rendered for a context section ('' when RAG is off or fails)."""
    if deps.rag is None:
        return ""
    try:
        hits = await deps.rag.search(run_id, query, k, path_filter)
    except Exception as exc:
        logger.warning("retrieval failed for %s: %s", run_id, exc)
        return ""
    return "\n\n".join(h.render() for h in hits)


async def reindex(deps: GraphDeps, run_id: str, root: Path, node: str) -> None:
    """Incrementally re-index the checked-out integration branch; failures are reported, not
    fatal (search then serves the previous index)."""
    if deps.rag is None:
        return
    try:
        stats = await deps.rag.sync_workspace(run_id, root)
    except Exception as exc:
        logger.warning("indexing failed for %s: %s", run_id, exc)
        await deps.emit(run_id, EventType.ERROR, node=node, message=f"indexing failed: {exc}")
        return
    await deps.emit(
        run_id,
        EventType.TOOL_RESULT,
        node=node,
        tool="index_codebase",
        ok=True,
        result=f"+{len(stats.added)} ~{len(stats.updated)} -{len(stats.removed)} files, "
        f"{stats.chunks} chunks embedded",
        stats=stats.as_dict(),
    )


def instrument(deps: GraphDeps, name: str, fn: NodeFn) -> NodeFn:
    """Emit node_started / node_finished / error events around a node."""

    @functools.wraps(fn)
    async def wrapper(state: Mapping[str, Any]) -> Any:
        run_id = state["run_id"]
        task = state.get("task")
        task_id = task.get("id") if isinstance(task, dict) else None
        await deps.emit(run_id, EventType.NODE_STARTED, node=name, task_id=task_id)
        try:
            result = await fn(state)
        except GraphBubbleUp:
            raise  # interrupts / parent commands are control flow, not errors
        except Exception as exc:
            logger.exception("node %s failed", name)
            await deps.emit(
                run_id, EventType.ERROR, node=name, task_id=task_id, message=str(exc)[:2000]
            )
            raise
        await deps.emit(run_id, EventType.NODE_FINISHED, node=name, task_id=task_id)
        return result

    return wrapper


def load_transcript(saved: Sequence[dict[str, Any]] | None) -> list[BaseMessage] | None:
    return messages_from_dict(list(saved)) if saved else None


def save_transcript(messages: Sequence[BaseMessage]) -> list[dict[str, Any]]:
    return messages_to_dict(list(messages))


async def agent_turn(
    deps: GraphDeps,
    *,
    role: Role,
    run_id: str,
    node: str,
    task_id: str | None,
    system: str,
    build_context: Callable[[], Awaitable[str]] | Callable[[], str],
    tools: Sequence[ToolSpec],
    saved: Sequence[dict[str, Any]] | None,
) -> AgentOutcome:
    """Start a fresh agent conversation, or continue a saved one (after ask_human)."""
    messages = load_transcript(saved)
    if messages is None:
        context = build_context()
        if not isinstance(context, str):
            context = await context
        messages = [SystemMessage(content=system), HumanMessage(content=context)]
    return await run_agent(
        deps.llm,
        role,
        messages,
        tools,
        max_steps=deps.settings.max_agent_steps,
        on_tool_event=deps.tool_hook(run_id, node, task_id),
    )


def questions_asked(qa_log: Sequence[Mapping[str, Any]], asker: str, task_id: str | None) -> int:
    return sum(1 for e in qa_log if e.get("asker") == asker and e.get("task_id") == task_id)


def qa_entries(
    qa_log: Sequence[Mapping[str, Any]], *, asker: str | None = None, task_id: str | None = None
) -> list[QAEntry]:
    return [
        QAEntry.model_validate(e)
        for e in qa_log
        if (asker is None or e.get("asker") == asker) and e.get("task_id") == task_id
    ]


async def pending_question(
    deps: GraphDeps, run_id: str, role: str, task_id: str | None, outcome: AgentOutcome
) -> PendingQuestion:
    question = PendingQuestion(
        id=uuid.uuid4().hex[:12],
        role=role,
        task_id=task_id,
        question=outcome.question,
        tool_call_id=outcome.tool_call_id,
    )
    await deps.emit(
        run_id,
        EventType.QUESTION,
        node=role,
        task_id=task_id,
        question_id=question.id,
        asker=role,
        target="human",
        question=question.question,
    )
    return question
