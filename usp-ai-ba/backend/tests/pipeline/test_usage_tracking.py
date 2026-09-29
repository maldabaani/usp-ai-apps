"""Covers pipeline/nodes/usage_tracking.py: folding one node's LLM calls into
StoryForgeState's running usage total."""
from __future__ import annotations

from pipeline.nodes import usage_tracking


def test_empty_usage_shape():
    assert usage_tracking.empty_usage() == {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "by_node": {},
    }


def test_merge_into_none_state_usage_starts_fresh():
    usage = usage_tracking.merge_into(
        None, "clarify_node", [{"input_tokens": 100, "output_tokens": 20, "model": "qwen2.5:14b"}]
    )

    assert usage["calls"] == 1
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 20
    assert usage["total_tokens"] == 120
    assert usage["by_node"]["clarify_node"] == {
        "calls": 1,
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
        "models": ["qwen2.5:14b"],
    }


def test_merge_into_accumulates_multiple_calls_in_one_node_run():
    calls = [
        {"input_tokens": 100, "output_tokens": 20, "model": "qwen2.5:14b"},
        {"input_tokens": 100, "output_tokens": 16384, "model": "qwen2.5:14b"},  # a retried, truncated attempt
    ]

    usage = usage_tracking.merge_into(None, "generate_node", calls)

    node = usage["by_node"]["generate_node"]
    assert node["calls"] == 2
    assert node["input_tokens"] == 200
    assert node["output_tokens"] == 16404
    assert node["models"] == ["qwen2.5:14b"]  # de-duplicated, not ["qwen2.5:14b", "qwen2.5:14b"]


def test_merge_into_is_additive_across_separate_node_runs():
    """A node re-run (e.g. StoryForge's own retry-assessment flow) adds to
    the running total instead of replacing it -- real tokens were spent
    again, so the job's real cost keeps accumulating."""
    first = usage_tracking.merge_into(
        None, "generate_node", [{"input_tokens": 100, "output_tokens": 50, "model": "qwen2.5:14b"}]
    )

    second = usage_tracking.merge_into(
        first, "generate_node", [{"input_tokens": 100, "output_tokens": 50, "model": "qwen2.5:14b"}]
    )

    assert second["by_node"]["generate_node"]["calls"] == 2
    assert second["input_tokens"] == 200
    assert second["output_tokens"] == 100
    assert second["total_tokens"] == 300


def test_merge_into_keeps_separate_nodes_independent():
    after_clarify = usage_tracking.merge_into(
        None, "clarify_node", [{"input_tokens": 10, "output_tokens": 5, "model": "qwen2.5:14b"}]
    )
    after_generate = usage_tracking.merge_into(
        after_clarify, "generate_node", [{"input_tokens": 200, "output_tokens": 100, "model": "claude-sonnet-5"}]
    )

    assert set(after_generate["by_node"]) == {"clarify_node", "generate_node"}
    assert after_generate["by_node"]["clarify_node"]["calls"] == 1  # untouched by generate_node's merge
    assert after_generate["calls"] == 2
    assert after_generate["input_tokens"] == 210
    assert after_generate["output_tokens"] == 105


def test_merge_into_tolerates_a_partially_shaped_old_dict():
    """A checkpoint from a slightly older version of this field (or a
    hand-written test fixture) that's missing a key shouldn't crash."""
    stale = {"calls": 1, "input_tokens": 10}  # no output_tokens/total_tokens/by_node

    usage = usage_tracking.merge_into(
        stale, "clarify_node", [{"input_tokens": 5, "output_tokens": 5, "model": "m"}]
    )

    assert usage["by_node"]["clarify_node"]["calls"] == 1
    assert usage["input_tokens"] == 5  # stale's own top-level counters are recomputed from by_node, not trusted
    assert usage["output_tokens"] == 5


def test_merge_into_with_no_calls_leaves_totals_unchanged():
    before = usage_tracking.merge_into(
        None, "clarify_node", [{"input_tokens": 10, "output_tokens": 5, "model": "m"}]
    )

    after = usage_tracking.merge_into(before, "clarify_node", [])

    assert after == before
