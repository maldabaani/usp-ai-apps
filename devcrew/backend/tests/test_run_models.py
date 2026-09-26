"""Per-run model choice (Phase 16, BL-112): the picker's choices, validation and routing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.api_harness import Api, api

REQUEST = {"request": "Build a TODO API with CRUD", "repo_target": "me/todo", "create_repo": False}


def installed(a: Api, *names: str) -> None:
    async def lister() -> set[str]:
        return set(names)

    a.container.llm.model_lister = lister


async def usage_models(a: Api, run_id: str) -> dict[str, list[str]]:
    usage = (await a.client.get(f"/runs/{run_id}/usage")).json()
    return {line["key"]: line["models"] for line in usage["by_role"]}


async def test_models_lists_role_defaults_and_installed_models(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        body = (await a.client.get("/models")).json()
        assert body["installed"] is None  # unknown: the fake gateway has no Ollama
        assert {r["role"]: r["model"] for r in body["roles"]}["developer"] == "fake"
        installed(a, "fake", "qwen-big:32b")
        body = (await a.client.get("/models")).json()
        assert body["installed"] == ["fake", "qwen-big:32b"]


async def test_a_run_uses_its_own_model_for_the_chosen_roles(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        installed(a, "fake", "qwen-big:32b")
        body: dict[str, Any] = {
            **REQUEST,
            "models": {"developer": "qwen-big:32b", "planner": "fake", "qa": " "},
        }
        resp = await a.client.post("/runs", json=body)
        assert resp.status_code == 201, resp.text
        run_id = resp.json()["id"]
        run = await a.settle(run_id)
        # defaults and blanks are dropped: only real overrides are kept
        assert run["models"] == {"developer": "qwen-big:32b"}
        await a.approve(run_id)
        await a.approve(run_id)  # tasks run in the parallel task workers
        used = await usage_models(a, run_id)
        assert used["developer"] == ["qwen-big:32b"]
        assert used["planner"] == ["fake"] and used["architect"] == ["fake"]


async def test_unknown_models_and_roles_are_refused(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        installed(a, "fake")
        resp = await a.client.post("/runs", json={**REQUEST, "models": {"developer": "nope:1b"}})
        assert resp.status_code == 422
        assert "not installed in Ollama: nope:1b" in resp.json()["detail"]
        resp = await a.client.post("/runs", json={**REQUEST, "models": {"designer": "fake"}})
        assert resp.status_code == 422


async def test_any_model_name_is_accepted_when_ollama_cannot_be_asked(tmp_path: Path) -> None:
    async with api(tmp_path) as a:

        async def down() -> set[str]:
            raise OSError("connection refused")

        a.container.llm.model_lister = down
        resp = await a.client.post("/runs", json={**REQUEST, "models": {"reviewer": "other:7b"}})
        assert resp.status_code == 201, resp.text
        run = await a.settle(resp.json()["id"])
        assert run["models"] == {"reviewer": "other:7b"}
