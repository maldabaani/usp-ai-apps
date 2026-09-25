from __future__ import annotations

from pydantic import BaseModel, Field

from app.tools.base import ToolSpec

QUESTION_LIMIT_REPLY = (
    "Question limit reached for this task. Do not ask again: use your best judgment, "
    "and document the assumption in your final answer."
)


class AskHumanArgs(BaseModel):
    question: str = Field(
        min_length=5, description="One specific question. Include the options you considered."
    )


def ask_human_tool(*, limit_reached: bool = False) -> ToolSpec:
    description = (
        "Ask the human user a question when a decision cannot be made from the request and "
        "artifacts. The run pauses until they answer. Use sparingly."
    )
    if limit_reached:

        async def refuse(_: AskHumanArgs) -> str:
            return QUESTION_LIMIT_REPLY

        return ToolSpec("ask_human", description, AskHumanArgs, handler=refuse)
    return ToolSpec("ask_human", description, AskHumanArgs, pauses_for_human=True)
