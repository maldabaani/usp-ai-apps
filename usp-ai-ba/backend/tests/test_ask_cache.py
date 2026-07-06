"""Covers api/ask_cache.py's exact-question-match answer cache and its
wiring into api/routers/ask.py's _ask() (Phase L-F): a hit skips retrieval
and the chat call entirely; a miss on any of {conversation context, prompt
template, ingestion generation} is treated as a genuinely different
question, never served stale; the empty-corpus fallback is never cached.
"""
from __future__ import annotations

import time

import jwt
import pytest
from fastapi.testclient import TestClient

import prompt_store
from api import ask_cache
from api.main import app
from api.routers import ask
from config import settings
from ingestion import ingestion_generation

client = TestClient(app, raise_server_exceptions=False)


def _token(username: str = "cache_test_user", role: str = "user") -> str:
    payload = {"sub": username, "role": role, "exp": time.time() + 3600}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_token()}"}


class _FakeChunk:
    def __init__(self, content):
        self.content = content


class _FakeChat:
    def __init__(self, chunks):
        self._chunks = chunks
        self.call_count = 0

    async def astream(self, messages):
        self.call_count += 1
        for chunk in self._chunks:
            yield _FakeChunk(chunk)


class _RaisingChat:
    async def astream(self, messages):
        raise AssertionError("chat client should not be called on a cache hit")
        yield  # pragma: no cover - unreachable, makes this an async generator


_SAMPLE_RETRIEVED = {
    "manuals": [{"content": "manual text", "metadata": {"source": "manual.pdf"}}],
    "codebase": [],
    "entities": [],
}


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    ask._ask_chat = None
    ask._ask_chat_generation = -1
    ask._ask_chat_model_kind = None
    monkeypatch.setattr(settings, "JOBS_DIR", str(tmp_path / "jobs"))
    prompt_store._cache = None
    ask_cache._cache.clear()
    ingestion_generation._generation = 0
    yield
    ask._ask_chat = None
    ask._ask_chat_generation = -1
    ask._ask_chat_model_kind = None
    prompt_store._cache = None
    ask_cache._cache.clear()
    ingestion_generation._generation = 0


def test_build_key_differs_when_any_ingredient_differs():
    base = ask_cache.build_key("technical", 0, "template", "", "question")

    assert base != ask_cache.build_key("business", 0, "template", "", "question")
    assert base != ask_cache.build_key("technical", 1, "template", "", "question")
    assert base != ask_cache.build_key("technical", 0, "different template", "", "question")
    assert base != ask_cache.build_key("technical", 0, "template", "some context", "question")
    assert base != ask_cache.build_key("technical", 0, "template", "", "different question")
    assert base == ask_cache.build_key("technical", 0, "template", "", "question")


def test_identical_question_is_served_from_cache_without_a_second_chat_call(monkeypatch):
    async def fake_retrieve(question, top_k=10):
        return _SAMPLE_RETRIEVED

    monkeypatch.setattr(ask, "retrieve_all_collections", fake_retrieve)
    fake_chat = _FakeChat(["cached ", "answer"])
    monkeypatch.setattr(ask, "_get_ask_chat", lambda: fake_chat)

    first = client.post("/api/ask/technical", json={"question": "repeat me"}, headers=_auth_headers())
    assert first.status_code == 200
    assert fake_chat.call_count == 1

    # A second, identical question must be served from cache -- swap in a
    # chat that raises if called at all, proving no retrieval/chat happened.
    monkeypatch.setattr(ask, "_get_ask_chat", lambda: _RaisingChat())
    second = client.post("/api/ask/technical", json={"question": "repeat me"}, headers=_auth_headers())

    assert second.status_code == 200
    assert 'event: chunk\ndata: "cached answer"' in second.text


def test_a_bumped_ingestion_generation_causes_a_cache_miss(monkeypatch):
    async def fake_retrieve(question, top_k=10):
        return _SAMPLE_RETRIEVED

    monkeypatch.setattr(ask, "retrieve_all_collections", fake_retrieve)
    monkeypatch.setattr(ask, "_get_ask_chat", lambda: _FakeChat(["first"]))
    client.post("/api/ask/technical", json={"question": "same question"}, headers=_auth_headers())

    ingestion_generation.bump()

    second_chat = _FakeChat(["second"])
    monkeypatch.setattr(ask, "_get_ask_chat", lambda: second_chat)
    client.post("/api/ask/technical", json={"question": "same question"}, headers=_auth_headers())

    assert second_chat.call_count == 1


def test_a_changed_custom_prompt_causes_a_cache_miss(monkeypatch):
    async def fake_retrieve(question, top_k=10):
        return _SAMPLE_RETRIEVED

    monkeypatch.setattr(ask, "retrieve_all_collections", fake_retrieve)
    monkeypatch.setattr(ask, "_get_ask_chat", lambda: _FakeChat(["first"]))
    client.post("/api/ask/technical", json={"question": "same question"}, headers=_auth_headers())

    prompt_store.save_custom_prompt("technical", "A brand new template.\n{context}\n")

    second_chat = _FakeChat(["second"])
    monkeypatch.setattr(ask, "_get_ask_chat", lambda: second_chat)
    client.post("/api/ask/technical", json={"question": "same question"}, headers=_auth_headers())

    assert second_chat.call_count == 1


def test_different_conversations_do_not_share_a_cache_entry(monkeypatch):
    from api import conversation_store

    async def fake_retrieve(question, top_k=10):
        return _SAMPLE_RETRIEVED

    monkeypatch.setattr(ask, "retrieve_all_collections", fake_retrieve)

    conversation_a = conversation_store.create_conversation("cache_test_user", "technical")
    conversation_store.append_message("cache_test_user", conversation_a["id"], "user", "context A", [])
    conversation_store.append_message("cache_test_user", conversation_a["id"], "assistant", "reply A", [])

    conversation_b = conversation_store.create_conversation("cache_test_user", "technical")
    conversation_store.append_message("cache_test_user", conversation_b["id"], "user", "context B", [])
    conversation_store.append_message("cache_test_user", conversation_b["id"], "assistant", "reply B", [])

    monkeypatch.setattr(ask, "_get_ask_chat", lambda: _FakeChat(["answer for A"]))
    client.post(
        "/api/ask/technical",
        json={"question": "same question", "conversation_id": conversation_a["id"]},
        headers=_auth_headers(),
    )

    chat_b = _FakeChat(["answer for B"])
    monkeypatch.setattr(ask, "_get_ask_chat", lambda: chat_b)
    client.post(
        "/api/ask/technical",
        json={"question": "same question", "conversation_id": conversation_b["id"]},
        headers=_auth_headers(),
    )

    assert chat_b.call_count == 1


def test_empty_corpus_fallback_is_never_cached(monkeypatch):
    async def fake_retrieve_empty(question, top_k=10):
        return {"manuals": [], "codebase": [], "entities": []}

    monkeypatch.setattr(ask, "retrieve_all_collections", fake_retrieve_empty)
    monkeypatch.setattr(ask, "_get_ask_chat", lambda: _RaisingChat())

    client.post("/api/ask/technical", json={"question": "anything?"}, headers=_auth_headers())

    # Corpus now has content -- if the empty-corpus fallback had wrongly been
    # cached under this same key, this second call would incorrectly replay
    # the "no content" message instead of calling the (now working) chat.
    async def fake_retrieve_nonempty(question, top_k=10):
        return _SAMPLE_RETRIEVED

    monkeypatch.setattr(ask, "retrieve_all_collections", fake_retrieve_nonempty)
    real_chat = _FakeChat(["a real answer"])
    monkeypatch.setattr(ask, "_get_ask_chat", lambda: real_chat)

    resp = client.post("/api/ask/technical", json={"question": "anything?"}, headers=_auth_headers())

    assert real_chat.call_count == 1
    assert "a real answer" in resp.text
