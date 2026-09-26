"""ask_agent: a scoped one-shot question to the Architect or Planner.

It does NOT run the target's node: the target role's prompt (without its output section), the
question and the relevant artifacts go into one structured call. If the target says a human
decision is needed, the question is routed to the human (the graph interrupts) and the answer
resumes the asking agent.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.events.types import EventType
from app.graph.context_builder import (
    Section,
    budget_for,
    fit_sections,
    render_contracts,
    render_plan_tasks,
    render_stories,
    render_task,
)
from app.graph.state import AgentAnswer, Design, Plan, PlanTask, QAEntry
from app.llm.models_config import Role
from app.llm.structured import StructuredOutputError, generate_structured
from app.tools.base import PauseForHuman, ToolError, ToolSpec
from app.tools.human import QUESTION_LIMIT_REPLY

if TYPE_CHECKING:  # avoid an import cycle with app.graph.runtime
    from app.graph.runtime import GraphDeps


@dataclass
class QuestionBudget:
    """Questions a task may still ask (ask_agent + ask_human share MAX_QUESTIONS_PER_TASK)."""

    asked: int
    limit: int
    log: list[QAEntry] = field(default_factory=list)  # ask_agent Q&A produced in this turn

    @property
    def exhausted(self) -> bool:
        return self.asked + len(self.log) >= self.limit


class AskAgentArgs(BaseModel):
    target: Literal["architect", "planner"] = Field(
        description="architect: design/contracts/structure. planner: scope/requirements."
    )
    question: str = Field(min_length=5, description="One specific question.")


def _artifacts(target: str, task: PlanTask, plan: Plan, design: Design, budget: int) -> str:
    sections = [
        Section("Asking developer's task", render_task(task), priority=0, required=True),
    ]
    if target == "architect":
        sections += [
            Section("Design contracts", render_contracts(design, task.target_files), 1),
            Section("Key decisions", "\n".join(f"- {d}" for d in design.key_decisions), 2),
            Section("Design document", design.design_doc, 3),
        ]
    else:
        sections += [
            Section("Plan summary", plan.summary, 1),
            Section("User stories and acceptance criteria", render_stories(plan), 1),
            Section("All tasks", render_plan_tasks(plan), 2),
        ]
    return fit_sections(sections, budget)


def ask_agent_tool(
    deps: GraphDeps,
    *,
    run_id: str,
    asker: str,
    task: PlanTask,
    plan: Plan,
    design: Design,
    budget: QuestionBudget,
    route_to_human: Callable[[str, str, str], str] = lambda target, question, reason: (
        f"[The {target} could not settle this and asked you: {reason}] {question}"
    ),
) -> ToolSpec:
    async def handler(args: AskAgentArgs) -> str:
        if budget.exhausted:
            return QUESTION_LIMIT_REPLY
        role = Role(args.target)
        system = (
            deps.prompts.role_brief(args.target)
            + "\n\n"
            + deps.prompts.get("ask_agent", AgentAnswer)
        )
        context = _artifacts(
            args.target, task, plan, design, budget_for(deps.llm.spec(role).prompt_budget, system)
        )
        question_id = uuid.uuid4().hex[:12]
        await deps.emit(
            run_id,
            EventType.QUESTION,
            node=asker,
            task_id=task.id,
            question_id=question_id,
            asker=asker,
            target=args.target,
            question=args.question,
        )
        try:
            reply = await generate_structured(
                deps.llm,
                role,
                [
                    SystemMessage(content=system),
                    HumanMessage(content=f"{context}\n## Question\n{args.question}"),
                ],
                AgentAnswer,
            )
        except StructuredOutputError as exc:
            raise ToolError(f"the {args.target} did not produce an answer: {exc}") from exc
        answer = reply.value
        await deps.emit(
            run_id,
            EventType.ANSWER,
            node=args.target,
            task_id=task.id,
            question_id=question_id,
            answer=answer.answer,
            needs_human=answer.needs_human,
        )
        budget.log.append(
            QAEntry(
                id=question_id,
                task_id=task.id,
                asker=asker,
                target=args.target,
                question=args.question,
                answer=answer.answer,
            )
        )
        if answer.needs_human:
            # The Coordinator's routing rule: agents escalate product decisions to the human.
            raise PauseForHuman(
                route_to_human(args.target, args.question, answer.reason or answer.answer)
            )
        return answer.answer

    return ToolSpec(
        "ask_agent",
        "Ask the Architect (design, contracts, structure) or the Planner (scope, requirements) "
        "a question. Prefer this over ask_human for anything the plan or design should settle.",
        AskAgentArgs,
        handler,
    )
