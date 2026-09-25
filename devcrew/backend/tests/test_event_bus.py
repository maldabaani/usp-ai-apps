from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from app.events.bus import EventBus
from app.events.store import InMemoryEventStore
from app.events.types import Event, EventType


async def _take(agen: AsyncGenerator[Event], n: int, limit_s: float = 1.0) -> list[Event]:
    out: list[Event] = []
    async with asyncio.timeout(limit_s):
        async for event in agen:
            out.append(event)
            if len(out) == n:
                break
    return out


async def test_publish_persists_before_subscribers() -> None:
    store = InMemoryEventStore()
    bus = EventBus(store)
    event = await bus.publish("r1", EventType.NODE_STARTED, node="planner")
    assert event.id == 1
    assert await store.list_after("r1", 0) == [event]


async def test_replay_after_last_event_id_then_live() -> None:
    bus = EventBus(InMemoryEventStore())
    for i in range(5):
        await bus.publish("r1", EventType.TOOL_CALL, payload={"i": i})
    await bus.publish("other", EventType.ERROR)

    sub = bus.subscribe("r1", last_event_id=3)
    replayed = await _take(sub, 2)
    assert [e.payload["i"] for e in replayed] == [3, 4]

    live = asyncio.create_task(_take(sub, 1))
    await asyncio.sleep(0)
    await bus.publish("r1", EventType.MERGE, payload={"i": 5})
    assert [e.payload["i"] for e in await live] == [5]
    await sub.aclose()
    assert bus.subscriber_count("r1") == 0


async def test_no_gaps_or_duplicates_under_concurrent_publish() -> None:
    bus = EventBus(InMemoryEventStore())
    for i in range(50):
        await bus.publish("r1", EventType.TOOL_RESULT, payload={"i": i})

    async def publisher() -> None:
        for i in range(50, 200):
            await bus.publish("r1", EventType.TOOL_RESULT, payload={"i": i})
            if i % 7 == 0:
                await asyncio.sleep(0)

    sub = bus.subscribe("r1")
    pub = asyncio.create_task(publisher())
    events = await _take(sub, 200, limit_s=5)
    await pub
    await sub.aclose()
    assert [e.payload["i"] for e in events] == list(range(200))
    assert [e.id for e in events] == sorted({e.id for e in events})


async def test_lagged_subscriber_resyncs_from_store() -> None:
    bus = EventBus(InMemoryEventStore(), queue_size=3)
    sub = bus.subscribe("r1")
    first = asyncio.create_task(_take(sub, 1))
    await asyncio.sleep(0)
    for i in range(20):  # overflows the 3-slot queue while nobody reads
        await bus.publish("r1", EventType.TOOL_CALL, payload={"i": i})
    events = await first
    events += await _take(sub, 19)
    await sub.aclose()
    assert [e.payload["i"] for e in events] == list(range(20))


async def test_multiple_subscribers_each_get_everything() -> None:
    bus = EventBus(InMemoryEventStore())
    a, b = bus.subscribe("r1"), bus.subscribe("r1")
    ta = asyncio.create_task(_take(a, 3))
    tb = asyncio.create_task(_take(b, 3))
    await asyncio.sleep(0)
    for t in (EventType.NODE_STARTED, EventType.QUESTION, EventType.NODE_FINISHED):
        await bus.publish("r1", t)
    assert [e.type for e in await ta] == [e.type for e in await tb]
    await a.aclose()
    await b.aclose()
