"""Role prompt files (backend/prompts/<name>.md)."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from pydantic import BaseModel

from app.llm.structured import schema_prompt


class PromptLibrary:
    def __init__(self, prompts_dir: Path) -> None:
        self.prompts_dir = prompts_dir

    @cache  # noqa: B019 - one library per process, prompts are immutable at runtime
    def _raw(self, name: str) -> str:
        path = self.prompts_dir / f"{name}.md"
        if not path.is_file():
            raise FileNotFoundError(f"prompt file missing: {path}")
        return path.read_text(encoding="utf-8")

    def role_brief(self, name: str) -> str:
        """A role's prompt without its output-format section (for one-shot Q&A)."""
        return self._raw(name).split("\n## Output", 1)[0].rstrip()

    def get(self, name: str, schema: type[BaseModel] | None = None) -> str:
        text = self._raw(name)
        if schema is not None:
            text = text.replace("{schema}", schema_prompt(schema))
        return text
