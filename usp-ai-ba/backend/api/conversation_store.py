"""Backend-persisted conversation memory for Ask Technical/Business
(Phase L-E): file-per-conversation, not a shared flat-list-rewrite (see
api/job_registry.py) -- unlike a job registry's low-frequency writes, a
conversation grows one message at a time and would otherwise mean rewriting
one ever-growing shared JSON file on every single question/answer.

Layout: <JOBS_DIR>/conversations/<owner>/<conversation_id>.json, scoped
per-user by directory nesting alone. conversation_id is always
server-generated (uuid4), never accepted from a client, closing off path
traversal by construction -- there's no way for a caller to influence the
filename.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Literal

from config import settings

Kind = Literal["technical", "business"]


def _owner_dir(owner: str) -> str:
    return os.path.join(settings.JOBS_DIR, "conversations", owner)


def _conversation_path(owner: str, conversation_id: str) -> str:
    return os.path.join(_owner_dir(owner), f"{conversation_id}.json")


def _default_title(kind: Kind) -> str:
    return f"New {kind} conversation"


def create_conversation(owner: str, kind: Kind, title: str | None = None) -> dict:
    conversation_id = str(uuid.uuid4())
    now = time.time()
    conversation = {
        "id": conversation_id,
        "owner": owner,
        "kind": kind,
        "title": title or _default_title(kind),
        "created_at": now,
        "updated_at": now,
        "messages": [],
    }
    os.makedirs(_owner_dir(owner), exist_ok=True)
    with open(_conversation_path(owner, conversation_id), "w") as f:
        json.dump(conversation, f)
    return conversation


def list_conversations(owner: str) -> list[dict]:
    """Summaries only (no "messages" body) -- a conversation list view has
    no need to load every message of every conversation just to render
    title/kind/updated_at."""
    owner_dir = _owner_dir(owner)
    if not os.path.isdir(owner_dir):
        return []

    summaries = []
    for name in os.listdir(owner_dir):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(owner_dir, name)) as f:
            conversation = json.load(f)
        summaries.append(
            {
                "id": conversation["id"],
                "kind": conversation["kind"],
                "title": conversation["title"],
                "created_at": conversation["created_at"],
                "updated_at": conversation["updated_at"],
            }
        )
    summaries.sort(key=lambda c: c["updated_at"], reverse=True)
    return summaries


def get_conversation(owner: str, conversation_id: str) -> dict | None:
    """Returns None for both "doesn't exist" and "exists but belongs to a
    different owner" -- callers must turn a None here into a 404 (not 403),
    so a non-owner can't distinguish "not yours" from "doesn't exist" and
    probe for valid conversation ids."""
    path = _conversation_path(owner, conversation_id)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        conversation = json.load(f)
    if conversation.get("owner") != owner:
        return None
    return conversation


def append_message(owner: str, conversation_id: str, role: str, text: str, sources: list[str]) -> dict | None:
    conversation = get_conversation(owner, conversation_id)
    if conversation is None:
        return None
    conversation["messages"].append({"role": role, "text": text, "sources": sources, "created_at": time.time()})
    conversation["updated_at"] = time.time()
    with open(_conversation_path(owner, conversation_id), "w") as f:
        json.dump(conversation, f)
    return conversation


def delete_conversation(owner: str, conversation_id: str) -> bool:
    if get_conversation(owner, conversation_id) is None:
        return False
    os.remove(_conversation_path(owner, conversation_id))
    return True
