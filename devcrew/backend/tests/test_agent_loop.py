from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

from app.llm.agent import ELIDED, compact_messages, run_agent
from app.llm.models_config import Role
from app.llm.tokens import messages_tokens
from app.tools.base import ToolError, ToolSpec
from app.tools.human import ask_human_tool
from tests.fakes import Brain, Call, final, gateway, tool_call, tool_results

MESSAGES = [SystemMessage(content="# Role: Developer\n"), HumanMessage(content="task")]


class EchoArgs(BaseModel):
    text: str


async def _echo(args: EchoArgs) -> str:
    if args.text == "boom":
        raise ToolError("boom!")
    return f"echo:{args.text}"


ECHO = ToolSpec("echo", "Echo text", EchoArgs, _echo)


def brain(fn: object) -> Brain:
    return Brain(responders={"developer": fn})  # type: ignore[dict-item]


async def test_executes_tools_until_final_answer() -> None:
    events: list[tuple[str, dict[str, object]]] = []

    def dev(call: Call) -> AIMessage:
        results = tool_results(call)
        if len(results) < 2:
            return tool_call("echo", text="boom" if not results else "hi")
        return final(f"done: {[r.content for r in results]}")

    async def hook(kind: str, payload: dict[str, object]) -> None:
        events.append((kind, payload))

    out = await run_agent(
        gateway(brain(dev)), Role.DEVELOPER, MESSAGES, [ECHO], max_steps=5, on_tool_event=hook
    )
    assert out.kind == "final" and out.steps == 3 and out.tool_calls == 2
    assert out.final_text == "done: ['ERROR: boom!', 'echo:hi']"
    assert [k for k, _ in events] == ["tool_call", "tool_result", "tool_call", "tool_result"]
    assert events[1][1]["ok"] is False and events[3][1]["ok"] is True


async def test_malformed_call_gets_one_corrective_retry() -> None:
    replies = iter([tool_call("echo", wrong="x"), tool_call("echo", text="ok"), final("fine")])
    b = brain(lambda call: next(replies))
    out = await run_agent(gateway(b), Role.DEVELOPER, MESSAGES, [ECHO], max_steps=6)
    assert out.kind == "final" and out.final_text == "fine"
    correction = b.calls[1].messages[-1]
    assert isinstance(correction, HumanMessage) and "malformed" in str(correction.content)
    assert "text: Field required" in str(correction.content)
    # The bad call's id still gets a ToolMessage so the transcript stays well-formed.
    assert isinstance(b.calls[1].messages[-2], ToolMessage)


async def test_second_malformed_call_is_an_error() -> None:
    def dev(call: Call) -> AIMessage:
        return tool_call("nonexistent_tool", text="x")

    b = brain(dev)
    out = await run_agent(gateway(b), Role.DEVELOPER, MESSAGES, [ECHO], max_steps=6)
    assert out.kind == "error" and "unknown tool 'nonexistent_tool'" in out.error
    assert len(b.calls) == 2


async def test_invalid_json_tool_call_detected() -> None:
    def dev(call: Call) -> AIMessage:
        return AIMessage(
            content="",
            invalid_tool_calls=[
                {"name": "echo", "args": "{", "id": "1", "error": None, "type": "invalid_tool_call"}
            ],
        )

    out = await run_agent(gateway(brain(dev)), Role.DEVELOPER, MESSAGES, [ECHO], max_steps=6)
    assert out.kind == "error" and "not valid JSON" in out.error


async def test_ask_human_pauses_with_transcript() -> None:
    def dev(call: Call) -> AIMessage:
        return tool_call("ask_human", question="Which database?")

    out = await run_agent(
        gateway(brain(dev)), Role.DEVELOPER, MESSAGES, [ECHO, ask_human_tool()], max_steps=6
    )
    assert out.kind == "ask_human" and out.question == "Which database?"
    assert isinstance(out.messages[-1], AIMessage) and out.tool_call_id


async def test_step_limit() -> None:
    out = await run_agent(
        gateway(brain(lambda c: tool_call("echo", text="again"))),
        Role.DEVELOPER,
        MESSAGES,
        [ECHO],
        max_steps=3,
    )
    assert out.kind == "error" and "step limit" in out.error


def test_compaction_elides_old_tool_output_first() -> None:
    big = "x" * 3000
    msgs = [SystemMessage(content="s"), HumanMessage(content="ctx")]
    for i in range(5):
        msgs += [
            AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": f"c{i}"}]),
            ToolMessage(content=big, tool_call_id=f"c{i}"),
        ]
    compact_messages(msgs, budget=2500)
    assert messages_tokens(msgs) <= 2500
    tool_msgs = [m for m in msgs if isinstance(m, ToolMessage)]
    assert tool_msgs[0].content == ELIDED and tool_msgs[-1].content == big
    assert msgs[1].content == "ctx"


def test_compaction_truncates_when_eliding_is_not_enough() -> None:
    msgs = [SystemMessage(content="s"), HumanMessage(content="y\n" * 6000)]
    compact_messages(msgs, budget=1000)
    assert messages_tokens(msgs) <= 1000 and "[truncated]" in str(msgs[1].content)


async def test_a_repeated_identical_call_withdraws_the_tool() -> None:
    """The real-run failure: after the question limit a small model asked the same question 27
    times. Two identical calls with identical results in a row withdraw the tool."""

    def dev(call: Call) -> AIMessage:
        if call.tool_names and "ask_human" in call.tool_names:
            return tool_call("ask_human", question="Which template?")
        return final("decided: python-fastapi")

    b = brain(dev)
    tools = [ask_human_tool(limit_reached=True), ECHO]
    out = await run_agent(gateway(b), Role.DEVELOPER, MESSAGES, tools, max_steps=30)
    assert out.kind == "final" and out.final_text == "decided: python-fastapi"
    assert len(b.calls) == 3  # two refused asks, then the tool is gone
    assert b.calls[2].tool_names == ("echo",)
    note = b.calls[2].messages[-1]
    assert isinstance(note, HumanMessage) and "no longer available" in str(note.content)


async def test_repeating_a_tool_after_other_work_is_fine() -> None:
    script = iter(
        [
            tool_call("echo", text="t"),
            tool_call("echo", text="x"),
            tool_call("echo", text="t"),
            tool_call("echo", text="t"),
            final("ok"),
        ]
    )
    b = brain(lambda call: next(script))
    out = await run_agent(gateway(b), Role.DEVELOPER, MESSAGES, [ECHO], max_steps=10)
    assert out.kind == "final" and out.tool_calls == 4
    # the last two calls were identical and consecutive: echo is withdrawn before the answer
    assert b.calls[-1].tool_names == ()


async def test_a_tool_call_cut_off_by_the_output_limit_is_retried_not_fatal() -> None:
    """Real run: Ollama answered 500 'invalid tool call arguments' when the answer hit
    num_predict inside write_file, and the whole run failed."""
    cut = RuntimeError(
        'llama-server returned invalid tool call arguments for "write_file": unexpected end of '
        "JSON input (status code: 500)"
    )
    replies: list[object] = [cut, tool_call("echo", text="small"), final("done")]

    def dev(call: Call) -> AIMessage:
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply  # type: ignore[return-value]

    b = brain(dev)
    out = await run_agent(gateway(b), Role.DEVELOPER, MESSAGES, [ECHO], max_steps=6)
    assert out.kind == "final" and out.final_text == "done"
    assert "cut off at the output limit" in str(b.calls[1].messages[-1].content)

    replies[:] = [cut, cut]
    out = await run_agent(gateway(brain(dev)), Role.DEVELOPER, MESSAGES, [ECHO], max_steps=6)
    assert out.kind == "error" and "cut off" in (out.error or "")


async def test_other_model_errors_still_raise() -> None:
    def dev(call: Call) -> AIMessage:
        raise ConnectionError("ollama is down")

    try:
        await run_agent(gateway(brain(dev)), Role.DEVELOPER, MESSAGES, [ECHO], max_steps=3)
    except ConnectionError:
        return
    raise AssertionError("expected ConnectionError")
