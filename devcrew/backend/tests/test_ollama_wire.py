"""Exercise the real ChatOllama client against a fake Ollama HTTP server (no real Ollama)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.llm.client import LLMGateway
from app.llm.models_config import ModelsConfig, Role
from app.llm.structured import generate_structured
from app.tools.human import ask_human_tool


class Answer(BaseModel):
    value: int


def fake_ollama(requests: list[dict[str, Any]]) -> FastAPI:
    app = FastAPI()

    @app.post("/api/chat")
    async def chat(request: Request) -> StreamingResponse:
        body = await request.json()
        requests.append(body)
        message: dict[str, Any] = {"role": "assistant", "content": ""}
        if body.get("tools"):
            message["tool_calls"] = [
                {"function": {"name": "ask_human", "arguments": {"question": "Which DB?"}}}
            ]
        else:
            message["content"] = json.dumps({"value": 42})
        chunk = {
            "model": body["model"],
            "created_at": "2026-01-01T00:00:00Z",
            "message": message,
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 11,
            "eval_count": 7,
        }

        async def stream() -> AsyncIterator[bytes]:
            yield (json.dumps(chunk) + "\n").encode()

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    return app


@pytest.fixture
async def ollama_url() -> AsyncIterator[tuple[str, list[dict[str, Any]]]]:
    requests: list[dict[str, Any]] = []
    config = uvicorn.Config(fake_ollama(requests), host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    async with asyncio.timeout(10):
        while not server.started:  # noqa: ASYNC110 - uvicorn exposes only a flag
            await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}", requests
    server.should_exit = True
    await task


async def test_real_chat_ollama_roundtrip(ollama_url: tuple[str, list[dict[str, Any]]]) -> None:
    url, requests = ollama_url
    models = ModelsConfig.model_validate(
        {
            "defaults": {"model": "qwen2.5-coder:14b", "num_ctx": 8192, "num_predict": 1024},
            "roles": {"planner": {"temperature": 0.3}},
        }
    )
    gw = LLMGateway(models, base_url=url, max_parallel=1)
    msgs = [SystemMessage(content="# Role: Planner"), HumanMessage(content="hi")]

    ai = await gw.ainvoke(Role.PLANNER, msgs, tools=[ask_human_tool().schema()])
    assert ai.tool_calls[0]["name"] == "ask_human"
    assert ai.tool_calls[0]["args"] == {"question": "Which DB?"}
    assert ai.tool_calls[0]["id"]
    sent = requests[0]
    assert sent["model"] == "qwen2.5-coder:14b"
    assert sent["options"]["num_ctx"] == 8192 and sent["options"]["temperature"] == 0.3
    assert sent["options"]["num_predict"] == 1024
    assert sent["tools"][0]["function"]["name"] == "ask_human"

    result = await generate_structured(gw, Role.PLANNER, msgs, Answer)
    assert result.value.value == 42
    assert requests[1]["format"]["properties"]["value"]["type"] == "integer"
    assert gw.usage.by_role[Role.PLANNER].input_tokens == 22
    assert gw.usage.by_role[Role.PLANNER].output_tokens == 14
