"""decide()'s resilience to a transient Ollama outage: retries a fresh conversation only when
every reply in the failed one came back empty, not when the model responds but is simply wrong.
"""

from __future__ import annotations

from pathlib import Path

from devcrew.graph.coordinator import decide
from tests.devcrew.fakes import Brain, Call, final, truncated
from tests.devcrew.graph_harness import make_harness

RUN = "run-decide-test0000000000"


def scripted_coordinator(*replies: str) -> tuple[Brain, list[Call]]:
    queue = list(replies)
    brain = Brain(responders={"coordinator": lambda c: final(queue.pop(0))})
    return brain, brain.calls


async def test_decide_retries_a_fresh_conversation_after_an_all_empty_one(
    tmp_path: Path,
) -> None:
    # generate_structured's own 3 internal attempts (within one conversation) all come back
    # empty -- the signature of a transient Ollama hiccup -- then a fresh conversation succeeds.
    brain, calls = scripted_coordinator(
        "", "", "", '{"action": "escalate", "reason": "x", "question_for_human": "?"}'
    )
    h = make_harness(
        tmp_path, brain=brain, coordinator_decision_retries=2, coordinator_decision_retry_delay_s=0
    )
    result = await decide(
        h.deps, run_id=RUN, task_id=None, context="ctx", allowed=["escalate"]
    )
    assert result is not None
    assert result.action == "escalate"
    assert len(calls) == 4  # 3 empty attempts in conversation 1 + 1 successful attempt in conversation 2


async def test_decide_retries_a_fresh_conversation_after_an_all_truncated_one(
    tmp_path: Path,
) -> None:
    # Every reply in the first conversation is cut off by the output-token cap (e.g. a verbose
    # "replan" guidance hitting num_predict) -- same outer-retry treatment as all-empty, since
    # this is also a systemic issue rather than the model proposing something wrong on purpose.
    queue = [
        truncated('{"action": "replan", "reason": "x", "guidance": "keep going'),
        truncated('{"action": "replan", "reason": "x", "guidance": "keep going'),
        truncated('{"action": "replan", "reason": "x", "guidance": "keep going'),
        final('{"action": "escalate", "reason": "x", "question_for_human": "?"}'),
    ]
    brain = Brain(responders={"coordinator": lambda c: queue.pop(0)})
    h = make_harness(
        tmp_path, brain=brain, coordinator_decision_retries=2, coordinator_decision_retry_delay_s=0
    )
    result = await decide(
        h.deps, run_id=RUN, task_id=None, context="ctx", allowed=["escalate"]
    )
    assert result is not None
    assert result.action == "escalate"
    # 3 truncated attempts in conversation 1 + 1 successful attempt in conversation 2
    assert len(brain.calls) == 4


async def test_decide_gives_up_after_one_conversation_when_the_model_is_just_wrong(
    tmp_path: Path,
) -> None:
    # The model responds every time, just never with something valid -- retrying from scratch
    # wouldn't fix that, so this must NOT spend a second conversation's worth of attempts.
    brain, calls = scripted_coordinator("not json", "still not json", "nope")
    h = make_harness(
        tmp_path, brain=brain, coordinator_decision_retries=3, coordinator_decision_retry_delay_s=0
    )
    result = await decide(
        h.deps, run_id=RUN, task_id=None, context="ctx", allowed=["escalate"]
    )
    assert result is None
    assert len(calls) == 3  # exactly one conversation's worth of attempts


async def test_decide_gives_up_after_retries_exhausted_on_repeated_empty_responses(
    tmp_path: Path,
) -> None:
    # Every conversation comes back empty -- worth retrying, but not forever.
    brain, calls = scripted_coordinator(*([""] * 6))
    h = make_harness(
        tmp_path, brain=brain, coordinator_decision_retries=2, coordinator_decision_retry_delay_s=0
    )
    result = await decide(
        h.deps, run_id=RUN, task_id=None, context="ctx", allowed=["escalate"]
    )
    assert result is None
    assert len(calls) == 6  # 2 conversations x 3 empty attempts each
