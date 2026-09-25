from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from app.config import DEVCREW_DIR, Settings
from app.rag.embeddings import Embedder
from app.rag.index import CodeIndex, indexable, matches_filter
from app.rag.service import RagService, chroma_http_client
from app.tools.catalog import RulesCatalog
from app.tools.git import GitRepo
from tests.fakes import FakeChroma, HashEmbeddingModel

ROUTER = b"def list_todos():\n    return []\n\n\ndef create_todo(title: str):\n    return title\n"
CONFTEST = b"import pytest\n\n\n@pytest.fixture\ndef client():\n    return 'client'\n"


def make_index() -> tuple[CodeIndex, FakeChroma, HashEmbeddingModel]:
    chroma, model = FakeChroma(), HashEmbeddingModel()
    return CodeIndex(chroma, Embedder(model, model_name="nomic-embed-text"), "r1"), chroma, model


async def test_incremental_sync_by_file_hash() -> None:
    index, chroma, model = make_index()
    stats = await index.sync(
        {"app/todos.py": ROUTER, "tests/conftest.py": CONFTEST, "empty.py": b""}, commit_sha="a1"
    )
    assert stats.added == ["app/todos.py", "tests/conftest.py"] and stats.chunks == 4
    embedded = model.documents_embedded

    stats = await index.sync(
        {"app/todos.py": ROUTER, "tests/conftest.py": CONFTEST}, commit_sha="a2"
    )
    assert (stats.unchanged, stats.chunks, model.documents_embedded) == (2, 0, embedded)

    changed = ROUTER + b"\n\ndef delete_todo(todo_id: int):\n    return None\n"
    stats = await index.sync({"app/todos.py": changed}, commit_sha="a3")
    assert stats.updated == ["app/todos.py"] and stats.removed == ["tests/conftest.py"]
    rows = chroma.collections["run_r1"].rows.values()
    assert {m["path"] for _, _, m in rows} == {"app/todos.py"}
    assert {m["commit_sha"] for _, _, m in rows} == {"a3"}
    assert sorted(m["symbol"] for _, _, m in rows) == [
        "def create_todo",
        "def delete_todo",
        "def list_todos",
    ]


async def test_sources_are_synced_independently() -> None:
    index, chroma, _ = make_index()
    await index.sync(
        {"rules/python.md": b"# Rules\n**PY-001** hints\n"}, commit_sha="r", source="rules"
    )
    await index.sync(
        {"rules/python.md": b"# project doc that happens to share the path\n"}, commit_sha="w"
    )
    await index.sync({}, commit_sha="w2")  # workspace emptied: rules must survive
    rows = list(chroma.collections["run_r1"].rows.values())
    assert [(m["source"], m["path"]) for _, _, m in rows] == [("rules", "rules/python.md")]


async def test_search_ranks_and_filters() -> None:
    index, _, _ = make_index()
    await index.sync(
        {
            "app/todos.py": ROUTER,
            "tests/conftest.py": CONFTEST,
            "web/todo.spec.ts": b"describe('todo', () => {\n  it('works', () => {});\n});\n",
        },
        commit_sha="s",
    )
    hits = await index.search("create todo title", k=2)
    assert hits[0].path == "app/todos.py" and hits[0].symbol == "def create_todo"
    assert (hits[0].start_line, hits[0].end_line) == (5, 6)
    assert 0 < hits[0].score <= 1
    assert {h.path for h in await index.search("fixture client", 5, "tests/")} == {
        "tests/conftest.py"
    }
    assert {h.path for h in await index.search("todo", 5, "*.spec.ts")} == {"web/todo.spec.ts"}
    assert "--- app/todos.py:5-6  (def create_todo)" in hits[0].render()


async def test_empty_index_and_delete() -> None:
    index, chroma, _ = make_index()
    assert await index.search("anything") == []
    await index.delete()
    assert chroma.collections == {}
    await index.delete()  # idempotent


def test_indexable_rules() -> None:
    assert indexable("app/a.py", b"x = 1", 1000)
    assert not indexable("package-lock.json", b"{}", 1000)
    assert not indexable("logo.png", b"x", 1000)
    assert not indexable("big.py", b"x" * 2000, 1000)
    assert not indexable("bin.dat", b"ab\x00cd", 1000)
    assert not indexable("empty.py", b"  \n", 1000)


def test_path_filter() -> None:
    assert matches_filter("app/routers/todos.py", "app/")
    assert matches_filter("app/routers/todos.py", "./app")
    assert not matches_filter("apps/x.py", "app")
    assert matches_filter("src/app/a.spec.ts", "*.spec.ts")
    assert matches_filter("anything", None)


async def test_nomic_prefixes_and_batching() -> None:
    seen: list[list[str]] = []

    class Model(HashEmbeddingModel):
        async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
            seen.append(texts)
            return await super().aembed_documents(texts)

    emb = Embedder(Model(), model_name="nomic-embed-text", batch_size=2)
    await emb.embed_documents(["a", "b", "c"])
    assert seen == [["search_document: a", "search_document: b"], ["search_document: c"]]
    plain = Embedder(Model(), model_name="mxbai-embed-large")
    seen.clear()
    await plain.embed_documents(["a"])
    assert seen == [["a"]]


# --------------------------------------------------------------------------------------------
# Real ChromaDB server (opt-in): CHROMA_TEST=1 with Chroma on CHROMA_HOST:CHROMA_PORT.
# --------------------------------------------------------------------------------------------
@pytest.mark.skipif(os.environ.get("CHROMA_TEST") != "1", reason="set CHROMA_TEST=1")
async def test_real_chroma_workspace_rules_and_cleanup(tmp_path: Path) -> None:
    import chromadb

    settings = Settings(_env_file=None)
    rag = RagService(
        chroma_http_client(settings),
        Embedder(HashEmbeddingModel(), model_name="nomic-embed-text"),
        settings,
    )
    run_id = uuid.uuid4().hex
    ws = tmp_path / "repo"
    (ws / "app").mkdir(parents=True)
    (ws / "app" / "todos.py").write_bytes(ROUTER)
    (ws / "untracked.py").write_text("SECRET = 1\n")
    repo = GitRepo(ws)
    await repo.init()
    await repo.run("add", "app")
    await repo.run("commit", "-qm", "init")
    try:
        stats = await rag.sync_workspace(run_id, ws)
        assert stats.added == ["app/todos.py"]  # only tracked files
        await rag.sync_rules(run_id, RulesCatalog(DEVCREW_DIR / "rules"), ["python"])
        hits = await rag.search(run_id, "create todo title", 3)
        assert hits[0].path == "app/todos.py"
        rules_hits = await rag.search(run_id, "status codes 201 404", 5, "rules/")
        assert rules_hits and rules_hits[0].source == "rules"
    finally:
        await rag.delete(run_id)
    client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
    assert f"run_{run_id}" not in [c.name for c in client.list_collections()]
