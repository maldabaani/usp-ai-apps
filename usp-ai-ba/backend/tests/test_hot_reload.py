"""Covers the settings_generation hot-reload fix (Phase F2): a settings-screen
change to OLLAMA_LLM_MODEL/OLLAMA_BASE_URL/OLLAMA_EMBED_MODEL now actually
takes effect on the next call, instead of silently requiring a process
restart (the pre-existing bug this session's research found StoryForge had,
mirroring CodeMind's own honestly-documented RESTART_REQUIRED_FIELDS gap).
"""
from config import settings
from ingestion import chroma_client
from pipeline.nodes import clarify, generate


def test_generate_llm_rebuilds_only_when_generation_advances():
    first = generate._get_llm()
    second = generate._get_llm()
    assert first is second  # no settings change -- same cached client

    original_model = settings.OLLAMA_LLM_MODEL
    try:
        settings.apply_updates({"OLLAMA_LLM_MODEL": "a-different-test-model"})
        third = generate._get_llm()
        assert third is not first
        assert third.model == "a-different-test-model"
    finally:
        settings.apply_updates({"OLLAMA_LLM_MODEL": original_model})


def test_clarify_llm_rebuilds_only_when_generation_advances():
    first = clarify._get_llm()
    second = clarify._get_llm()
    assert first is second

    original_url = settings.OLLAMA_BASE_URL
    try:
        settings.apply_updates({"OLLAMA_BASE_URL": "http://test-host:11434"})
        third = clarify._get_llm()
        assert third is not first
        assert third.base_url == "http://test-host:11434"
    finally:
        settings.apply_updates({"OLLAMA_BASE_URL": original_url})


def test_generate_llm_rebuilds_when_num_ctx_changes():
    first = generate._get_llm()
    original_num_ctx = settings.OLLAMA_NUM_CTX
    try:
        settings.apply_updates({"OLLAMA_NUM_CTX": 4096})
        second = generate._get_llm()
        assert second is not first
        assert second.num_ctx == 4096
    finally:
        settings.apply_updates({"OLLAMA_NUM_CTX": original_num_ctx})


def test_clarify_llm_rebuilds_when_num_ctx_changes():
    first = clarify._get_llm()
    original_num_ctx = settings.OLLAMA_NUM_CTX
    try:
        settings.apply_updates({"OLLAMA_NUM_CTX": 4096})
        second = clarify._get_llm()
        assert second is not first
        assert second.num_ctx == 4096
    finally:
        settings.apply_updates({"OLLAMA_NUM_CTX": original_num_ctx})


def test_chroma_embeddings_rebuild_only_when_generation_advances():
    first = chroma_client.get_embeddings()
    second = chroma_client.get_embeddings()
    assert first is second

    original_model = settings.OLLAMA_EMBED_MODEL
    try:
        settings.apply_updates({"OLLAMA_EMBED_MODEL": "a-different-embed-model"})
        third = chroma_client.get_embeddings()
        assert third is not first
        assert third.model == "a-different-embed-model"
    finally:
        settings.apply_updates({"OLLAMA_EMBED_MODEL": original_model})


def test_chroma_embeddings_uses_configured_embed_num_ctx_not_hardcoded():
    first = chroma_client.get_embeddings()
    assert first.num_ctx == settings.OLLAMA_EMBED_NUM_CTX


def test_chroma_embeddings_rebuilds_when_embed_num_ctx_changes():
    first = chroma_client.get_embeddings()
    original_num_ctx = settings.OLLAMA_EMBED_NUM_CTX
    try:
        settings.apply_updates({"OLLAMA_EMBED_NUM_CTX": 4096})
        second = chroma_client.get_embeddings()
        assert second is not first
        assert second.num_ctx == 4096
    finally:
        settings.apply_updates({"OLLAMA_EMBED_NUM_CTX": original_num_ctx})


def test_chroma_vector_store_cache_is_dropped_on_settings_change(tmp_path):
    # Redirect the persistent client to a throwaway directory before the
    # first call in this test -- get_chroma_client()'s underlying
    # chromadb.PersistentClient is a true process-lifetime singleton this
    # session's fix doesn't touch, so whichever path is set the first time
    # it's constructed is what it keeps; pointing it at tmp_path here avoids
    # ever creating a real ./chroma_db directory as a side effect of running
    # this test suite.
    original_path = settings.CHROMA_PERSIST_PATH
    try:
        settings.apply_updates({"CHROMA_PERSIST_PATH": str(tmp_path)})
        first = chroma_client.get_vector_store("manuals")
        second = chroma_client.get_vector_store("manuals")
        assert first is second  # cached within the same generation

        settings.apply_updates({"OLLAMA_EMBED_MODEL": "yet-another-embed-model"})
        third = chroma_client.get_vector_store("manuals")
        assert third is not first  # whole cache dropped, not reused
    finally:
        settings.apply_updates({"CHROMA_PERSIST_PATH": original_path})
