"""check_chroma() in isolation, outside test_health.py's module-wide
autouse _stub_infra fixture (which replaces health.check_chroma itself with
a stub for every test in that file -- exactly wrong for testing this
function's own real behavior)."""
from __future__ import annotations

import pytest

from devcrew import health


async def test_check_chroma_uses_the_embedded_client_not_a_remote_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test: check_chroma used to ping chromadb.HttpClient (a
    remote Chroma server) even though the merged app's real RAG store is
    ingestion/chroma_client.py's embedded, on-disk PersistentClient -- a
    critical=True startup check that could fail (or falsely pass) against a
    service nothing in the merged app actually talks to."""
    import ingestion.chroma_client as chroma_client

    calls: list[str] = []

    class _FakeClient:
        def heartbeat(self) -> int:
            calls.append("heartbeat")
            return 12345

    monkeypatch.setattr(chroma_client, "get_chroma_client", lambda: _FakeClient())

    detail = await health.check_chroma()

    assert calls == ["heartbeat"]
    assert "embedded" in detail
