"""Token usage accounting for the StoryForge assessment pipeline's own LLM
calls (clarify_node/generate_node -- the only two nodes that call an LLM at
all; analyze_node/review_node/export nodes don't).

DevCrew's own usage.py derives its RunUsage from an event log DevCrew
maintains for its multi-agent runs -- StoryForge's pipeline has no such log,
so usage is instead carried forward node-to-node directly on
StoryForgeState, the same way errors/warnings already are (see
pipeline/state.py). Every attempt is counted, not just the one that
eventually succeeds -- a retried/truncated/failed call still spent real
tokens, so it still counts toward the job's real cost.
"""
from __future__ import annotations

NODE_TOTAL_KEYS = ("calls", "input_tokens", "output_tokens")


def empty_usage() -> dict:
    return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "by_node": {}}


def _empty_node_total() -> dict:
    return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "models": []}


def merge_into(state_usage: dict | None, node_name: str, calls: list[dict]) -> dict:
    """Fold one node run's LLM calls (each `{"input_tokens", "output_tokens",
    "model"}`, collected via llm_retry.py's `on_usage` callback) into the
    job's running usage total. `state_usage` is whatever StoryForgeState
    already has under "usage" -- None/missing for a job whose checkpoint
    predates this field, or a fresh job, both treated as empty (matching
    pipeline/state.py's existing convention for a field added after some
    checkpoints already existed)."""
    usage = {**empty_usage(), **(state_usage or {})}
    by_node = dict(usage.get("by_node") or {})
    node_total = {**_empty_node_total(), **(by_node.get(node_name) or {})}

    for call in calls:
        node_total["calls"] += 1
        node_total["input_tokens"] += int(call.get("input_tokens") or 0)
        node_total["output_tokens"] += int(call.get("output_tokens") or 0)
        model = call.get("model")
        if model and model not in node_total["models"]:
            node_total["models"].append(str(model))
    node_total["total_tokens"] = node_total["input_tokens"] + node_total["output_tokens"]

    by_node[node_name] = node_total
    usage["by_node"] = by_node
    for key in NODE_TOTAL_KEYS:
        usage[key] = sum(n[key] for n in by_node.values())
    usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    return usage
