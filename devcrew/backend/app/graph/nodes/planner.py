from __future__ import annotations

from typing import Any

from langgraph.types import Command

from app.db.models import RunStatus
from app.events.types import EventType
from app.graph.context_builder import budget_for, planner_context
from app.graph.runtime import (
    GraphDeps,
    NodeFn,
    agent_turn,
    pending_question,
    qa_entries,
    questions_asked,
    save_transcript,
)
from app.graph.state import Plan, dump
from app.llm.models_config import Role
from app.llm.structured import StructuredOutputError, generate_structured
from app.tools.human import ask_human_tool

NODE = "planner"


def make_planner(deps: GraphDeps) -> NodeFn:
    async def planner(state: dict[str, Any]) -> Command[str]:
        run_id = state["run_id"]
        qa_log = state.get("qa_log", [])
        limit_reached = questions_asked(qa_log, NODE, None) >= deps.settings.max_questions_per_task
        system = deps.prompts.get(NODE, Plan)
        previous = Plan.model_validate(state["plan"]) if state.get("plan") else None

        def context() -> str:
            return planner_context(
                state["request"],
                budget_for(deps.llm.spec(Role.PLANNER).prompt_budget, system),
                feedback=state.get("plan_feedback"),
                previous_plan=previous,
                qa=qa_entries(qa_log, asker=NODE),
            )

        outcome = await agent_turn(
            deps,
            role=Role.PLANNER,
            run_id=run_id,
            node=NODE,
            task_id=None,
            system=system,
            build_context=context,
            tools=[ask_human_tool(limit_reached=limit_reached)],
            saved=state.get("scratch", {}).get(NODE),
        )
        if outcome.kind == "ask_human":
            question = await pending_question(deps, run_id, NODE, None, outcome)
            return Command(
                goto="ask_human",
                update={
                    "scratch": {NODE: save_transcript(outcome.messages)},
                    "pending_question": dump(question),
                },
            )
        if outcome.kind == "error":
            return await escalate(deps, run_id, NODE, outcome.error)
        try:
            result = await generate_structured(
                deps.llm,
                Role.PLANNER,
                outcome.messages[:-1],
                Plan,
                first_response=outcome.final_text,
            )
        except StructuredOutputError as exc:
            return await escalate(deps, run_id, NODE, str(exc))
        return Command(
            goto="approve_plan",
            update={
                "plan": dump(result.value),
                "plan_feedback": None,
                "scratch": {NODE: None},
                "status": RunStatus.AWAITING_PLAN_APPROVAL,
            },
        )

    return planner


async def escalate(deps: GraphDeps, run_id: str, node: str, reason: str) -> Command[str]:
    """Record the failure (error event + state) and hand over to the Coordinator."""
    await deps.emit(run_id, EventType.ERROR, node=node, message=reason)
    return Command(
        goto="coordinator",
        update={
            "escalation": {"node": node, "reason": reason},
            "scratch": {node: None},
            "errors": [f"{node}: {reason}"],
        },
    )
