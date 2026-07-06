"""Covers api/conversation_store.py's file-per-conversation CRUD (Phase
L-E): creation, listing (summaries only), ownership isolation (a non-owner
id lookup returns None, same as a genuinely unknown id -- so callers can't
distinguish "not yours" from "doesn't exist"), message append, and delete.
"""
from __future__ import annotations

import pytest

from api import conversation_store
from config import settings


@pytest.fixture(autouse=True)
def _isolated_jobs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "JOBS_DIR", str(tmp_path / "jobs"))


def test_create_conversation_has_expected_shape():
    conversation = conversation_store.create_conversation("alice", "technical")

    assert conversation["owner"] == "alice"
    assert conversation["kind"] == "technical"
    assert conversation["title"] == "New technical conversation"
    assert conversation["messages"] == []
    assert conversation["created_at"] == conversation["updated_at"]


def test_create_conversation_accepts_a_custom_title():
    conversation = conversation_store.create_conversation("alice", "business", title="Q3 planning")

    assert conversation["title"] == "Q3 planning"


def test_get_conversation_returns_none_for_unknown_id():
    assert conversation_store.get_conversation("alice", "does-not-exist") is None


def test_get_conversation_returns_none_for_another_owners_conversation():
    conversation = conversation_store.create_conversation("alice", "technical")

    assert conversation_store.get_conversation("bob", conversation["id"]) is None
    assert conversation_store.get_conversation("alice", conversation["id"]) is not None


def test_list_conversations_returns_summaries_sorted_by_updated_at_desc():
    first = conversation_store.create_conversation("alice", "technical")
    second = conversation_store.create_conversation("alice", "business")
    conversation_store.append_message("alice", first["id"], "user", "hello", [])

    summaries = conversation_store.list_conversations("alice")

    assert [s["id"] for s in summaries] == [first["id"], second["id"]]
    assert "messages" not in summaries[0]


def test_list_conversations_is_scoped_per_owner():
    conversation_store.create_conversation("alice", "technical")
    conversation_store.create_conversation("bob", "technical")

    assert len(conversation_store.list_conversations("alice")) == 1
    assert len(conversation_store.list_conversations("bob")) == 1


def test_list_conversations_returns_empty_list_for_owner_with_none():
    assert conversation_store.list_conversations("nobody") == []


def test_append_message_updates_messages_and_updated_at():
    conversation = conversation_store.create_conversation("alice", "technical")

    updated = conversation_store.append_message("alice", conversation["id"], "user", "hi", [])

    assert len(updated["messages"]) == 1
    assert updated["messages"][0] == {
        "role": "user",
        "text": "hi",
        "sources": [],
        "created_at": updated["messages"][0]["created_at"],
    }
    assert updated["updated_at"] >= conversation["updated_at"]


def test_append_message_returns_none_for_unknown_conversation():
    assert conversation_store.append_message("alice", "does-not-exist", "user", "hi", []) is None


def test_delete_conversation_removes_it_and_returns_true():
    conversation = conversation_store.create_conversation("alice", "technical")

    assert conversation_store.delete_conversation("alice", conversation["id"]) is True
    assert conversation_store.get_conversation("alice", conversation["id"]) is None


def test_delete_conversation_returns_false_for_unknown_id():
    assert conversation_store.delete_conversation("alice", "does-not-exist") is False


def test_delete_conversation_cannot_delete_another_owners_conversation():
    conversation = conversation_store.create_conversation("alice", "technical")

    assert conversation_store.delete_conversation("bob", conversation["id"]) is False
    assert conversation_store.get_conversation("alice", conversation["id"]) is not None
