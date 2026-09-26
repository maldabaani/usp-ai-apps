from __future__ import annotations

from typing import Any

from langgraph.types import Command

from app.db.models import RunStatus
from app.graph.context_builder import architect_context, budget_for
from app.graph.nodes.planner import escalate, repo_tools
from app.graph.nodes.repository import repo_projects, repo_stack
from app.graph.requirements import effective_request
from app.graph.runtime import (
    GraphDeps,
    NodeFn,
    agent_turn,
    pending_question,
    qa_entries,
    questions_asked,
    save_transcript,
)
from app.graph.state import Design, ExistingDesignDraft, dump, get_plan
from app.graph.steering import notes_text, with_notes
from app.llm.models_config import Role
from app.llm.structured import StructuredOutputError, generate_structured
from app.tools.catalog import list_templates_tool, read_rules_tool
from app.tools.human import ask_human_tool

NODE = "architect"


def make_architect(deps: GraphDeps) -> NodeFn:
    async def architect(state: dict[str, Any]) -> Command[str]:
        run_id = state["run_id"]
        plan = get_plan(state)
        qa_log = state.get("qa_log", [])
        limit_reached = questions_asked(qa_log, NODE, None) >= deps.settings.max_questions_per_task
        existing = bool(state.get("repo_info"))
        system = (
            deps.prompts.get("architect_existing", ExistingDesignDraft)
            if existing
            else deps.prompts.get(NODE, Design)
        )
        previous = Design.model_validate(state["design"]) if state.get("design") else None

        def context() -> str:
            return architect_context(
                effective_request(state),
                plan,
                budget_for(deps.llm.spec(Role.ARCHITECT).prompt_budget, system),
                feedback=state.get("design_feedback"),
                previous_design=previous,
                qa=qa_entries(qa_log, asker=NODE),
                repo=state.get("repo_info"),
                notes=notes_text(state.get("human_notes") or []),
            )

        outcome = await agent_turn(
            deps,
            role=Role.ARCHITECT,
            run_id=run_id,
            node=NODE,
            task_id=None,
            system=system,
            build_context=context,
            tools=(
                [
                    read_rules_tool(deps.rules),
                    ask_human_tool(limit_reached=limit_reached),
                    *repo_tools(deps, state),
                ]
                if existing
                else [
                    list_templates_tool(deps.templates),
                    read_rules_tool(deps.rules),
                    ask_human_tool(limit_reached=limit_reached),
                ]
            ),
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
            if existing:
                draft = await generate_structured(
                    deps.llm,
                    Role.ARCHITECT,
                    outcome.messages[:-1],
                    ExistingDesignDraft,
                    first_response=outcome.final_text,
                )
                projects = repo_projects(state)
                design = Design.model_validate(
                    {
                        **draft.value.model_dump(),
                        "stack": repo_stack(projects),
                        "template_id": "existing",
                        "existing_projects": [p.model_dump() for p in projects],
                    },
                    context={"task_stacks": {t.stack for t in plan.tasks}},
                )
            else:
                design = (
                    await generate_structured(
                        deps.llm,
                        Role.ARCHITECT,
                        outcome.messages[:-1],
                        Design,
                        context={
                            "templates": deps.templates.ids_by_stack(),
                            "task_stacks": {t.stack for t in plan.tasks},
                        },
                        first_response=outcome.final_text,
                    )
                ).value
        except (StructuredOutputError, ValueError) as exc:
            return await escalate(deps, run_id, NODE, str(exc))
        return Command(
            goto="approve_design",
            update={
                "design": dump(design),
                "design_feedback": None,
                "scratch": {NODE: None},
                "status": RunStatus.AWAITING_DESIGN_APPROVAL.value,
            },
        )

    return with_notes(deps, "Architect", architect)
