from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, Field

from app.tools.base import PauseForHuman, ToolSpec

QUESTION_LIMIT_REPLY = (
    "Question limit reached for this task. Do not ask again: use your best judgment, "
    "and document the assumption in your final answer."
)


class AskHumanArgs(BaseModel):
    question: str = Field(
        min_length=5, description="One specific question. Include the options you considered."
    )


def ask_human_tool(*, limit_reached: bool | Callable[[], bool] = False) -> ToolSpec:
    """`limit_reached` may be a callable, checked when the tool is called (the question budget
    can shrink during a turn, e.g. through ask_agent)."""
    description = (
        "Ask the human user a question when a decision cannot be made from the request and "
        "artifacts. The run pauses until they answer. Use sparingly."
    )
    if callable(limit_reached):
        exhausted = limit_reached

        async def maybe_pause(args: AskHumanArgs) -> str:
            if exhausted():
                return QUESTION_LIMIT_REPLY
            raise PauseForHuman(args.question)

        return ToolSpec("ask_human", description, AskHumanArgs, handler=maybe_pause)
    if limit_reached:

        async def refuse(_: AskHumanArgs) -> str:
            return QUESTION_LIMIT_REPLY

        return ToolSpec("ask_human", description, AskHumanArgs, handler=refuse)
    return ToolSpec("ask_human", description, AskHumanArgs, pauses_for_human=True)
