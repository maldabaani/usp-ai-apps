"""Server-Sent Events for run events, with replay from Postgres via Last-Event-ID."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable

from app.db.models import RunStatus
from app.events.bus import EventBus
from app.events.types import Event, EventType

KEEPALIVE_S = 15.0
RETRY_MS = 3000


def format_event(event: Event) -> str:
    data = json.dumps(
        {
            "id": event.id,
            "run_id": event.run_id,
            "type": event.type.value,
            "node": event.node,
            "task_id": event.task_id,
            "payload": event.payload,
            "created_at": event.created_at.isoformat(),
        },
        default=str,
    )
    return f"id: {event.id}\nevent: {event.type.value}\ndata: {data}\n\n"


def is_terminal_event(event: Event) -> bool:
    if event.type is not EventType.STATUS:
        return False
    try:
        return RunStatus(str(event.payload.get("status"))).is_terminal
    except ValueError:
        return False


async def event_stream(
    bus: EventBus,
    run_id: str,
    last_event_id: int | None,
    *,
    run_is_terminal: Callable[[], Awaitable[bool]],
    keepalive_s: float = KEEPALIVE_S,
) -> AsyncIterator[str]:
    """Replay everything after `last_event_id`, then stream live events.

    The stream ends after a terminal status event, or right after the replay when the run had
    already finished. Keepalive comments keep proxies from closing idle connections.
    """
    yield f"retry: {RETRY_MS}\n\n"
    if await run_is_terminal():  # finished run: replay what the client missed and close
        async for event in bus.replay(run_id, last_event_id or 0):
            yield format_event(event)
        return
    subscription = bus.subscribe(run_id, last_event_id)
    # One pending read survives keepalive timeouts: cancelling an async generator's in-flight
    # anext() would close the subscription for good.
    pending: asyncio.Future[Event] | None = None
    try:
        seen_terminal = False
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(subscription))
            done, _ = await asyncio.wait({pending}, timeout=keepalive_s)
            if not done:
                if seen_terminal or await run_is_terminal():
                    return
                yield ": keepalive\n\n"
                continue
            event = pending.result()
            pending = None
            yield format_event(event)
            if is_terminal_event(event):
                seen_terminal = True
                if await run_is_terminal():
                    return
    finally:
        if pending is not None:
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
        await subscription.aclose()
