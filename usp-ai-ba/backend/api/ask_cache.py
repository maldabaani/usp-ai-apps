"""In-memory exact-question-match answer cache for Ask Technical/Business
(Phase L-F). No TTL, no size cap in v1 -- the locked-in decision was
in-memory only, so a process restart already bounds worst-case growth; an
LRU cap is a reasonable future follow-up, not built preemptively.

Not explicitly invalidated on ingestion completion -- instead,
ingestion/ingestion_generation.py's counter is baked into every cache key
(build_key), so a bumped generation simply produces different keys from
that point on. Stale entries under the old generation are never looked up
again; they just sit unused until the process restarts.
"""
from __future__ import annotations

import hashlib
import time

_cache: dict[str, dict] = {}


def build_key(kind: str, ingestion_generation: int, prompt_template: str, conversation_context_text: str, question: str) -> str:
    """The two highest-risk omissions this key must never make (see plan
    file section L, Phase F): leaving out conversation_context_text (a
    cached answer generated inside one conversation could leak into an
    unrelated conversation, or a fresh no-conversation ask, asking the exact
    same question text) and leaving out prompt_template (a Settings-page
    prompt edit would otherwise have zero visible effect on already-cached
    questions until a process restart, since the ingestion-generation
    counter alone doesn't change on a prompt edit)."""
    raw = f"{kind}::{ingestion_generation}::{prompt_template}::{conversation_context_text}::{question}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get(key: str) -> dict | None:
    return _cache.get(key)


def put(key: str, answer: str, sources: list[str]) -> None:
    _cache[key] = {"answer": answer, "sources": sources, "created_at": time.time()}
