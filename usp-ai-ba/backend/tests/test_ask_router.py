"""Covers api/routers/ask.py -- the standing Ask Technical/Business
endpoints introduced to replace CodeMind's retired per-job Ask feature,
querying the shared ingestion corpus (ingestion/retrieval.py) directly."""
import time

import jwt
import pytest
from fastapi.testclient import TestClient

import prompt_store
from api import ask_cache, conversation_store
from api.main import app
from api.routers import ask
from config import settings
from ingestion import ingestion_generation
from prompts.ask_prompts import BUSINESS_ASK_SYSTEM_PROMPT, TECHNICAL_ASK_SYSTEM_PROMPT, _GROUNDING_RULES

client = TestClient(app, raise_server_exceptions=False)


def _token(username: str = "ask_test_user", role: str = "user") -> str:
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
        self.calls = []

    async def astream(self, messages):
        self.calls.append(messages)
        for chunk in self._chunks:
            yield _FakeChunk(chunk)


class _RaisingChat:
    async def astream(self, messages):
        raise AssertionError("chat client should not be called when the corpus is empty")
        yield  # pragma: no cover - unreachable, makes this an async generator


_SAMPLE_RETRIEVED = {
    "manuals": [{"content": "manual text", "metadata": {"source": "manual.pdf"}}],
    "codebase": [{"content": "code text", "metadata": {"source": "src/Auth.java", "type": "java_class"}}],
    "entities": [],
}


@pytest.fixture(autouse=True)
def _reset_ask_chat_cache(tmp_path, monkeypatch):
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


def test_ask_technical_returns_sse_sources_and_chunks(monkeypatch):
    async def fake_retrieve(question, top_k=10):
        assert question == "how does auth work?"
        return _SAMPLE_RETRIEVED

    fake_chat = _FakeChat(["It ", "authenticates ", "users."])
    monkeypatch.setattr(ask, "retrieve_all_collections", fake_retrieve)
    monkeypatch.setattr(ask, "_get_ask_chat", lambda: fake_chat)

    resp = client.post(
        "/api/ask/technical",
        json={"question": "how does auth work?"},
        headers=_auth_headers(),
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert 'event: sources\ndata: ["manual.pdf", "src/Auth.java"]' in resp.text
    assert 'event: chunk\ndata: "It "' in resp.text
    assert 'event: chunk\ndata: "authenticates "' in resp.text
    assert 'event: chunk\ndata: "users."' in resp.text


def test_ask_business_returns_sse_sources_and_chunks(monkeypatch):
    async def fake_retrieve(question, top_k=10):
        return _SAMPLE_RETRIEVED

    fake_chat = _FakeChat(["Handles logins."])
    monkeypatch.setattr(ask, "retrieve_all_collections", fake_retrieve)
    monkeypatch.setattr(ask, "_get_ask_chat", lambda: fake_chat)

    resp = client.post(
        "/api/ask/business",
        json={"question": "what does the login feature do?"},
        headers=_auth_headers(),
    )

    assert resp.status_code == 200
    assert 'event: chunk\ndata: "Handles logins."' in resp.text


def test_ask_technical_and_business_use_different_system_prompts(monkeypatch):
    async def fake_retrieve(question, top_k=10):
        return _SAMPLE_RETRIEVED

    monkeypatch.setattr(ask, "retrieve_all_collections", fake_retrieve)

    technical_chat = _FakeChat(["answer"])
    monkeypatch.setattr(ask, "_get_ask_chat", lambda: technical_chat)
    client.post("/api/ask/technical", json={"question": "q"}, headers=_auth_headers())
    technical_system_prompt = technical_chat.calls[0][0].content

    business_chat = _FakeChat(["answer"])
    monkeypatch.setattr(ask, "_get_ask_chat", lambda: business_chat)
    client.post("/api/ask/business", json={"question": "q"}, headers=_auth_headers())
    business_system_prompt = business_chat.calls[0][0].content

    assert technical_system_prompt != business_system_prompt
    assert technical_system_prompt == TECHNICAL_ASK_SYSTEM_PROMPT.format(
        context=ask._build_context(_SAMPLE_RETRIEVED)
    )
    assert business_system_prompt == BUSINESS_ASK_SYSTEM_PROMPT.format(
        context=ask._build_context(_SAMPLE_RETRIEVED)
    )
    # Both share the same grounding-rule discipline verbatim (ported from
    # codemind/qa.py), differing only in framing around it.
    assert _GROUNDING_RULES in technical_system_prompt
    assert _GROUNDING_RULES in business_system_prompt


def test_ask_technical_uses_a_saved_custom_prompt_with_no_restart(monkeypatch):
    async def fake_retrieve(question, top_k=10):
        return _SAMPLE_RETRIEVED

    monkeypatch.setattr(ask, "retrieve_all_collections", fake_retrieve)
    prompt_store.save_custom_prompt("technical", "Custom override.\n{context}\n")

    fake_chat = _FakeChat(["answer"])
    monkeypatch.setattr(ask, "_get_ask_chat", lambda: fake_chat)

    resp = client.post("/api/ask/technical", json={"question": "q"}, headers=_auth_headers())

    assert resp.status_code == 200
    sent_system_prompt = fake_chat.calls[0][0].content
    assert sent_system_prompt == "Custom override.\n{context}\n".format(context=ask._build_context(_SAMPLE_RETRIEVED))
    assert sent_system_prompt != TECHNICAL_ASK_SYSTEM_PROMPT.format(context=ask._build_context(_SAMPLE_RETRIEVED))


def test_ask_rejects_blank_question():
    resp = client.post("/api/ask/technical", json={"question": ""}, headers=_auth_headers())
    assert 400 <= resp.status_code < 500


def test_ask_returns_no_results_message_without_calling_chat_when_corpus_empty(monkeypatch):
    async def fake_retrieve(question, top_k=10):
        return {"manuals": [], "codebase": [], "entities": []}

    monkeypatch.setattr(ask, "retrieve_all_collections", fake_retrieve)
    monkeypatch.setattr(ask, "_get_ask_chat", lambda: _RaisingChat())

    resp = client.post("/api/ask/technical", json={"question": "anything?"}, headers=_auth_headers())

    assert resp.status_code == 200
    assert 'event: sources\ndata: []' in resp.text
    assert "No content has been ingested yet" in resp.text


def test_ask_requires_auth():
    resp = client.post("/api/ask/technical", json={"question": "anything?"})
    assert resp.status_code == 401


def test_ask_status_reports_counts(monkeypatch):
    monkeypatch.setattr(ask, "collection_counts", lambda: {"manuals": 3, "codebase": 12, "entities": 0})

    resp = client.get("/api/ask/status", headers=_auth_headers())

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"counts": {"manuals": 3, "codebase": 12, "entities": 0}, "has_content": True}


def test_ask_status_reports_no_content_for_empty_corpus(monkeypatch):
    monkeypatch.setattr(ask, "collection_counts", lambda: {"manuals": 0, "codebase": 0, "entities": 0})

    resp = client.get("/api/ask/status", headers=_auth_headers())

    assert resp.json()["has_content"] is False


# -- Conversation memory (Phase L-E) ----------------------------------------


def test_ask_without_conversation_id_auto_creates_one_and_returns_its_header(monkeypatch):
    async def fake_retrieve(question, top_k=10):
        return _SAMPLE_RETRIEVED

    monkeypatch.setattr(ask, "retrieve_all_collections", fake_retrieve)
    monkeypatch.setattr(ask, "_get_ask_chat", lambda: _FakeChat(["answer"]))

    resp = client.post("/api/ask/technical", json={"question": "q"}, headers=_auth_headers())

    assert resp.status_code == 200
    conversation_id = resp.headers["X-Conversation-Id"]
    conversation = conversation_store.get_conversation("ask_test_user", conversation_id)
    assert conversation is not None
    assert conversation["kind"] == "technical"


def test_ask_persists_the_question_and_full_assistant_answer(monkeypatch):
    async def fake_retrieve(question, top_k=10):
        return _SAMPLE_RETRIEVED

    monkeypatch.setattr(ask, "retrieve_all_collections", fake_retrieve)
    monkeypatch.setattr(ask, "_get_ask_chat", lambda: _FakeChat(["It ", "works."]))

    resp = client.post("/api/ask/technical", json={"question": "how does it work?"}, headers=_auth_headers())

    conversation_id = resp.headers["X-Conversation-Id"]
    conversation = conversation_store.get_conversation("ask_test_user", conversation_id)
    assert conversation["messages"][0] == {
        "role": "user",
        "text": "how does it work?",
        "sources": [],
        "created_at": conversation["messages"][0]["created_at"],
    }
    assert conversation["messages"][1]["role"] == "assistant"
    assert conversation["messages"][1]["text"] == "It works."
    assert conversation["messages"][1]["sources"] == ["manual.pdf", "src/Auth.java"]


def test_ask_with_existing_conversation_id_folds_prior_turns_into_the_message_list(monkeypatch):
    async def fake_retrieve(question, top_k=10):
        return _SAMPLE_RETRIEVED

    monkeypatch.setattr(ask, "retrieve_all_collections", fake_retrieve)

    conversation = conversation_store.create_conversation("ask_test_user", "technical")
    conversation_store.append_message("ask_test_user", conversation["id"], "user", "first question", [])
    conversation_store.append_message("ask_test_user", conversation["id"], "assistant", "first answer", [])

    fake_chat = _FakeChat(["second answer"])
    monkeypatch.setattr(ask, "_get_ask_chat", lambda: fake_chat)

    resp = client.post(
        "/api/ask/technical",
        json={"question": "follow-up question", "conversation_id": conversation["id"]},
        headers=_auth_headers(),
    )

    assert resp.status_code == 200
    assert resp.headers["X-Conversation-Id"] == conversation["id"]
    sent_messages = fake_chat.calls[0]
    # [SystemMessage, HumanMessage("first question"), AIMessage("first answer"), HumanMessage("follow-up question")]
    assert [m.content for m in sent_messages[1:]] == ["first question", "first answer", "follow-up question"]


def test_ask_trims_old_turns_once_the_history_char_budget_is_exceeded(monkeypatch):
    async def fake_retrieve(question, top_k=10):
        return _SAMPLE_RETRIEVED

    monkeypatch.setattr(ask, "retrieve_all_collections", fake_retrieve)
    monkeypatch.setattr(settings, "CONVERSATION_HISTORY_CHAR_BUDGET", 10)

    conversation = conversation_store.create_conversation("ask_test_user", "technical")
    conversation_store.append_message("ask_test_user", conversation["id"], "user", "a" * 20, [])
    conversation_store.append_message("ask_test_user", conversation["id"], "assistant", "b" * 20, [])
    conversation_store.append_message("ask_test_user", conversation["id"], "user", "recent", [])

    fake_chat = _FakeChat(["answer"])
    monkeypatch.setattr(ask, "_get_ask_chat", lambda: fake_chat)

    client.post(
        "/api/ask/technical",
        json={"question": "q", "conversation_id": conversation["id"]},
        headers=_auth_headers(),
    )

    sent_messages = fake_chat.calls[0]
    # Only the most recent prior turn ("recent") survives the tiny budget --
    # the older, larger "a"*20/"b"*20 turns are trimmed.
    assert [m.content for m in sent_messages[1:]] == ["recent", "q"]


def test_ask_with_unknown_conversation_id_returns_404():
    resp = client.post(
        "/api/ask/technical", json={"question": "q", "conversation_id": "does-not-exist"}, headers=_auth_headers()
    )

    assert resp.status_code == 404


def test_ask_with_no_results_still_records_the_turn_and_conversation_id(monkeypatch):
    async def fake_retrieve(question, top_k=10):
        return {"manuals": [], "codebase": [], "entities": []}

    monkeypatch.setattr(ask, "retrieve_all_collections", fake_retrieve)
    monkeypatch.setattr(ask, "_get_ask_chat", lambda: _RaisingChat())

    resp = client.post("/api/ask/technical", json={"question": "anything?"}, headers=_auth_headers())

    assert resp.status_code == 200
    conversation_id = resp.headers["X-Conversation-Id"]
    conversation = conversation_store.get_conversation("ask_test_user", conversation_id)
    assert conversation["messages"][1]["text"] == ask._NO_RESULTS_MESSAGE
