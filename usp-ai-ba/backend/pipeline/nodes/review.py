"""Node 4: human-in-the-loop review gate (optional, controlled by ``review_mode``)."""
from __future__ import annotations

from pipeline.state import StoryForgeState


async def review_node(state: StoryForgeState) -> StoryForgeState:
    """Pass generated stories through to approved_stories when review is disabled.

    When ``review_mode`` is True, the graph is interrupted before this node's
    downstream successor (``create_ado_node``) so a human can edit
    ``generated_stories`` via the review API before they are copied into
    ``approved_stories`` and the graph is resumed.
    """
    if not state["review_mode"]:
        return {
            **state,
            "approved_stories": state["generated_stories"],
            "human_approved": True,
            "status": "creating",
        }

    return {
        **state,
        "status": "reviewing",
    }
