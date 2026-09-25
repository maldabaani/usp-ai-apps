"""Tool-calling agent loop hardened for local models.

- Malformed tool calls (bad JSON, unknown tool, invalid arguments) get ONE corrective retry;
  a second consecutive malformed turn ends the loop with kind="error" so the caller can emit an
  error event and route to the Coordinator.
- ask_human never blocks inside the loop: the loop returns kind="ask_human" with the transcript
  so the graph can interrupt in a dedicated node and later resume the same conversation.
- The transcript is compacted to stay within the model's prompt budget.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from pydantic import BaseModel, ValidationError

from app.llm.client import LLMGateway
from app.llm.models_config import Role
from app.llm.structured import format_validation_error
from app.llm.tokens import estimate_tokens, message_tokens, messages_tokens, truncate_to_tokens
from app.tools.base import PauseForHuman, ToolError, ToolSpec

logger = logging.getLogger(__name__)

ELIDED = "[earlier tool output elided to fit the context window; call the tool again if needed]"
TOOL_SCHEMA_OVERHEAD_TOKENS = 150  # per bound tool, rough

ToolEventHook = Callable[[Literal["tool_call", "tool_result"], dict[str, Any]], Awaitable[None]]


@dataclass
class AgentOutcome:
    kind: Literal["final", "ask_human", "error"]
    messages: list[BaseMessage]
    final_text: str = ""
    question: str = ""
    tool_call_id: str = ""
    error: str = ""
    steps: int = 0
    tool_calls: int = field(default=0)


def compact_messages(messages: list[BaseMessage], budget: int, keep_last: int = 2) -> None:
    """Shrink the transcript in place until it fits `budget` tokens."""
    if messages_tokens(messages) <= budget:
        return
    cutoff = max(0, len(messages) - keep_last)
    for i in range(cutoff):
        msg = messages[i]
        if isinstance(msg, ToolMessage) and msg.content != ELIDED:
            messages[i] = ToolMessage(content=ELIDED, tool_call_id=msg.tool_call_id, name=msg.name)
            if messages_tokens(messages) <= budget:
                return
    # Last resort: truncate the largest message bodies.
    while messages_tokens(messages) > budget:
        idx = max(range(len(messages)), key=lambda i: message_tokens(messages[i]))
        msg = messages[idx]
        text = msg.content if isinstance(msg.content, str) else str(msg.content)
        overflow = messages_tokens(messages) - budget
        target = max(64, estimate_tokens(text) - overflow - 64)
        if target >= estimate_tokens(text):
            return  # cannot shrink further
        messages[idx] = msg.model_copy(update={"content": truncate_to_tokens(text, target)})


def _malformed_feedback(problems: list[str], tools: Sequence[ToolSpec]) -> HumanMessage:
    names = ", ".join(t.name for t in tools)
    return HumanMessage(
        content=(
            "Your last tool call was malformed and was NOT executed:\n"
            + "\n".join(f"- {p}" for p in problems)
            + f"\nCall one of [{names}] with arguments that match its JSON schema exactly, "
            "or reply without a tool call if you are done."
        )
    )


async def run_agent(
    gateway: LLMGateway,
    role: Role,
    messages: Sequence[BaseMessage],
    tools: Sequence[ToolSpec],
    *,
    max_steps: int,
    on_tool_event: ToolEventHook | None = None,
    max_tool_output_tokens: int = 3000,
) -> AgentOutcome:
    transcript = list(messages)
    by_name = {t.name: t for t in tools}
    schemas = [t.schema() for t in tools]
    budget = gateway.spec(role).prompt_budget - TOOL_SCHEMA_OVERHEAD_TOKENS * len(tools)
    retried_malformed = False
    tool_calls_made = 0

    async def emit(kind: Literal["tool_call", "tool_result"], payload: dict[str, Any]) -> None:
        if on_tool_event is not None:
            await on_tool_event(kind, payload)

    for step in range(1, max_steps + 1):
        compact_messages(transcript, budget)
        ai = await gateway.ainvoke(role, transcript, tools=schemas or None)
        transcript.append(ai)

        problems: list[str] = []
        for bad in ai.invalid_tool_calls:
            problems.append(
                f"{bad.get('name') or '?'}: {bad.get('error') or 'arguments are not valid JSON'}"
            )
        valid: list[tuple[str, ToolSpec, BaseModel]] = []
        for call in ai.tool_calls:
            call_id = call.get("id") or uuid.uuid4().hex
            spec = by_name.get(call["name"])
            if spec is None:
                problems.append(f"unknown tool '{call['name']}'")
                continue
            try:
                valid.append((call_id, spec, spec.args_model.model_validate(call["args"])))
            except ValidationError as exc:
                problems.append(
                    f"{call['name']}: invalid arguments\n{format_validation_error(exc)}"
                )

        if problems:
            if retried_malformed:
                return AgentOutcome(
                    kind="error",
                    messages=transcript,
                    error="malformed tool call after corrective retry: " + "; ".join(problems),
                    steps=step,
                    tool_calls=tool_calls_made,
                )
            retried_malformed = True
            for call in ai.tool_calls:
                if call.get("id"):
                    transcript.append(
                        ToolMessage(content="ERROR: not executed", tool_call_id=call["id"])
                    )
            transcript.append(_malformed_feedback(problems, tools))
            continue
        retried_malformed = False

        if not ai.tool_calls:
            return AgentOutcome(
                kind="final",
                messages=transcript,
                final_text=ai.text,
                steps=step,
                tool_calls=tool_calls_made,
            )

        pause: tuple[str, str] | None = None
        for call_id, spec, args in valid:
            if spec.pauses_for_human:
                if pause is None:
                    pause = (call_id, str(getattr(args, "question", "")))
                else:
                    transcript.append(
                        ToolMessage(
                            content="ERROR: ask one question at a time", tool_call_id=call_id
                        )
                    )
                continue
            tool_calls_made += 1
            await emit("tool_call", {"tool": spec.name, "args": args.model_dump()})
            try:
                assert spec.handler is not None
                result = await spec.handler(args)
                ok = True
            except PauseForHuman as exc:
                if pause is None:
                    pause = (call_id, exc.question)
                    await emit("tool_result", {"tool": spec.name, "ok": True, "routed": "human"})
                    continue
                result, ok = "ERROR: ask one question at a time", False
            except ToolError as exc:
                result, ok = f"ERROR: {exc}", False
            except Exception as exc:
                logger.exception("tool %s crashed", spec.name)
                result, ok = f"ERROR: tool failed: {exc}", False
            result = truncate_to_tokens(result, max_tool_output_tokens)
            transcript.append(ToolMessage(content=result, tool_call_id=call_id, name=spec.name))
            await emit("tool_result", {"tool": spec.name, "ok": ok, "result": result[:2000]})

        if pause is not None:
            call_id, question = pause
            return AgentOutcome(
                kind="ask_human",
                messages=transcript,
                question=question,
                tool_call_id=call_id,
                steps=step,
                tool_calls=tool_calls_made,
            )

    return AgentOutcome(
        kind="error",
        messages=transcript,
        error=f"step limit ({max_steps}) reached without a final answer",
        steps=max_steps,
        tool_calls=tool_calls_made,
    )
