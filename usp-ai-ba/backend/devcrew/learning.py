"""Lessons: turn real run friction into a candidate stack-rule bullet, queued for human
approval (devcrew/db/lessons.py's LessonStore) before devcrew/tools/catalog.py's
RulesCatalog.append_rule() ever adds it to the stack's own rules file. Capture sources (see
devcrew/db/models.py's LessonSource): a Coordinator escalation, a changes_requested -> approve
review cycle, or a real PR review comment that led to a merged fix.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from devcrew.db.lessons import LessonStore
from devcrew.db.models import Lesson
from devcrew.llm.client import LLMGateway
from devcrew.llm.models_config import Role
from devcrew.llm.structured import StructuredOutputError, generate_structured
from devcrew.prompts import PromptLibrary

NODE = "lesson_summarizer"


class ProposedRule(BaseModel):
    worth_recording: bool = Field(
        description="False if this is a one-off mistake too specific to this run to generalize "
        "into a lasting rule, or something the stack's rules already cover."
    )
    rule_text: str = Field(
        default="",
        description="One general, imperative sentence for the stack's rules file. Empty when "
        "worth_recording is false.",
    )


async def summarize_lesson(
    llm: LLMGateway, prompts: PromptLibrary, *, stack: str, evidence: str
) -> str | None:
    """None if the model judges `evidence` not worth turning into a lasting rule."""
    system = prompts.get(NODE, ProposedRule)
    try:
        result = await generate_structured(
            llm,
            Role.COORDINATOR,
            [
                SystemMessage(content=system),
                HumanMessage(content=f"Stack: {stack}\n\nEvidence:\n{evidence}"),
            ],
            ProposedRule,
        )
    except StructuredOutputError:
        return None
    rule_text = result.value.rule_text.strip()
    return rule_text if result.value.worth_recording and rule_text else None


async def propose_lesson(
    lessons: LessonStore,
    llm: LLMGateway,
    prompts: PromptLibrary,
    *,
    run_id: str | None,
    task_id: str | None,
    stack: str,
    source: str,
    evidence: str,
) -> Lesson | None:
    """Summarize `evidence` into a candidate rule and queue it for human approval; None if
    nothing general was worth recording (not an error -- most friction isn't)."""
    rule_text = await summarize_lesson(llm, prompts, stack=stack, evidence=evidence)
    if rule_text is None:
        return None
    return await lessons.add_lesson(
        run_id=run_id,
        task_id=task_id,
        stack=stack,
        source=source,
        rule_text=rule_text,
        evidence=evidence,
    )
