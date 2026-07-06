"""In-memory-only counter bumped on every successful ingestion run, used
purely as a cache-invalidation signal for api/ask_cache.py (Phase L-F).
Deliberately separate from settings.settings_generation -- that counter
tracks LLM/embedding *configuration* changes, this one tracks corpus
*content* changes -- and deliberately not persisted, coherent with the
in-memory answer cache's own restart-resets-both design (a bounded,
accepted v1 tradeoff, not an oversight).
"""
from __future__ import annotations

_generation = 0


def bump() -> None:
    global _generation
    _generation += 1


def current() -> int:
    return _generation
