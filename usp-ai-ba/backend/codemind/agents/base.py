"""LogicExtractionAgent protocol + ExtractionResult.

Ported from com.jslogicextractor.agent.{LogicExtractionAgent,ExtractionResult}.
ExtractionResult keeps snake_case Python attributes but serializes to the
exact camelCase JSON shape Java's record produced (relativePath/agentName/...),
since that on-disk shape is what codemind.output writes and codemind.qa reads
back -- and what the Angular UI / any side-by-side parity diff depends on.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from codemind.models import SourceFile


@dataclass(frozen=True)
class ExtractionResult:
    relative_path: str
    agent_name: str
    success: bool
    skipped: bool
    content: str | None
    error_message: str | None
    duration_millis: int
    prompt_tokens: int | None
    completion_tokens: int | None

    def to_dict(self) -> dict:
        return {
            "relativePath": self.relative_path,
            "agentName": self.agent_name,
            "success": self.success,
            "skipped": self.skipped,
            "content": self.content,
            "errorMessage": self.error_message,
            "durationMillis": self.duration_millis,
            "promptTokens": self.prompt_tokens,
            "completionTokens": self.completion_tokens,
        }

    @staticmethod
    def from_dict(data: dict) -> "ExtractionResult":
        return ExtractionResult(
            relative_path=data.get("relativePath"),
            agent_name=data.get("agentName"),
            success=bool(data.get("success", False)),
            skipped=bool(data.get("skipped", False)),
            content=data.get("content"),
            error_message=data.get("errorMessage"),
            duration_millis=data.get("durationMillis") or 0,
            prompt_tokens=data.get("promptTokens"),
            completion_tokens=data.get("completionTokens"),
        )


def success_result(
    file: SourceFile,
    agent_name: str,
    content: str,
    duration_millis: int,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> ExtractionResult:
    return ExtractionResult(
        file.relative_path, agent_name, True, False, content, None, duration_millis, prompt_tokens, completion_tokens
    )


def failure_result(file: SourceFile, agent_name: str, error_message: str, duration_millis: int) -> ExtractionResult:
    return ExtractionResult(file.relative_path, agent_name, False, False, None, error_message, duration_millis, None, None)


def skipped_result(file: SourceFile, agent_name: str, reason: str) -> ExtractionResult:
    return ExtractionResult(file.relative_path, agent_name, True, True, None, reason, 0, None, None)


class LogicExtractionAgent(Protocol):
    def name(self) -> str: ...

    async def extract(self, file: SourceFile) -> ExtractionResult: ...
