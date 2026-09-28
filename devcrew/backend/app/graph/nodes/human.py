"""ask_human node: interrupts with the pending question, then resumes the asking agent."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.types import Command

from app.events.types import EventType
from app.graph.interrupts import InterruptKind, InterruptRequest, ResumeAction, request_input
from app.graph.runtime import GraphDeps, NodeFn, save_transcript
from app.graph.state import PendingQuestion, QAEntry, dump


def make_ask_human(deps: GraphDeps, *, scratch_key: str, pending_key: str) -> NodeFn:
    """Build an ask_human node for a graph whose transcripts live in `scratch_key`."""

    async def ask_human(state: dict[str, Any]) -> Command[str]:
        question = PendingQuestion.model_validate(state[pending_key])
        payload = request_input(
            InterruptRequest(
                kind=InterruptKind.QUESTION,
                title=f"The {question.role} has a question",
                allowed_actions=[ResumeAction.ANSWER],
                data={
                    "question_id": question.id,
                    "role": question.role,
                    "task_id": question.task_id,
                    "question": question.question,
                },
            )
        )
        answer = (payload.answer or "").strip()
        transcript = list(state[scratch_key][question.role]) + save_transcript(
            [ToolMessage(content=answer, tool_call_id=question.tool_call_id, name="ask_human")]
        )
        entry = QAEntry(
            id=question.id,
            task_id=question.task_id,
            asker=question.role,
            target="human",
            question=question.question,
            answer=answer,
        )
        await deps.emit(
            state["run_id"],
            EventType.ANSWER,
            node="ask_human",
            task_id=question.task_id,
            question_id=question.id,
            answer=answer,
        )
        return Command(
            goto=question.role,
            update={
                scratch_key: {question.role: transcript},
                pending_key: None,
                "qa_log": [dump(entry)],
            },
        )

    return ask_human
