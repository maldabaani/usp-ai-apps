"""Per-stack rules files and starter templates."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from devcrew.tools.base import ToolError, ToolSpec

RULE_ID_RE = re.compile(r"\*\*([A-Z]{2,5}-\d{3})\*\*")
RULE_LINE_RE = re.compile(r"^- \*\*([A-Z]{2,5}-\d{3})\*\* (.+)$", re.MULTILINE)
RULE_STACKS = ("python", "java", "angular")
# 900-999 is reserved for rules learned from run friction (devcrew/learning.py) so an approved
# lesson never renumbers or collides with the hand-curated 0xx-8xx ids above.
LEARNED_RULE_PREFIXES = {"python": "PY", "java": "JAVA", "angular": "NG"}
LEARNED_RULE_HEADING = "## Learned from experience"


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

    def rule_texts(self, stacks: list[str]) -> dict[str, str]:
        """Rule id -> the rule's text, e.g. PY-003 -> "Request/response bodies are ..."."""
        texts: dict[str, str] = {}
        for stack in stacks:
            try:
                texts.update(RULE_LINE_RE.findall(self.read(stack)))
            except ToolError:
                continue
        return texts

    def append_rule(self, stack: str, rule_text: str) -> str:
        """Append an approved lesson as a new rule bullet under a "Learned from experience"
        heading, in the 900-999 id range reserved for it. Returns the assigned rule id (e.g.
        "PY-901"). read() is uncached, so the new rule is citable by the Reviewer (rule_ref)
        on the very next run -- no backend restart needed."""
        prefix = LEARNED_RULE_PREFIXES.get(stack)
        if prefix is None:
            raise ToolError(
                f"unknown stack '{stack}'. Use one of: {', '.join(LEARNED_RULE_PREFIXES)}"
            )
        text = self.read(stack)
        used = {
            int(rid.rsplit("-", 1)[1])
            for rid in RULE_ID_RE.findall(text)
            if rid.startswith(f"{prefix}-9")
        }
        next_n = max(used, default=900) + 1
        if next_n > 999:
            raise ToolError(f"no learned-rule ids left for {stack} (900-999 exhausted)")
        rule_id = f"{prefix}-{next_n}"
        line = f"- **{rule_id}** {rule_text.strip()}"
        if LEARNED_RULE_HEADING in text:
            text = text.rstrip("\n") + f"\n{line}\n"
        else:
            text = text.rstrip("\n") + f"\n\n{LEARNED_RULE_HEADING}\n{line}\n"
        (self.rules_dir / f"{stack}.md").write_text(text, encoding="utf-8")
        return rule_id


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
    # Live preview (Phase 16): the command that serves the app and the port it listens on.
    preview_cmd: str | None = None
    preview_port: int | None = Field(default=None, ge=1, le=65535)
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
