"""Shared state passed between every node of the StoryForge LangGraph pipeline."""
from __future__ import annotations

from typing import TypedDict


class StoryForgeState(TypedDict):
    # Project metadata
    ppm_number: str
    ppm_name: str
    system_name: str
    job_id: str

    # Pipeline data
    solution_doc_text: str
    solution_doc_path: str
    retrieved_context: dict  # {"manuals": [...], "codebase": [...], "entities": [...]}

    # Clarification
    clarification_needed: bool
    clarification_questions: list[str]
    clarification_answers: dict  # question -> answer

    # Generation output
    generated_stories: list[dict]

    # Control flow
    review_mode: bool
    human_approved: bool
    approved_stories: list[dict]

    # Which output mode this job targets ("document"|"ado"|"notion") -- chosen
    # per-job at submission time (see api/routers/assess.py), rather than
    # always following whatever settings.OUTPUT_MODE currently holds. Older
    # checkpoints predating this field won't have it; pipeline/graph.py's
    # _route_after_review falls back to settings.OUTPUT_MODE in that case.
    output_mode: str

    # ADO results
    ado_results: list[dict]  # [{story_id, story_url, tasks: [{id, url, type}]}]

    # Document export results (used when output_mode == "document")
    document_path: str

    # Notion results (used when output_mode == "notion")
    notion_results: list[dict]  # [{epic_title, page_id, page_url}]

    errors: list[str]
    status: str  # "analyzing|clarifying|generating|reviewing|creating|done|error"


def new_state(
    job_id: str,
    ppm_number: str,
    ppm_name: str,
    system_name: str,
    solution_doc_path: str,
    review_mode: bool,
    output_mode: str,
) -> StoryForgeState:
    """Build a fresh StoryForgeState for a newly submitted assessment job."""
    return StoryForgeState(
        ppm_number=ppm_number,
        ppm_name=ppm_name,
        system_name=system_name,
        job_id=job_id,
        solution_doc_text="",
        solution_doc_path=solution_doc_path,
        retrieved_context={"manuals": [], "codebase": [], "entities": []},
        clarification_needed=False,
        clarification_questions=[],
        clarification_answers={},
        generated_stories=[],
        review_mode=review_mode,
        human_approved=False,
        approved_stories=[],
        output_mode=output_mode,
        ado_results=[],
        document_path="",
        notion_results=[],
        errors=[],
        status="analyzing",
    )
