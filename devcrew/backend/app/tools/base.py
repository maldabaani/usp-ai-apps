from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


class ToolError(Exception):
    """An expected tool failure; its message is returned to the model as `ERROR: ...`."""


class PauseForHuman(Exception):
    """Raised by a tool handler to route a question to the human (e.g. ask_agent escalation)."""

    def __init__(self, question: str) -> None:
        super().__init__(question)
        self.question = question


Handler = Callable[[Any], Awaitable[str]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Handler | None = None
    # ask_human: the agent loop stops and the graph interrupts instead of calling a handler.
    pauses_for_human: bool = False

    def __post_init__(self) -> None:
        if self.handler is None and not self.pauses_for_human:
            raise ValueError(f"tool {self.name} needs a handler")

    def schema(self) -> dict[str, Any]:
        params = self.args_model.model_json_schema()
        params.pop("title", None)
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": params},
        }
