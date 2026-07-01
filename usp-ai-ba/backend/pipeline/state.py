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

    # ADO results
    ado_results: list[dict]  # [{story_id, story_url, tasks: [{id, url, type}]}]

    # Document export results (used when settings.OUTPUT_MODE == "document")
    document_path: str

    # Notion results (used when settings.OUTPUT_MODE == "notion")
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
        ado_results=[],
        document_path="",
        notion_results=[],
        errors=[],
        status="analyzing",
    )
