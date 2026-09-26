"""Per-stack rules files and starter templates."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.tools.base import ToolError, ToolSpec

RULE_ID_RE = re.compile(r"\*\*([A-Z]{2,5}-\d{3})\*\*")
RULE_STACKS = ("python", "java", "angular")


class RulesCatalog:
    def __init__(self, rules_dir: Path) -> None:
        self.rules_dir = rules_dir

    def stacks(self) -> list[str]:
        return sorted(p.stem for p in self.rules_dir.glob("*.md"))

    def read(self, stack: str) -> str:
        if stack not in RULE_STACKS:
            raise ToolError(f"unknown stack '{stack}'. Use one of: {', '.join(RULE_STACKS)}")
        path = self.rules_dir / f"{stack}.md"
        if not path.is_file():
            raise ToolError(f"no rules file for {stack}")
        return path.read_text(encoding="utf-8")

    def rule_ids(self, stacks: list[str] | None = None) -> set[str]:
        ids: set[str] = set()
        for stack in stacks or self.stacks():
            try:
                ids.update(RULE_ID_RE.findall(self.read(stack)))
            except ToolError:
                continue
        return ids


class TemplateInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    stack: str
    description: str
    install_cmd: str  # the only command that runs with network access
    build_cmd: str
    test_cmd: str
    # Test command that also prints a coverage summary (used when quality gates are on).
    coverage_cmd: str | None = None
    path: Path = Field(exclude=True, default=Path())


class TemplatesCatalog:
    def __init__(self, templates_dir: Path) -> None:
        self.templates_dir = templates_dir

    def list(self) -> list[TemplateInfo]:
        out = []
        for meta in sorted(self.templates_dir.glob("*/template.yaml")):
            data = yaml.safe_load(meta.read_text(encoding="utf-8")) or {}
            out.append(TemplateInfo.model_validate({**data, "path": meta.parent}))
        return out

    def get(self, template_id: str) -> TemplateInfo:
        for info in self.list():
            if info.id == template_id:
                return info
        raise KeyError(template_id)

    def ids_by_stack(self) -> dict[str, str]:
        return {t.id: t.stack for t in self.list()}


class ReadRulesArgs(BaseModel):
    stack: str = Field(description="One of: python, java, angular.")


class ListTemplatesArgs(BaseModel):
    pass


def read_rules_tool(rules: RulesCatalog) -> ToolSpec:
    async def handler(args: ReadRulesArgs) -> str:
        return rules.read(args.stack)

    return ToolSpec(
        "read_rules",
        "Read the coding standards for a stack (rule ids like PY-001).",
        ReadRulesArgs,
        handler,
    )


def list_templates_tool(templates: TemplatesCatalog) -> ToolSpec:
    async def handler(_: ListTemplatesArgs) -> str:
        items = templates.list()
        if not items:
            return "(no templates installed)"
        return "\n".join(
            f"- id={t.id} stack={t.stack}: {t.description} (test: {t.test_cmd})" for t in items
        )

    return ToolSpec(
        "list_templates", "List available starter templates.", ListTemplatesArgs, handler
    )
